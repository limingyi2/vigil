# -*- coding: utf-8 -*-
"""主对话上下文压缩器（P8.4）。

只管"主对话的即时上下文"：历史滑出内存窗口时把旧消息折叠进本会话的
滚动摘要，超预算时优先缩减较早的历史。不动 recap 的日/周/月摘要系统，
不重写、不删除任何原始聊天记录（原始流水永远在 chat_history*.jsonl 里）。

核心约定：
- 隔离键 = persona_key + src + conversation_id。三个人设/渠道/会话互不串。
- 原始记录永远保留：滑出的消息先进 pending（持久化），摘要成功才清；
  摘要失败时 pending 与旧摘要原样不动，水位线不推进。
- Token 决策一律用估算值（estimate_tokens，启发式、宁高勿低），
  摘要请求的真实 usage 单独记进 stats —— 两者不混用，字节数永远不当 token。
- 上下文容量：本地 Ollama 用真实 num_ctx；云端用配置里的
  context_tokens/input_tokens；都没有才退保守默认值并标注来源。
- 摘要是**直连 provider 的单次请求**（独立超时、每轮至多 1 次），
  绝不递归进 Brain.chat —— 那会拿聊天锁、写历史、还能再触发压缩，死循环。
"""
import hashlib
import json
import os
import time

DEFAULT_CAPACITY = 8192          # 容量完全未知时的保守默认
DEFAULT_RESERVE_OUT = 2048       # 给模型输出预留
MARGIN_TOKENS = 256              # 估算误差余量
MSG_OVERHEAD = 4                 # 每条消息的角色/结构开销
SUMMARY_PROMPT_LIMIT = 900       # 摘要正文长度上限（字符）


# --- 配置（config.json 的 context_compression 键）---

def _cfg(brain):
    try:
        c = (brain.api or {}).get("context_compression")
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def enabled(brain):
    return bool(_cfg(brain).get("enabled", True))


def summary_max_chars(cfg=None):
    if cfg:
        try:
            return max(200, int(cfg.get("summary_max_chars") or SUMMARY_PROMPT_LIMIT))
        except (TypeError, ValueError):
            pass
    return SUMMARY_PROMPT_LIMIT


# --- Token 估算（启发式，与真实 usage 严格分开）---

def estimate_tokens(text):
    """粗估 token：CJK 约 0.9 token/字、其它约 0.3 token/字符（约 3.3 字符/token）。

    取偏高档：提前压缩的代价远小于超预算被端点 400。真实用量来自模型
    返回的 usage（见 maybe_summarize），只进统计、不参与这里的决策。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 0.9 + other * 0.3) + 1


def estimate_messages(msgs):
    return sum(estimate_tokens(m.get("content") or "") + MSG_OVERHEAD
               for m in msgs if isinstance(m, dict))


# --- 容量与预留 ---

def capacity(brain):
    """(tokens, source)：本地 Ollama 用真实 num_ctx；云端用显式配置；
    都没有退保守默认并标 unknown_default —— 调用方据此知道该不该信。"""
    api0 = brain._api_snapshot()
    try:
        mp = brain._provider(api0)
        if getattr(mp, "kind", "") == "ollama":
            n = getattr(mp, "num_ctx", None)
            if isinstance(n, int) and n > 0:
                return n, "ollama_num_ctx"
    except Exception:
        pass
    try:
        from model_providers.execution import capabilities
        cap = capabilities(api0)
        for k in ("context_tokens", "input_tokens"):
            v = cap.get(k)
            if isinstance(v, int) and 0 < v <= 10000000:
                return v, "configured"
    except Exception:
        pass
    try:
        d = int(_cfg(brain).get("default_capacity") or DEFAULT_CAPACITY)
        if d > 0:
            return d, "unknown_default"
    except (TypeError, ValueError):
        pass
    return DEFAULT_CAPACITY, "unknown_default"


def _reserve_out(brain):
    """给模型输出留的预算：本地用 num_predict；云端用配置的 max_output_tokens。"""
    api0 = brain._api_snapshot()
    try:
        mp = brain._provider(api0)
        if getattr(mp, "kind", "") == "ollama":
            return int(getattr(mp, "num_predict", 0) or DEFAULT_RESERVE_OUT)
    except Exception:
        pass
    try:
        from model_providers.execution import capabilities
        v = capabilities(api0).get("max_output_tokens")
        if isinstance(v, int) and 0 < v <= 100000:
            return v
    except Exception:
        pass
    return DEFAULT_RESERVE_OUT


# --- 隔离键与持久化 ---

def _cid_str(conversation_id):
    return str(conversation_id) if conversation_id else "legacy"


def _key_from(persona_key, src, conversation_id):
    return (str(persona_key or "default"), str(src or "wechat"),
            _cid_str(conversation_id))


def _key(brain, src, conversation_id):
    try:
        pk = brain._persona_key_for(src)
    except Exception:
        pk = "default"
    return _key_from(pk, src, conversation_id)


def _state_path(key):
    from paths import DATA_DIR
    h = hashlib.sha1("\x00".join(key).encode("utf-8")).hexdigest()
    return os.path.join(DATA_DIR, "context_compactions", h + ".json")


def load_state(key):
    p = _state_path(key)
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            st.setdefault("summary", "")
            st.setdefault("pending", [])
            st.setdefault("seq", 0)
            st.setdefault("ranges", [])
            st.setdefault("stats", {})
            return st
    except FileNotFoundError:
        pass
    except Exception as e:
        # 坏文件不删不覆盖：留着给人查，本次按空状态走（原始记录都在存档里）
        print("[上下文] 压缩状态文件损坏，按空状态继续：%s（%s）"
              % (p, str(e)[:80]), flush=True)
    return {"summary": "", "pending": [], "seq": 0, "ranges": [], "stats": {}}


def save_state(key, state):
    """原子落盘：同目录临时文件 + os.replace。写失败时旧文件原样保留。"""
    p = _state_path(key)
    tmp = p + ".tmp"
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print("[上下文] 压缩状态写盘失败（旧状态保留）：%s" % str(e)[:100], flush=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def stats_for(persona_key, src, conversation_id):
    st = load_state(_key_from(persona_key, src, conversation_id))
    out = dict(st.get("stats") or {})
    out["summary"] = st.get("summary") or ""
    out["ranges"] = st.get("ranges") or []
    return out


def pending_count(persona_key, src, conversation_id):
    return len(load_state(_key_from(persona_key, src, conversation_id))
               .get("pending") or [])


def clear_scope(persona_key, src, conversation_id):
    """会话被清空时同步删压缩状态（内存摘要与持久化摘要一起没）。"""
    try:
        os.remove(_state_path(_key_from(persona_key, src, conversation_id)))
    except OSError:
        pass


# --- 滑出窗口的消息进 pending ---

def offer_dropped(brain, src, conversation_id, msgs):
    """hist 裁剪时滑出的消息存进本会话的 pending，等下次压缩折叠进摘要。

    这不是原始记录的唯一副本 —— chat_history*.jsonl 里永远有全量；
    pending 只是"还没进摘要"的暂存区，所以超限时丢最旧是安全的。
    """
    if not enabled(brain):
        return
    msgs = [m for m in (msgs or [])
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and (m.get("content") or "").strip()]
    if not msgs:
        return
    try:
        key = _key(brain, src, conversation_id)
        state = load_state(key)
        seq = int(state.get("seq") or 0)
        pend = list(state.get("pending") or [])
        for m in msgs:
            seq += 1
            pend.append({"seq": seq, "role": m["role"], "content": m["content"]})
        cap_n = int(_cfg(brain).get("pending_max_messages") or 500)
        if len(pend) > cap_n:
            pend = pend[len(pend) - cap_n:]
        state["pending"] = pend
        state["seq"] = seq
        st = state.setdefault("stats", {})
        st["dropped_total"] = int(st.get("dropped_total") or 0) + len(msgs)
        save_state(key, state)
    except Exception as e:
        print("[上下文] 滑出消息入 pending 失败（不影响聊天）：%s" % str(e)[:100],
              flush=True)


# --- 摘要 ---

_SUMMARY_SYSTEM = (
    "你是对话摘要器。把【已有摘要】和【新增对话】合并成一份延续性摘要，"
    "供助手在后续对话中无缝承接上下文。硬性要求：\n"
    "1) 保留全部精确数值、日期时间、版本号、Git SHA、文件路径、专有名词，一字不改；\n"
    "2) 保留用户已确认的决定、项目当前状态、尚未完成的任务与下一步；\n"
    "3) 不评论、不补充、不编造；两边冲突时以【新增对话】为准；\n"
    "4) 总长度不超过 {limit} 字，直接输出摘要正文，不要任何前后缀或解释。")


def _clip(text, limit):
    """模型超长时的保险截断：优先在段落边界切，不在数字中间留半截。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit * 0.6:
        cut = cut[:nl]
    return cut.rstrip()


def maybe_summarize(brain, cfg, *, key, state, info):
    """把 pending 折叠进滚动摘要。单次 provider 请求，成败都推进统计。

    成功：摘要更新、pending 清空、记录覆盖范围（ranges，按消息序号）；
    失败：旧摘要与 pending 一字不动，水位线不推进，info 里留失败原因。
    """
    stats = state.setdefault("stats", {})
    info["summarize_requests"] = int(stats.get("requests") or 0)
    if not state.get("pending"):
        return state
    api0 = brain._api_snapshot()
    try:
        mp = brain._provider(api0)
    except Exception as e:
        stats["failures"] = int(stats.get("failures") or 0) + 1
        stats["last_error"] = "provider: %s" % str(e)[:150]
        save_state(key, state)
        info["summarize_failed"] = stats["last_error"]
        return state
    if not brain._provider_ready(mp, api0):
        stats["failures"] = int(stats.get("failures") or 0) + 1
        stats["last_error"] = "provider_not_ready"
        save_state(key, state)
        info["summarize_failed"] = "provider_not_ready"
        return state

    limit = summary_max_chars(cfg)
    timeout = None
    try:
        timeout = float(cfg.get("summarize_timeout") or 45)
    except (TypeError, ValueError):
        timeout = 45.0
    old = state.get("summary") or ""
    transcript = "\n".join("%s: %s" % (p.get("role"), p.get("content"))
                           for p in state["pending"])
    payload = {
        "model": api0.get("model"),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _SUMMARY_SYSTEM.format(limit=limit)},
            {"role": "user",
             "content": "【已有摘要】\n" + (old or "（无）")
                        + "\n\n【新增对话】\n" + transcript},
        ],
    }
    try:
        from model_providers.execution import prepare, normalize, ERROR_MESSAGES
        j = mp.chat(prepare(payload, api0), timeout=timeout)
        normalized = normalize(j)
        if not normalized["complete"]:
            raise RuntimeError(ERROR_MESSAGES.get(normalized["reason"],
                                                  normalized["reason"]))
        text = _clip(normalized.get("content") or "", limit)
        if not text:
            raise RuntimeError("empty_summary")
        usage = normalized.get("usage") or {}
        stats["requests"] = int(stats.get("requests") or 0) + 1
        # 真实 token 来自模型 usage；估算值只用于上面的预算决策，两者不混
        stats["tokens_in"] = int(stats.get("tokens_in") or 0) \
            + int(usage.get("prompt_tokens") or 0)
        stats["tokens_out"] = int(stats.get("tokens_out") or 0) \
            + int(usage.get("completion_tokens") or 0)
        seqs = [int(p.get("seq") or 0) for p in state["pending"]]
        state.setdefault("ranges", []).append(
            {"from": min(seqs), "to": max(seqs),
             "t": time.strftime("%Y-%m-%d %H:%M")})
        state["summary"] = text
        state["pending"] = []
        state["updated_at"] = time.time()
        save_state(key, state)
        info["summarize_requests"] = stats["requests"]
    except Exception as e:
        stats["failures"] = int(stats.get("failures") or 0) + 1
        stats["last_error"] = str(e)[:200]
        save_state(key, state)          # 只推进统计，摘要与 pending 不动
        info["summarize_failed"] = str(e)[:120]
        info["summarize_requests"] = int(stats.get("requests") or 0)
    return state


# --- 上下文构建（三入口共用）---

def _summary_msg(summary, state):
    ranges = state.get("ranges") or []
    span = ""
    if ranges:
        span = "（已覆盖第 %d~%d 条消息）" % (ranges[0].get("from", 0),
                                             ranges[-1].get("to", 0))
    return {"role": "system",
            "content": "（以下是本会话更早对话的滚动摘要%s，"
                       "其中的数值、日期、路径、版本号均可直接引用）\n%s"
                       % (span, summary)}


def build_context(brain, user_text, *, system_text="", tool_specs=None,
                  src=None, conversation_id=None, allow_summarize=True):
    """构造模型请求的历史段。返回 (history_msgs, info)。

    - 短对话（没滑出、没超预算）：原样返回 recent_hist，零额外请求；
    - 摘要已存在且放得下：注入摘要（即使当前没超预算 —— 老事实不该因为
      "窗口够用"就丢）；
    - 滑出累计超过阈值、或当前超预算：把 pending 折叠进摘要（最多一次请求）；
    - 仍超预算：从最旧开始裁历史，最近消息与当前用户问题绝不裁。
    """
    if not enabled(brain):
        return list(brain.recent_hist()), {"disabled": True, "compacted": False}

    cfg = _cfg(brain)
    margin = MARGIN_TOKENS
    cap, cap_src = capacity(brain)
    reserve_out = _reserve_out(brain)
    est_sys = estimate_tokens(system_text or "")
    est_tools = estimate_tokens(json.dumps(tool_specs, ensure_ascii=False)) \
        if tool_specs else 0
    est_user = estimate_tokens(user_text or "")
    budget = cap - reserve_out - est_sys - est_tools - margin
    if budget <= 0:
        budget = max(64, cap // 4)
    info = {"capacity": cap, "capacity_source": cap_src,
            "reserve_out": reserve_out, "est_system": est_sys,
            "est_tools": est_tools, "est_user": est_user, "compacted": False}

    # 单条用户消息超长：不截断（任务指令一个字不能丢），明确报告
    if est_user > budget:
        info["user_overflow"] = True
        print("[上下文] 单条消息约 %d tokens 超过可用预算 %d（不截断，照发）"
              % (est_user, budget), flush=True)

    try:
        keep = int(cfg.get("recent_keep") or getattr(brain, "CTX_TURNS", 12))
    except (TypeError, ValueError):
        keep = 12
    hist = list(brain.recent_hist(keep))
    est_hist = estimate_messages(hist)

    key = _key(brain, src, conversation_id)
    state = load_state(key)
    summary = state.get("summary") or ""
    est_summary = estimate_tokens(summary)

    def fits(n_hist_tokens, with_summary=True):
        total = est_user + n_hist_tokens + margin
        if with_summary:
            total += est_summary
        return total <= budget

    after = int(cfg.get("summarize_after_dropped") or 12)
    over = not (fits(est_hist) or (not summary and fits(est_hist, False)))
    # 触发折叠的两个条件：
    # ① 超预算（硬性：§5，超了就必须缩）；
    # ② 滑出累计够一批（§3"逐步把旧消息合并进滚动摘要"—— 当前窗口放得下
    #    也不等于旧事实还在：滑出的消息只存在于 pending 里，不折叠就永远
    #    进不了上下文。短对话没有滑出，永远不会走到这里）。
    if allow_summarize and not info.get("user_overflow") and state.get("pending") \
            and (over or len(state["pending"]) >= after):
        state = maybe_summarize(brain, cfg, key=key, state=state, info=info)
        summary = state.get("summary") or ""
        est_summary = estimate_tokens(summary)

    # 未超预算：原样返回；已有摘要且放得下就一并注入
    if fits(est_hist):
        msgs = []
        if summary:
            msgs.append(_summary_msg(summary, state))
            info["compacted"] = True
        msgs.extend(hist)
        info["trimmed_to"] = len(hist)
        info["summarize_requests"] = int((state.get("stats") or {})
                                         .get("requests") or 0)
        return msgs, info

    # 超预算：pending 折叠已在上面做过；这里从最旧开始裁历史，
    # 直到放得下。最近对话保底（宁挤预算不留空上下文），当前用户
    # 问题与系统规则从不裁。
    min_keep = min(int(cfg.get("min_keep") or 4), len(hist))
    keep_n = len(hist)
    while keep_n > 0 and not fits(estimate_messages(hist[keep_n:])):
        keep_n -= 1
    if keep_n < min_keep:
        keep_n = min_keep
    trimmed = hist[keep_n:]

    info["compacted"] = bool(summary) or keep_n < len(hist)
    info["trimmed_from"] = len(hist)
    info["trimmed_to"] = len(trimmed)
    info["summarize_requests"] = int((state.get("stats") or {})
                                     .get("requests") or 0)
    msgs = []
    if summary:
        msgs.append(_summary_msg(summary, state))
    msgs.extend(trimmed)
    return msgs, info
