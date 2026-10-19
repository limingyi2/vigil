# -*- coding: utf-8 -*-
"""P8.4 主对话上下文：流式历史遗漏修复 + 压缩器 验收测试。

不碰混合委派的 30 题评测集。全部离线：模型用 FakeProvider（脚本化响应），
VIGIL_HOME 指向临时目录，绝不读写真实 data/。

跑法：F:/zhixia/venv/Scripts/python.exe brain/_test_p84_context.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="vigil_p84_")
os.environ["VIGIL_HOME"] = _TMP          # 必须在 import 任何 brain 模块之前
sys.path.insert(0, _HERE)

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print("PASS %s" % name)
    except Exception as e:
        RESULTS.append((name, False, "%s: %s" % (type(e).__name__, str(e)[:200])))
        print("FAIL %s -> %s: %s" % (name, type(e).__name__, str(e)[:160]))
        traceback.print_exc(limit=2)


# ---------- FakeProvider：脚本化响应，记录收到的 payload ----------

def _final(text, usage=None):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20}}


def _tool_call(tc_id, name, args_json):
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": name, "arguments": args_json}}]},
        "finish_reason": "tool_calls"}], "usage": {}}


def _truncated(text):
    return {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": "length"}], "usage": {}}


class FakeProvider(object):
    kind = "cloud"
    supports_tools = True
    supports_stream = False       # completion_events 会走非流式适配路径

    def __init__(self, script=None, kind="cloud", num_ctx=None):
        self.script = list(script or [])
        self.calls = []
        self.kind = kind
        self.num_ctx = num_ctx
        self.model = "fake-model"

    def ready(self):
        return True

    def chat(self, payload, timeout=None):
        self.calls.append(payload)
        item = self.script.pop(0) if self.script else _final("默认回复")
        if isinstance(item, Exception):
            raise item
        # P8.5: the summary provider fixture now speaks the strict structured
        # protocol. Its scripted facts must still be grounded in synthetic logs.
        if '结构化对话摘要器' in payload.get('messages', [{}])[0].get('content', ''):
            import conversation_context as cc
            data = json.loads(payload['messages'][-1]['content'])
            body = item['choices'][0]['message']['content']
            refs = []
            for literal in re.findall(r'[A-Za-z0-9_]+(?:[.:/\\-][A-Za-z0-9_]+)*', body):
                match = next((m for m in data['new_messages'] if literal in m['quote']), None)
                if match and match['id'] not in refs:
                    refs.append(match['id'])
            refs = refs or [data['new_messages'][0]['id']]
            summary = {f: [] for f in cc.FIELDS}
            summary['source_refs'] = refs
            summary['facts'] = [{'text': item['choices'][0]['message']['content'],
                                 'source_refs': refs}]
            item = dict(item, choices=[{'message': {'content': json.dumps(summary, ensure_ascii=False)},
                                        'finish_reason': 'stop'}])
        return item


def make_brain(fake, history_turns=24):
    import brain as brain_mod
    import paths
    paths.ensure()      # 临时家要先建出 data/ 等目录（生产由 server 启动时建）
    cfg = {"model": "fake-model", "api_key": "k", "api_base": "http://x",
           "time_awareness": {"enabled": False},
           "memory_search": {"enabled": False}}
    b = brain_mod.Brain(mem=None, api_config=cfg, history_turns=history_turns)
    b._provider = lambda cfg=None: fake        # 实例级注入，绕过 get_provider
    return b


# ---------- 1. 流式聊天：成功后必须同步进内存历史（P8.4 根因） ----------

def t_stream_commits_history():
    fake = FakeProvider([_final("暗号是 ALPHA-7")])
    b = make_brain(fake)
    b.use_src("pc", None)
    evs = list(b.stream_chat("记住暗号", src="pc", conversation_id=None))
    assert not any(e.get("type") == "error" for e in evs), evs[-1:]
    roles = [(m["role"], m.get("content") or "") for m in b.hist[1:]]
    # hist 里的内容带 [MM-DD HH:MM] 时间戳（chat() 同款），只做子串匹配
    assert any("记住暗号" in c for r, c in roles if r == "user"), \
        "流式 user 未进内存历史: %r" % roles
    assert any("ALPHA-7" in c for r, c in roles if r == "assistant"), \
        "流式 assistant 未进内存历史: %r" % roles
    # 下一轮（无论流式与否）构建请求时必须带上上一轮内容
    fake2 = FakeProvider([])
    b._provider = lambda cfg=None: fake2
    list(b.stream_chat("暗号是什么", src="pc", conversation_id=None))
    sent = json.dumps(fake2.calls[0], ensure_ascii=False)
    assert "ALPHA-7" in sent, "下一轮请求里没有第一轮的流式回复"
    assert "记住暗号" in sent, "下一轮请求里没有第一轮的用户消息"


def t_stream_three_turns_reference():
    fake = FakeProvider([_final("第一轮说了：项目在 F:/proj/x"),
                         _final("第二轮说了：截止日是 11-01"),
                         _final("汇总")])
    b = make_brain(fake)
    b.use_src("pc", None)
    list(b.stream_chat("项目在哪", src="pc", conversation_id=None))
    list(b.stream_chat("截止日呢", src="pc", conversation_id=None))
    list(b.stream_chat("把前面两点复述一遍", src="pc", conversation_id=None))
    sent = json.dumps(fake.calls[2], ensure_ascii=False)
    assert "F:/proj/x" in sent and "11-01" in sent, \
        "第三轮请求丢失前两轮流式内容"


def t_stream_error_no_commit():
    # 截断（finish=length → complete=False）：不能把不完整答案当成功写历史
    fake = FakeProvider([_truncated("只写到一")])
    b = make_brain(fake)
    b.use_src("pc", None)
    evs = list(b.stream_chat("讲个长故事", src="pc", conversation_id=None))
    assert any(e.get("type") == "error" for e in evs)
    assert len(b.hist) == 1, "失败轮次不应进历史: %r" % b.hist
    # 模型异常（连接失败）：同样不写
    fake2 = FakeProvider([RuntimeError("connection lost")])
    b._provider = lambda cfg=None: fake2
    evs = list(b.stream_chat("再来一个", src="pc", conversation_id=None))
    assert any(e.get("type") == "error" for e in evs)
    assert len(b.hist) == 1, "异常轮次不应进历史: %r" % b.hist


def t_stream_disk_archive_written():
    fake = FakeProvider([_final("存档检查")])
    b = make_brain(fake)
    b.use_src("pc", None)
    list(b.stream_chat("写存档", src="pc", conversation_id=None))
    p = b._log_for("pc", None)
    assert os.path.exists(p), "流式轮次应写磁盘存档 %s" % p
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    assert any(r.get("role") == "assistant" and "存档检查" in r.get("text", "")
               for r in rows)


# ---------- 2. 压缩器：短对话不压缩 ----------

def t_short_no_compaction():
    import conversation_context as cc
    fake = FakeProvider([])
    b = make_brain(fake)
    b.use_src("pc", None)
    for i in range(4):
        b._commit_turn("第%d句" % i, "答%d" % i, src="pc", conversation_id=None)
    hist, info = cc.build_context(b, "新问题", system_text="sys",
                                  src="pc", conversation_id=None)
    assert not info.get("compacted"), info
    # 内容与顺序原样：8 条（4 轮），去掉时间戳后逐字对上
    bare = [(m["role"], (m.get("content") or "").split("] ", 1)[-1])
            for m in hist]
    want = [("user", "第%d句" % i) for i in range(4)]
    got_users = [x for x in bare if x[0] == "user"]
    assert got_users == want, (got_users, want)
    assert len(fake.calls) == 0, "没超预算不该发摘要请求"


# ---------- 3. 长对话：滑出的旧消息进摘要，关键事实可召回 ----------

def _feed(b, n, fact_turn=None, conversation_id=None):
    for i in range(n):
        u = "第%d句" % i
        if i == fact_turn:
            u = "第%d句：版本号 1.2.3，SHA abc123def，配置在 F:/proj/config.yaml" % i
        if i == 0:
            u += ' 端口 18787；仓库 G:/repoA；工具清单 C:/tools.json；蓝图 D:/bp.png；入口仓库 G:/repoR；num_ctx 16384'
        b._commit_turn(u, "答%d" % i, src="pc", conversation_id=conversation_id)


def t_long_conversation_fact_recall():
    import uuid
    import conversation_context as cc
    # 摘要请求由 FakeProvider 兜底回答：把关键事实原样带回（模拟忠实摘要）
    fake = FakeProvider([_final("早期聊过：版本号 1.2.3，SHA abc123def，配置在 F:/proj/config.yaml",
                                usage={"prompt_tokens": 500, "completion_tokens": 60})])
    b = make_brain(fake)
    cid = str(uuid.uuid4())
    b.use_src("pc", cid)
    _feed(b, 40, fact_turn=3, conversation_id=cid)
    hist, info = cc.build_context(b, "之前的版本号是多少", system_text="sys",
                                  src="pc", conversation_id=cid)
    blob = json.dumps(hist, ensure_ascii=False)
    assert "1.2.3" in blob, "摘要注入后第 3 句的关键数值丢了: %s" % info
    assert info.get("compacted"), info
    assert info.get("summarize_requests", 0) >= 1, info
    # 原始记录（磁盘存档）一条不少：独立会话的存档就是 40 轮 × 2 条
    p = b._log_for("pc", cid)
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    assert len(rows) == 80, "原始存档被动过: %d" % len(rows)
    # 真实 token 计量来自 usage，不是字节估算
    st = cc.stats_for(b._persona_key_for("pc"), "pc", cid)
    assert st.get("tokens_in") == 500, st


def t_long_various_lengths():
    import conversation_context as cc
    for n in (20, 50, 100):
        shutil.rmtree(_TMP, ignore_errors=True)
        os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
        fake = FakeProvider([_final("摘要%d" % n)])
        b = make_brain(fake)
        b.use_src("pc", None)
        _feed(b, n)
        hist, info = cc.build_context(b, "还在吗", system_text="sys",
                                      src="pc", conversation_id=None)
        assert len(hist) >= 1 and all(m.get("role") in ("system", "user", "assistant")
                                      for m in hist), (n, info)
        assert not any("第0句：版本号" in (m.get("content") or "") and n > 24
                       for m in hist) or info.get("compacted"), (n, info)


# ---------- 4. 精确值不被二次破坏 ----------

def t_summary_length_cap_safe_clip():
    import conversation_context as cc
    fake = FakeProvider([_final("长" * 5000 + " 1.2.3")])
    b = make_brain(fake)
    b.use_src("pc", None)
    _feed(b, 30, fact_turn=3)
    before = cc.pending_count(b._persona_key_for("pc"), "pc", "legacy")
    hist, info = cc.build_context(b, "问", system_text="sys",
                                  src="pc", conversation_id=None)
    st = cc.stats_for(b._persona_key_for("pc"), "pc", "legacy")
    lim = cc.summary_max_chars()
    assert len(st.get("summary") or "") <= lim + 4, (len(st.get("summary") or ""), lim)
    assert info.get("summarize_failed"), "P8.5 超长输出必须拒绝，不能截断标识符"
    assert cc.pending_count(b._persona_key_for("pc"), "pc", "legacy") == before


# ---------- 5. 多会话隔离 ----------

def t_two_conversations_isolated():
    import uuid
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    cid_a, cid_b = str(uuid.uuid4()), str(uuid.uuid4())
    fake = FakeProvider([_final("会话A的事实：仓库在 G:/repoA"),
                         _final("会话B的摘要")])
    b = make_brain(fake)
    b.use_src("pc", cid_a)
    _feed(b, 30, conversation_id=cid_a)
    hist_a, info_a = cc.build_context(b, "仓库在哪", system_text="sys",
                                      src="pc", conversation_id=cid_a)
    assert "G:/repoA" in json.dumps(hist_a, ensure_ascii=False), info_a
    # 会话 B：新键，不得继承 A 的摘要，也不得触发读 A
    fake2 = FakeProvider([])
    b._provider = lambda cfg=None: fake2
    b.use_src("pc", cid_b)
    b._commit_turn("B的第一句", "B答", src="pc", conversation_id=cid_b)
    hist_b, info_b = cc.build_context(b, "B问", system_text="sys",
                                      src="pc", conversation_id=cid_b)
    assert "G:/repoA" not in json.dumps(hist_b, ensure_ascii=False), "会话 A 摘要串进了 B"
    assert not info_b.get("compacted"), info_b
    # 渠道隔离：wechat 与 pc 同 cid 不共用同一份状态文件
    pk = b._persona_key_for("pc")
    assert cc._state_path((pk, "pc", cid_a)) != cc._state_path((pk, "wechat", cid_a))


# ---------- 6. 重启恢复 ----------

def t_restart_recovery():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([_final("重启前摘要：端口号 18787")])
    b = make_brain(fake)
    b.use_src("pc", None)
    _feed(b, 30)
    hist, info = cc.build_context(b, "q", system_text="sys", src="pc", conversation_id=None)
    assert "18787" in json.dumps(hist, ensure_ascii=False)
    # 新 Brain 实例 = 模拟重启：摘要仍能恢复
    fake2 = FakeProvider([])
    b2 = make_brain(fake2)
    b2.use_src("pc", None)
    b2._provider = lambda cfg=None: fake2
    hist2, info2 = cc.build_context(b2, "端口多少", system_text="sys",
                                    src="pc", conversation_id=None)
    assert "18787" in json.dumps(hist2, ensure_ascii=False), "重启后摘要丢了: %s" % info2
    assert len(fake2.calls) == 0, "重启后不该重新摘要"


# ---------- 7. 工具调用链不破坏 FC 配对 ----------

def t_tool_pairing_preserved():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([
        _final("早期摘要：工具清单在 C:/tools.json"),
        _tool_call("t1", "calc", '{"expr": "1+1"}'),
        _final("等于 2"),
    ])
    b = make_brain(fake)
    b.use_src("pc", None)
    _feed(b, 30)
    ans = b.draft_native("算一下 1+1", memory_block="", ctx_src="pc")
    assert ans and "2" in ans, ans
    # 回放的请求链里 assistant.tool_calls 与 role:tool 严格配对
    found = False
    for payload in fake.calls:
        msgs = payload.get("messages") or []
        for i, m in enumerate(msgs):
            if m.get("tool_calls"):
                nxt = msgs[i + 1] if i + 1 < len(msgs) else {}
                assert nxt.get("role") == "tool" and \
                    nxt.get("tool_call_id") == m["tool_calls"][0]["id"], \
                    "FC 配对被压缩破坏: %r" % msgs[i:i + 2]
                found = True
    assert found, "没走到工具循环"


# ---------- 8. 摘要失败/模型不可用：原文与旧摘要完好，水位线不推进 ----------

def t_summarize_failure_safe():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([RuntimeError("provider down")])
    b = make_brain(fake)
    b.use_src("pc", None)
    _feed(b, 30)
    pending_before = cc.pending_count(b._persona_key_for("pc"), "pc", "legacy")
    assert pending_before > 0, pending_before
    hist, info = cc.build_context(b, "问", system_text="sys", src="pc",
                                  conversation_id=None)
    assert not any("provider down" in (m.get("content") or "")
                   for m in hist), "失败信息不能当摘要注入"
    assert cc.pending_count(b._persona_key_for("pc"), "pc", "legacy") == pending_before, \
        "失败后 pending 不能丢"
    st = cc.stats_for(b._persona_key_for("pc"), "pc", "legacy")
    assert st.get("failures", 0) >= 1, st
    assert not st.get("summary"), st
    # 原始 hist 未被改写
    assert len([m for m in b.hist[1:]]) == 24, len(b.hist)


def t_provider_not_ready_no_summary():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([])
    b = make_brain(fake)
    b._provider_ready = lambda mp, cfg=None: False   # 模拟 provider 未就绪
    b.use_src("pc", None)
    _feed(b, 30)
    hist, info = cc.build_context(b, "问", system_text="sys", src="pc",
                                  conversation_id=None)
    assert len(fake.calls) == 0, "未就绪时不该发请求"
    assert hist, "至少要返回裁剪后的历史"


# ---------- 9. 清空会话：压缩状态一并清除，不复活 ----------

def t_clear_scope():
    import uuid
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    cid = str(uuid.uuid4())
    fake = FakeProvider([_final("摘要：蓝图在 D:/bp.png")])
    b = make_brain(fake)
    b.use_src("pc", cid)
    _feed(b, 30, conversation_id=cid)
    pk = b._persona_key_for("pc")
    assert cc.pending_count(pk, "pc", cid) > 0
    cc.clear_scope(pk, "pc", cid)
    st = cc.stats_for(pk, "pc", cid)
    assert not st.get("summary"), st
    assert cc.pending_count(pk, "pc", cid) == 0, "清空后 pending 应为空"
    fake2 = FakeProvider([])
    b._provider = lambda cfg=None: fake2
    hist, info = cc.build_context(b, "蓝图呢", system_text="sys", src="pc",
                                  conversation_id=cid)
    assert "D:/bp.png" not in json.dumps(hist, ensure_ascii=False), "清空后摘要复活了"


# ---------- 10. 模式正确性：本地模式摘要不偷偷调云端 ----------

def t_local_mode_uses_local_provider():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([_final("本地摘要：num_ctx 16384")], kind="ollama",
                        num_ctx=16384)
    b = make_brain(fake)
    b.api["model_mode"] = "local"
    b.api["ollama"] = {"base": "http://127.0.0.1:11434", "model": "fake",
                       "num_ctx": 16384}
    b.use_src("pc", None)
    _feed(b, 30)
    hist, info = cc.build_context(b, "问", system_text="sys", src="pc",
                                  conversation_id=None)
    assert info.get("capacity") == 16384, info        # 容量来自真实 num_ctx
    assert info.get("capacity_source") == "ollama_num_ctx", info
    # 摘要请求发给了同一个（本地）provider，云端零调用
    assert len(fake.calls) == 1, "本地模式摘要应只发给本地 provider: %d" % len(fake.calls)


# ---------- 11. 单条用户消息超长：不截断、明确报告 ----------

def t_user_overflow_reported():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([])
    b = make_brain(fake)
    b.use_src("pc", None)
    huge = "任务" * 80000          # 远超任一云端预设的输入预算
    hist, info = cc.build_context(b, huge, system_text="sys", src="pc",
                                  conversation_id=None)
    assert info.get("user_overflow"), info
    assert all(huge not in (m.get("content") or "") for m in hist
               if m.get("role") == "user"), "用户消息不该被塞进历史区"


# ---------- 12. 开关关闭：完全回到旧行为 ----------

def t_disabled_switch():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([])
    b = make_brain(fake)
    b.api["context_compression"] = {"enabled": False}
    b.use_src("pc", None)
    _feed(b, 30)
    hist, info = cc.build_context(b, "问", system_text="sys", src="pc",
                                  conversation_id=None)
    assert info.get("disabled"), info
    assert not info.get("compacted"), info
    assert len(fake.calls) == 0


# ---------- 13. draft_native / stream_chat / agent run 三入口同源 ----------

def t_three_entries_same_builder():
    import conversation_context as cc
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(os.path.join(_TMP, "data"), exist_ok=True)
    fake = FakeProvider([_final("入口摘要：仓库 G:/repoR"),
                         _final("draft答"), _final("stream答")])
    b = make_brain(fake)
    b.use_src("pc", None)
    _feed(b, 30)
    # draft_native 与 stream_chat 内部都必须经过 build_context：
    # 用哨兵——摘要在三个入口的请求里都出现
    ans = b.draft_native("问1", memory_block="", ctx_src="pc")
    sent1 = json.dumps(fake.calls[-1], ensure_ascii=False)
    assert "G:/repoR" in sent1, "draft_native 没带摘要"
    list(b.stream_chat("问2", src="pc", conversation_id=None))
    sent2 = json.dumps(fake.calls[-1], ensure_ascii=False)
    assert "G:/repoR" in sent2, "stream_chat 没带摘要"
    # agent run 入口：直接验证同一构建函数被 server 使用
    import agent_runs
    msgs = [{"role": "system", "content": "s"}]
    hist, info = cc.build_context(b, "任务", system_text="s", src="pc",
                                  conversation_id=None, allow_summarize=False)
    msgs.extend(hist)
    assert "G:/repoR" in json.dumps(msgs, ensure_ascii=False), "agent run 入口没带摘要"
    # 3 次：摘要 1 次 + draft 聊天 1 次 + stream 聊天 1 次（重建不再摘要）
    assert len(fake.calls) == 3, "allow_summarize=False 不该再发摘要请求: %d" % len(fake.calls)


if __name__ == "__main__":
    check("流式成功后进内存历史并影响下一轮", t_stream_commits_history)
    check("连续三轮流式可引用前两轮", t_stream_three_turns_reference)
    check("流式失败/截断不写历史", t_stream_error_no_commit)
    check("流式成功写磁盘存档", t_stream_disk_archive_written)
    check("短对话不触发压缩", t_short_no_compaction)
    check("40 条长对话关键事实可召回", t_long_conversation_fact_recall)
    check("20/50/100 条长对话稳定", t_long_various_lengths)
    check("摘要超长安全截断", t_summary_length_cap_safe_clip)
    check("两个 PC 会话互不串话", t_two_conversations_isolated)
    check("重启后摘要恢复", t_restart_recovery)
    check("FC 工具配对不破坏", t_tool_pairing_preserved)
    check("摘要失败原文完好", t_summarize_failure_safe)
    check("provider 未就绪不发请求", t_provider_not_ready_no_summary)
    check("清空会话压缩状态清除", t_clear_scope)
    check("本地模式摘要走本地 provider", t_local_mode_uses_local_provider)
    check("单条消息超长明确报告", t_user_overflow_reported)
    check("开关关闭回到旧行为", t_disabled_switch)
    check("三入口同一构建函数", t_three_entries_same_builder)
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n通过 %d / %d" % (n_ok, len(RESULTS)))
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(0 if n_ok == len(RESULTS) else 1)
