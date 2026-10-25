# -*- coding: utf-8 -*-
"""Plan-and-Execute：复杂任务先列步骤，再按步骤执行工具。

ReAct 是一步看一步的，复杂任务容易漏：「算一下这个再搜一下那个」算完第一个
就忘了第二个。这里先让便宜的 flash 模型列步骤，逐步执行，最后把结果一次性
喂给主模型组织回答。

只在 should_plan() 判为复杂任务时走；闲聊直接走原来的 ReAct，不花 token。

步骤是模型输出的 JSON 数组，tool 为 null 的那步是"不用工具，组织语言"的收尾：
    [{"step": "搜一下上证指数", "tool": "search", "arg": "上证指数"},
     {"step": "整理回答", "tool": null, "arg": null}]

任何一步出问题（调不通、解析不出、没配 key）都返回 None，由调用方回退 ReAct。
"""
import json
import re
import threading

NAME = "planner"
DESC = "Plan-and-Execute：复杂任务先规划再执行"

# 两张表都只放多字词。反例：
#   跳过词里的「早」砍掉「帮我查一下早上的会议」，「啊」砍掉「帮我总结一下啊」
#   规划词里的「三个」拖「我运行了三个小时」进规划，「再」拖「她再也不会来了」
# 单字命中率太高，一个字就能左右判断。漏判只是走回ReAct，不会出错。
_PLAN_KEYWORDS = (
    "帮我写", "帮我查", "帮我搜", "帮我看", "帮我找", "帮我整理",
    "帮我总结", "帮我分析", "帮我对比", "帮我比较", "帮我安排", "帮我规划",
    "总结一下", "整理一下", "分析一下", "对比一下", "比较一下",
    "总结", "整理", "分析", "对比", "比较", "归纳",
    "查一下", "搜一下", "找一下", "看一下",
    "写一份", "写个", "做一份", "做个",
    "报告", "方案", "计划书", "步骤", "攻略", "教程",
    "怎么做", "如何", "怎么样才",
    "分别", "逐一", "每个都", "全部都", "所有的",
)

_SKIP_KEYWORDS = (
    "在吗", "在么", "在不在",
    "晚安", "早安", "晚上了", "早上好", "早安啦",
    "吃了吗", "吃过了", "睡了", "睡了吗", "在干嘛", "在忙吗",
    "你好", "谢谢", "谢啦", "对不起", "抱歉", "不好意思",
    "哈哈哈", "嘿嘿", "呵呵", "嘻嘻", "嗯嗯", "嗯哼",
)

# 长消息只有在带这些"并列/递进"痕迹时才当成多目标任务（见 should_plan）。
# 单纯的长几乎都是情绪或叙述，不是任务。
_MULTI_TASK_HINTS = (
    "再", "然后", "顺便", "以及", "还要", "另外", "分别", "逐一", "都", "各",
)


def plan_decision(text):
    """返回 `(要不要规划, 为什么)`。`should_plan` 是它的薄封装。

    ## 为什么要把"为什么"也返回

    规划分支现在还在观察期（见文件末的 `note`）：将来判它"删还是留"，
    光知道"触发了多少次"不够，还得知道**它为什么触发** ——
    是关键词一刀切中的（`帮我查`这种实词），还是长度+并列兜底中的。
    后者才是最容易误判的那一类（长情绪句子），要盯的就是它。

    返回的字符串直接进 trace，所以写得短、能 grep。
    """
    t = (text or "").strip()
    if not t:
        return False, "空"
    for kw in _SKIP_KEYWORDS:
        if kw in t:
            return False, "跳过词:" + kw
    for kw in _PLAN_KEYWORDS:
        if kw in t:
            return True, "规划词:" + kw
    if len(t) >= 120:
        for kw in _MULTI_TASK_HINTS:
            if kw in t:
                return True, "长句+并列:%s(%d字)" % (kw, len(t))
        return False, "长句但没并列(%d字)" % len(t)
    # 说"没命中"而不是"短句" —— 120 字以下都走这里，80 字的情绪句
    # 直接标成"短句"会让人以为它很短，看数据时会误判。
    return False, "没命中规划词(%d字)" % len(t)


def should_plan(text):
    """要不要为这句话规划。启发式，不调模型。

    跳过词 → 规划词 → 长度。按顺序判，不合并成一个大 if：
    跳过词必须优先，「帮我总结一下啊」里既有跳过词也有规划词，按谁先算都行，
    但「在吗，帮我查天气」只能跳过。

    长度兜底这条要卡得很紧。
    原来写的是 `len(t) >= 40` —— 40 字的中文就是一句正常的聊天。实测一句
    纯情绪的"你还记得我们上次说的那个事吗，我最近一直在想……"（正好 40 字）
    会被判成复杂任务，于是多花两次 flash 调用、而且最终回答还被套上
    「你已经按计划完成了以下步骤」的任务框架。对一个陪伴型 agent，**用户最
    在意的长情绪句子恰好全都命中这条规则**，方向正好反了。
    判错的代价是不对称的：漏判只是走回普通 ReAct（他们自己的注释里也写了
    "漏判只是走回 ReAct，不会出错"），误判要花钱、还会改变她的说话方式。
    所以宁可漏：把长度门槛提到"确实像在交代一长串事"，并且要求话里带点
    并列/递进的痕迹（再、然后、顺便、以及……），而不是单纯的长。
    """
    # 🔴 判据只有 plan_decision 一处。这里再抄一遍的话，两个函数迟早会分叉
    # —— 而这个文件里"两处各写一份判断"已经出过事了（见 note 的说明）。
    return plan_decision(text)[0]


# ---------------------------------------------------------------------------
# 观测：这一节不改变任何行为，只为"将来判它删还是留"攒数据
# ---------------------------------------------------------------------------
# 规划分支现在是**观察期**。要回答的问题有三个：
#   1. 它到底触发多少次？（会不会绝大多数长消息都被它截走）
#   2. 触发了之后**成不成**？plan() 返回空、或者中途抛异常回退 ——
#      这两种只打一行日志、trace 里看不见，等于白花。
#   3. 它值不值？多花几次模型调用、多花多少毫秒。
# 攒够再决定删还是留。**这段没有任何行为副作用**，删它不影响功能。
def enabled():
    """规划分支开着没有（`plan_and_execute.enabled`）。

    单独抽出来是给 server 用的：关掉之后**不能再走 `plan()`**（它自己也会
    返回 None，但那样 server 会把它记成"判要规划却没解析出步骤"，
    等于把"配置关了"记成"它失败了"，观测数据就废了）。
    """
    try:
        return bool(_planner_config().get("enabled", False))
    except Exception:
        return False


_STATS = {
    "decided": 0,       # should_plan 判"要规划"的次数（真走了这条路的）
    "would": 0,         # **关掉期间**本来会触发的次数（将来要不要重开的依据）
    "ok": 0,            # 规划出来并正常答完
    "empty": 0,         # 判要规划，但 plan() 一步都没解析出来（回退直答）
    "error": 0,         # 规划途中抛异常（回退直答）
    "revised": 0,       # 被检查器打回改过一次
    "steps_total": 0,   # 累计步骤数（除以 ok 就是平均步数）
    "ms_total": 0,      # 累计规划+执行+收尾的耗时
    "calls_total": 0,   # 累计额外模型调用次数（规划/检查/修正各算一次）
}
_STATS_LOCK = threading.Lock()
_LAST = {}              # {"why": …, "kind": …} 最近一次，给面板/排查看


def note(**kw):
    """给统计加一笔。**任何异常都吞掉** —— 观测代码绝不能弄坏聊天。"""
    try:
        with _STATS_LOCK:
            for k, v in kw.items():
                if isinstance(v, (int, float)):
                    _STATS[k] = _STATS.get(k, 0) + v
                else:
                    _LAST[k] = v
    except Exception:
        pass


def stats():
    """给 /api/stats 看的一份快照。"""
    try:
        with _STATS_LOCK:
            d = dict(_STATS)
            d["last"] = dict(_LAST)
        d["rate"] = (round(d["ok"] / float(d["decided"]), 3)
                     if d.get("decided") else 0.0)
        d["avg_steps"] = (round(d["steps_total"] / float(d["ok"]), 2)
                          if d.get("ok") else 0.0)
        return d
    except Exception:
        return {}


def _planner_config():
    """从 config.json 读 plan_and_execute 段。"""
    try:
        from runtime import CFG_PATH
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("plan_and_execute") or {}
    except Exception:
        return {}


def _available_tools_text(src=None):
    """获取所有可用工具的说明文本，拼进规划提示词。

    从原生 FC 工具表（all_specs）生成，替代旧的 PROMPT_LINE（只列了三个内置工具）。
    规划阶段是纯文本调用（不传 tools 参数），所以需要在提示词里列工具清单。

    🔴 src 必须用本轮真实渠道：以前这里硬编码 src="app"，微信/群聊触发规划时
    提示词列出的工具和 execute_plan 的授权集合（按真实 src 算）对不上，
    模型规划了一个该渠道被关掉的工具，执行时才被"未授权"打回。
    """
    parts = []
    try:
        import tools
        specs = tools.all_specs(src=src)
        lines = []
        for t in specs:
            fn = t["function"]
            lines.append("- %s：%s" % (fn["name"], fn["description"]))
        if lines:
            parts.append("可用工具（原生 function calling）：\n" + "\n".join(lines))
        extra = tools.tool_help_extra()
        if extra:
            parts.append(extra)
    except Exception:
        pass
    return "\n".join(parts)


def _plan_prompt(text, src=None):
    """规划阶段的提示词。"""
    tools_text = _available_tools_text(src)
    return (
        "你是一个任务规划助手。用户给你一个任务，你需要把它拆解成具体的执行步骤。\n\n"
        "【可用工具】\n"
        "%s\n\n"
        "【任务】\n"
        "%s\n\n"
        "【输出要求】\n"
        "只输出一个 JSON 数组，每个元素是一个步骤，格式：\n"
        '[{"step": "步骤描述", "tool": "工具名或null", "arg": "参数或null"}, ...]\n\n'
        "规则：\n"
        "- 简单任务（不需要工具、一句话能答的）返回 [{\"step\": \"直接回答\", \"tool\": null, \"arg\": null}]\n"
        "- 每个步骤最多调一个工具\n"
        "- 工具参数要具体，不要写占位符\n"
        "- 步骤不要超过 5 步\n"
        "- 最后一步通常是 tool=null 的整理/回答步\n"
        "- 只输出 JSON，不要输出解释、不要输出 markdown 代码块"
    ) % (tools_text, text)


def _extract_json(text):
    """从模型输出里提取 JSON 数组。

    模型可能输出 markdown 代码块、可能有前后多余文本。容错提取。
    """
    t = (text or "").strip()
    if not t:
        return None
    # 去掉 markdown 代码块
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 找第一个 [ 和最后一个 ]
    start = t.find("[")
    end = t.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return None
    candidate = t[start:end + 1]
    try:
        data = json.loads(candidate)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return None


def _ask_flash(prompt, temperature, max_tokens, timeout):
    """问规划/检查用的那个便宜模型一次，拿原始文本回来。

    api_base / api_key 优先取 plan_and_execute 段自己的，留空就退回全局那份
    （跟聊天同一个上游）。拿不到就返回 None，调用方回退 ReAct。
    """
    cfg = _planner_config()
    model = cfg.get("planner_model") or "deepseek-ai/DeepSeek-V4-Flash"
    api_base = cfg.get("api_base")
    api_key = cfg.get("api_key")
    if not api_base or not api_key:
        try:
            from runtime import API_CFG
            api_base = api_base or API_CFG.get("api_base")
            api_key = api_key or API_CFG.get("api_key")
        except Exception:
            pass
    if not api_base or not api_key:
        return None
    # 🔴 模型和 base 必须同家：planner_model 默认是 DeepSeek flash，主服务商
    # 切回小米后这里会 400「Unsupported model」。失败虽然只是静默回退 ReAct，
    # 但那两次 flash 调用就白花了。换家规则统一实现在 model_pick，别在这再抄
    # 一遍 —— 抄两遍必漂移。
    try:
        from model_pick import _judge_api
        api_base, api_key, model = _judge_api(
            {"api_base": api_base, "api_key": api_key, "model": model}, cfg)
    except Exception:
        pass
    if not api_base or not model:
        return None
    try:
        import httpx
        r = httpx.post(
            api_base.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key},
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": temperature,
                  "max_tokens": max_tokens},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("[规划] 模型调用失败：%s" % str(e)[:100], flush=True)
        return None


def _split_tool(tool, arg):
    """把 "weather:北京" / "[tool:weather:北京]" 拆成 ("weather", "北京")。

    模型很爱照着提示词里的示例写全名（PROMPT_LINE 就是
    "[tool:weather:城市]"），但 tools.TOOLS 的键是裸名
    （{"ip_city":...,"weather":...}）。不拆的话 TOOLS.get("weather:北京")
    返回 None，一路落到 plugin_host.run_tool 也查不到，最后她只能跟用户说
    「查不到」—— 而工具明明有。

    括号形态必须一起处理。 原来只 partition(":") 切第一个冒号，
    而提示词里的示例**就是带括号的**，模型照抄时进来的是
    "[tool:weather:北京]" —— 切出来是 ("[tool", "weather:北京]")，
    名字变成 "[tool"、参数里还挂着个 "]"。等于上面那段注释想修的问题
    只修了一半：模型越"守规矩地照抄示例"，越必然失败。
    多冒号（"tool:weather:北京"）同理，所以先剥壳再切。

    arg 已经给了就以 arg 为准：模型把参数写两遍时它通常更具体。
    """
    tool = str(tool or "").strip().lower()
    if not tool:
        return None, arg
    # 剥掉 [tool:...] 的外壳与可能重复的 "tool:" 前缀
    m = re.match(r"^\[?\s*(?:tool\s*[:：]\s*)?(.*?)\s*\]?$", tool, re.S)
    if m:
        tool = m.group(1).strip()
    if not tool:
        return None, arg
    name, _, inline = tool.partition(":")
    name = name.strip()
    if not name:
        return None, arg
    if inline.strip() and not (arg or "").strip():
        arg = inline.strip()
    return name, (str(arg).strip() if arg is not None else None)


def plan(text, src=None):
    """让 flash 模型列步骤。返回 [(step, tool, arg), ...]，失败返回 None。

    src 是本轮真实渠道：规划提示词的工具清单必须按它过滤，和 execute_plan
    的授权集合保持一致（见 _available_tools_text）。
    """
    if not _planner_config().get("enabled", False):
        return None
    raw = _ask_flash(_plan_prompt(text, src), 0.1, 600, 30)
    if raw is None:
        return None

    steps = _extract_json(raw)
    if steps is None:
        print("[规划] 步骤解析失败，回退 ReAct。原始输出：%s" % raw[:200],
              flush=True)
        return None

    out = []
    for s in steps[:5]:
        if not isinstance(s, dict):
            continue
        step_desc = str(s.get("step") or "").strip()
        tool, arg = _split_tool(s.get("tool"), s.get("arg"))
        if step_desc or tool:
            out.append((step_desc, tool, arg))
    if not out:
        return None
    print("[规划] 规划完成，%d 步：%s" % (
        len(out), " → ".join(s[0] for s in out)), flush=True)
    return out


def _tool_spec_names(specs):
    """Extract names from the OpenAI tool schema returned by tools.all_specs."""
    names = set()
    for spec in specs or []:
        try:
            name = spec["function"]["name"]
        except (KeyError, TypeError):
            continue
        if isinstance(name, str) and name:
            names.add(name.lower())
    return names


def execute_plan(steps, src=None, allowed_tools=None, brain=None):
    """按步骤执行工具，返回 [(step_desc, tool, arg, result), ...]。

    tool 为 null 的步骤不执行工具，result 为 None（留给最后模型组织回答）。
    执行权限由当前渠道可用工具与本轮 allow-list 的交集决定；提示词里的
    工具清单不作为授权来源。没有 allow-list 时 fail closed。
    """
    import tools
    active = _tool_spec_names(tools.all_specs(brain=brain, src=src))
    requested = {str(name).lower() for name in (allowed_tools or [])}
    permitted = active & requested
    results = []
    for step_desc, tool, arg in steps:
        if not tool:
            results.append((step_desc, tool, arg, None))
            continue
        if str(tool).lower() not in permitted:
            result = "工具未授权：%s（渠道=%s）" % (tool, src or "default")
            print("[规划] 拒绝未授权工具 %s（渠道=%s）" % (tool, src or "default"), flush=True)
            results.append((step_desc, tool, arg, result))
            continue
        try:
            # D1：delegate_to_local 的工作区路径常常只写在步骤描述里
            # （arg 是自由文本），执行委派时把步骤描述并进目标，路由才
            # 能识别授权工作区；其它工具保持只用 arg，行为不变。
            if str(tool).lower() == "delegate_to_local":
                arg = ((step_desc or "") + " " + (arg or "")).strip() or None
            result = tools.run_tool(tool, arg, src=src)
            print("[规划] 执行工具 %s(%s) → %s" % (
                tool, (arg or "")[:30],
                (str(result)[:80] if result else "None")), flush=True)
        except Exception as e:
            result = "工具执行失败：%s" % str(e)[:80]
            print("[规划] 工具 %s 执行失败：%s" % (tool, str(e)[:80]), flush=True)
        results.append((step_desc, tool, arg, result))
    return results


def build_final_prompt(text, results):
    """把执行结果拼进提示，让模型组织最终回答。"""
    lines = ["（系统提示：你已经按计划完成了以下步骤，用这些结果直接回答他。）\n"]
    for step_desc, tool, arg, result in results:
        if tool and result:
            lines.append("步骤【%s】调用 %s，结果：%s" % (step_desc, tool, result))
        elif tool and not result:
            lines.append("步骤【%s】调用 %s，但查询失败（照实说查不到，别编）" % (step_desc, tool))
        else:
            lines.append("步骤【%s】" % step_desc)
    lines.append("\n他的原话：%s" % text)
    lines.append("\n现在用上面的结果直接回答他，别再写工具标签，别再写思考过程。")
    return "\n".join(lines)


# --- 结果检查器（第3步：轻量监督）---

def _results_text(results):
    """把执行结果格式化成简洁文本，供检查器参考。"""
    lines = []
    for step_desc, tool, arg, result in results:
        if tool and result:
            lines.append("- [%s] %s → %s" % (tool, step_desc, str(result)[:200]))
        elif tool and not result:
            lines.append("- [%s] %s → 执行失败/无结果" % (tool, step_desc))
        else:
            lines.append("- %s（无需工具）" % step_desc)
    return "\n".join(lines)


def _verify_prompt(text, results, answer):
    """结果检查器的提示词。只检查3件事，不做内容评判。"""
    return (
        "你是一个严格的结果检查器。系统已经按计划执行了工具并拿到结果，"
        "现在助手给出了回答。请只检查以下3件事：\n\n"
        "1. 执行成功的工具结果，回答里必须用到，不能忽略\n"
        "2. 执行失败的工具，回答里必须照实说查不到/失败，不能假装成功或编造结果\n"
        "3. 不能编造执行结果里没有的信息（数字、事实、数据）\n\n"
        "【用户问题】\n%s\n\n"
        "【执行结果】\n%s\n\n"
        "【助手回答】\n%s\n\n"
        "【输出要求】\n"
        "- 如果回答完全正确（3条都满足），只输出 OK\n"
        "- 如果有问题，输出修正建议，格式：\n"
        "  问题：具体哪里错了\n"
        "  修正：应该怎么改\n"
        "- 只输出 OK 或修正建议，不要输出其他内容，不要输出 markdown"
    ) % (text, _results_text(results), answer)


def verify_result(text, results, answer):
    """检查主模型回答是否正确使用了所有执行结果。

    返回：
    - None：回答没问题，不需要修正
    - 字符串：修正建议，喂给主模型让她修正

    检查器用 flash 模型，跟规划同一个。只在 Plan-and-Execute 路径调用，
    闲聊不走这里，0 额外成本。

    **复杂度过滤（2026-10-08 新增）**：简单任务跳过检查，省一次 flash 调用。
    判断标准：实际调用了工具的步骤 <= 2 且 涉及的不同工具数 <= 1 → 跳过。
    比如"查一下天气"（1步1工具）、"帮我算个数"（1步1工具）都不需要检查。
    多步骤、多工具的复杂任务才跑检查器，保证质量。
    """
    if not _planner_config().get("enabled", False):
        return None
    # 没有工具调用的纯回答不用检查（规划结果里全是 tool=None 的步骤）
    used_tools = [tool for _, tool, _, _ in results if tool]
    if not used_tools:
        return None
    # 简单任务跳过：工具步骤 <= 2 且 不同工具数 <= 1
    if len(used_tools) <= 2 and len(set(used_tools)) <= 1:
        print("[检查器] 简单任务（%d步/%d工具），跳过检查" %
              (len(used_tools), len(set(used_tools))), flush=True)
        return None

    raw = _ask_flash(_verify_prompt(text, results, answer), 0.0, 300, 20)
    if raw is None:
        print("[检查器] 没调通，跳过检查", flush=True)
        return None
    raw = raw.strip()

    if raw.upper().startswith("OK") or raw == "":
        # 这行别加 emoji：云端控制台是 GBK，print 一个对勾会抛
        # UnicodeEncodeError，而它在 try 之外 —— 每次判定通过都炸一次。
        print("[检查器] 回答通过检查", flush=True)
        return None

    print("[检查器] 发现问题，建议修正：%s" % raw[:150], flush=True)
    return raw


def build_revise_prompt(text, results, answer, verify_feedback):
    """把检查器的修正建议拼进提示，让主模型修正回答。

    开头必须跟 build_final_prompt 一样以「（系统提示：你已经按计划完成了以下步骤」
    起头 —— server._restore_hist 靠这个前缀认出「这条不是用户说的话」并撤掉。
    两句提示内容不同但前缀相同，认得出的部分就够用。
    """
    lines = [
        "（系统提示：你已经按计划完成了以下步骤，但检查器发现你的回答有问题。"
        "请根据下面的反馈修正。)\n"
    ]
    lines.append("【他的原话】%s" % text)
    lines.append("\n【执行结果】")
    for step_desc, tool, arg, result in results:
        if tool and result:
            lines.append("- [%s] %s → %s" % (tool, step_desc, str(result)[:200]))
        elif tool and not result:
            lines.append("- [%s] %s → 执行失败/无结果（照实说查不到）" % (tool, step_desc))
    lines.append("\n【你刚才的回答】\n%s" % answer)
    lines.append("\n【检查器反馈】\n%s" % verify_feedback)
    lines.append("\n现在根据执行结果和检查器反馈，重新回答他。"
                 "必须用上所有执行成功的结果，执行失败的照实说查不到，不许编造。"
                 "别再写工具标签，别再写思考过程。")
    return "\n".join(lines)
