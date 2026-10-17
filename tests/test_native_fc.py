# -*- coding: utf-8 -*-
"""原生 function calling 的多轮链路（线上走的就是 draft_native）。

不联网：把 brain.httpx 换成一个按顺序吐响应的假对象，直接驱动 draft_native，
检查它**真的发出了什么**。这一批断言的价值在于：原生 FC 出错的方式大多很安静
—— 工具没被调用、参数没解析出来、role:tool 没配对，模型只会正常地答一句
（编的），HTTP 全是 200，日志一行不报。

覆盖：
  1. chat() 确实走 draft_native（防回退）
  2. 多轮：tool_calls → role:tool（tool_call_id 严格对齐）→ 出正文
  3. no_tools=True 时完全不挂工具表（plan 分支收尾用）
  4. features.tools 关掉时不挂工具表，**而且还能正常说话**
     （原来 len(None) 会抛 TypeError，被吞掉之后整轮变"API失败"，
      也就是把工具开关一关她就哑了）
  5. 模型陷在工具循环里时：收尾那次不挂工具、逼出正文，而不是返回 None
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import brain as B  # noqa: E402
import features  # noqa: E402
import plugin_host  # noqa: E402
import tools as tl  # noqa: E402


class _Resp(object):
    def __init__(self, obj, status=200, text=""):
        self._o = obj
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError(
                "stub %s" % self.status_code, request=None, response=self)

    def json(self):
        return self._o


def _tool_call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _say(text):
    return {"choices": [{"message": {"content": text}}]}


def _want(cid, name, args):
    return {"choices": [{"message": {"content": "",
                                     "tool_calls": [_tool_call(cid, name, args)]}}]}


class TestNativeFunctionCalling(unittest.TestCase):

    def setUp(self):
        plugin_host.load(force=True)
        self.seen = []
        self._real_httpx = B.httpx
        self._real_features_on = features.on
        # This contract test is about native tool dispatch, independent of the
        # developer's local per-channel feature preferences.
        features.on = lambda key, src=None: (
            True if key == "calc" else self._real_features_on(key, src))
        # 模型出口虽收口到 model_providers，但 CloudProvider 经 brain.httpx
        # 取传输层（见 cloud._active_http），所以桩 B.httpx 一处即可。

    def tearDown(self):
        B.httpx = self._real_httpx
        features.on = self._real_features_on

    def _stub(self, payloads):
        """按顺序返回响应；用完之后一直重复最后一个。

        记录请求体**必须深拷贝**：draft_native 传的是同一个 msgs 列表对象，
        多轮循环会继续往它上面 append。存引用的话，后面几轮追加的内容会
        "倒灌"进前面几轮的记录里 —— 断言就会看到第 1 次请求里已经有了第 2 轮
        的 assistant/tool 消息。（这个坑本身踩过一次。）
        """
        import copy
        seen = self.seen

        def _post(url, headers=None, json=None, timeout=None):
            seen.append(copy.deepcopy(json))
            return _Resp(payloads[min(len(seen) - 1, len(payloads) - 1)])

        B.httpx = type("H", (), {"post": staticmethod(_post)})

    def _brain(self):
        return B.Brain(mem=None, api_config={
            "api_key": "test", "api_base": "http://example.invalid/v1",
            "model": "主模型"})

    def _max_rounds(self):
        """工具循环的实际上限 —— 别在测试里写死轮数。

        这个值是**会调的**（为了多步任务"整理文件"从 6 提到了 24），
        写死的话每次调都得回来改测试，改漏了就是一条假失败。
        """
        return int(self._brain().api.get("tool_max_rounds") or 24)

    # --- 1. 别悄悄换回去，也别把死掉的那两条复活 ---
    def test_chat_uses_draft_native(self):
        src = inspect.getsource(B.Brain.chat)
        self.assertIn("self.draft_native(", src,
                      "chat() 不再走原生 function calling 了")

    def test_只有一条拼提示词的链(self):
        """🔴 `draft()` / `draft_router()` 必须保持**不存在**。

        它们被删掉不是因为"没人用"，而是因为**同一件事有两份实现**：
        `draft()` 是唯一会拼 draft_rules 的地方，而 chat() 不再调它 ——
        整块说话规则静默失效；两条链的提示词还都在教 `[tool:...]` 标签，
        跟 tools schema 打架。谁把它们加回来，这里就红。
        """
        src = inspect.getsource(B.Brain)
        self.assertNotIn("def draft(self", src,
                         "draft() 又回来了 —— 它和 draft_native 就是两份提示词")
        self.assertNotIn("def draft_router(self", src,
                         "draft_router() 又回来了 —— 同上")
        self.assertIn("_user_msg_with_rules", inspect.getsource(B.Brain.draft_native),
                      "draft_native 没走统一的规则拼接口")

    # --- 2. 多轮 + role:tool 配对 ---
    def test_multi_round_pairs_tool_results(self):
        self._stub([
            _want("c1", "calc", {"expression": "128*3"}),
            _want("c2", "calc", {"expression": "7+8"}),
            _say("128×3 是 384，7+8 是 15。"),
        ])
        br = self._brain()
        ans = br.draft_native("算两个数", "")
        self.assertEqual(len(self.seen), 3, "应该 2 轮工具 + 1 轮正文")
        self.assertTrue(ans and "384" in ans, "最终正文没拿到：%r" % ans)
        self.assertEqual(br.last_tools_used, ["calc"])

        msgs = self.seen[1]["messages"]
        wants = [m for m in msgs
                 if m.get("role") == "assistant" and m.get("tool_calls")]
        results = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(wants), 1)
        self.assertEqual(len(results), 1)
        # OpenAI 格式：每个 tool_call 必须配一条同 id 的 role:tool，
        # 配错了下一次请求会 400
        self.assertEqual(results[0].get("tool_call_id"), "c1")
        self.assertIn("384", results[0].get("content") or "")
        # 每一轮都要挂着工具表，否则第二轮模型没法再调
        self.assertIn("tools", self.seen[0])
        self.assertIn("tools", self.seen[1])

    def test_parallel_calls_in_one_round_all_get_results(self):
        self._stub([
            {"choices": [{"message": {"content": "", "tool_calls": [
                _tool_call("a", "calc", {"expression": "1+1"}),
                _tool_call("b", "calc", {"expression": "2+2"}),
            ]}}]},
            _say("1+1 是 2，2+2 是 4。"),
        ])
        br = self._brain()
        ans = br.draft_native("算两个", "")
        results = [m for m in self.seen[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual([m.get("tool_call_id") for m in results], ["a", "b"])
        self.assertTrue(ans)

    # --- 3. no_tools ---
    def test_no_tools_sends_no_tool_table(self):
        self._stub([_say("直接回答。")])
        ans = self._brain().draft_native("plan 收尾", "", no_tools=True)
        self.assertNotIn("tools", self.seen[0], "no_tools=True 却挂了工具表")
        self.assertTrue(ans)

    # --- 4. 关掉工具开关不能把她弄哑 ---
    def test_tools_switch_off_still_answers(self):
        orig = features.on
        features.on = lambda k, src=None: (False if k == "tools"
                                           else orig(k, src))
        try:
            self._stub([_say("关掉工具后照样能说话。")])
            br = self._brain()
            ans = br.draft_native("随便说点什么", "")
        finally:
            features.on = orig
        self.assertNotIn("tools", self.seen[0])
        self.assertTrue(ans, "关掉工具开关之后她不能说话了（len(None) 那类崩溃）")
        self.assertEqual(br.last_sizes.get("tools"), 0)

    # --- 5. 工具循环刹不住时的收尾 ---
    def test_runaway_loop_falls_back_to_prose_without_tools(self):
        """模型一直要调工具时，最后一轮**不挂工具**逼它出正文。

        轮数上限从 6 提到 24（多步任务"整理文件"要十几步才够），
        所以这里按实际上限来构造桩：前 N 轮一直要工具，第 N+1 轮给正文。
        """
        N = self._max_rounds()
        loop = _want("c", "calc", {"expression": "1+1"})
        self._stub([loop] * N + [_say("查完了，就是 2。")])
        br = self._brain()
        ans = br.draft_native("死循环", "")
        self.assertEqual(len(self.seen), N + 1, "应该是 %d 轮 + 1 次收尾" % N)
        self.assertNotIn("tools", self.seen[-1],
                         "收尾那次还挂着工具，模型只会继续要工具")
        self.assertTrue(ans, "收尾没逼出正文 → 用户会看到'脑子转不动'")

    # --- 6. 未确认的错误不得去掉工具表盲目重试 ---
    def test_tools_rejection_does_not_blindly_retry(self):
        """未知异常不能推断为 tools 不兼容并自动重新计费。"""
        seen = self.seen

        class _Boom(Exception):
            pass

        def _post(url, headers=None, json=None, timeout=None):
            import copy
            seen.append(copy.deepcopy(json))
            if "tools" in json:
                raise _Boom("400 bad request: unknown field tools")
            return _Resp(_say("不带工具也照样回答。"))

        B.httpx = type("H", (), {"post": staticmethod(_post)})
        br = self._brain()
        ans = br.draft_native("帮我查天气", "")
        self.assertEqual(len(seen), 1, "未知错误不得重发模型请求")
        self.assertIn("tools", seen[0])
        self.assertTrue(ans, "降级之后她还是说不了话")

    def test_retry_does_not_loop_forever(self):
        """一次失败就停止，不递归或重新派发请求。"""
        seen = self.seen

        def _post(url, headers=None, json=None, timeout=None):
            import copy
            seen.append(copy.deepcopy(json))
            raise RuntimeError("一直失败")

        B.httpx = type("H", (), {"post": staticmethod(_post)})
        ans = self._brain().draft_native("帮我查天气", "")
        self.assertIn('未自动重试', ans)
        self.assertEqual(len(seen), 1, "异常后不得盲目重试")

    # --- 7. 寒暄不挂工具表（省 ~1100 token）---
    def test_smalltalk_does_not_attach_schema(self):
        self._stub([_say("在呢。")])
        br = self._brain()
        ans = br.draft_native("在吗", "")
        self.assertNotIn("tools", self.seen[0], "寒暄也挂了工具表，白花钱")
        self.assertTrue(ans)
        self.assertEqual(br.last_sizes.get("tools"), 0)

    def test_short_message_that_needs_a_tool_still_gets_schema(self):
        """判错的代价不对称：短消息里只要可能有工具需求，就必须照挂。"""
        self._stub([_say("北京今天晴。")])
        self._brain().draft_native("北京天气", "")
        self.assertIn("tools", self.seen[0],
                      "『北京天气』只有 4 个字但正需要工具，不该被省掉")


class TestSchemaNeeded(unittest.TestCase):
    """schema_needed 的判据：只在明确是寒暄时才省，宁可不省。"""

    def test_chat_only(self):
        for t in ("在吗", "晚安", "哈哈哈", "谢谢啦", "嗯嗯", ""):
            self.assertFalse(tl.schema_needed(t), t)

    def test_needs_tools(self):
        for t in ("北京天气", "128乘3", "在吗，帮我查天气", "帮我算一下",
                  "那个快递到了吗", "几点了", "今天好累啊"):
            self.assertTrue(tl.schema_needed(t), t)


class TestSpeechRulesReachTheModel(unittest.TestCase):
    """约束不再进提示词；人设说什么就是什么。

    ## 为什么单独立一条

    换到原生 function calling 之后，规则**整块漏过一次**：`draft_native` 和
    `draft_router` 自己拼 `msgs`，都只 append 了 `user_text`，而拼规则那行留在
    已经没人调用的老 `draft()` 里。于是发给模型的话里没有：
      - 说话风格（短句、像发微信、禁客服腔、不恭维、不撒娇）
      - 去 AI 味清单
      - 发图 / 语音 / [rand:] / [gen:] 的用法（"不会梗"就是这个）
      - 【说话多少】那个旋钮
    而接口、缓存、配置、界面**全都是对的** —— 面板显示"已设为 7"，
    运行时 `chatty.level()` 也确实返回 7，只是那段文字从没被拼进请求。
    症状是"她不像群友、不会梗、说话多少调了没反应"，查起来极难往
    "提示词里少了一段"上想。

    ## 判据分两层

    ① 内容在请求里（上面四类各取一个特征串）
    ② **位置**对：贴在最后一条 user 上，不是塞进 system —— 原设计明确说
       "放 system 尾部模型照做率明显差"，位置错了等于白拼
    """

    def setUp(self):
        plugin_host.load(force=True)
        self.seen = []
        self._real = B.httpx

    def tearDown(self):
        B.httpx = self._real

    def _stub(self, payloads):
        import copy

        def _post(url, headers=None, json=None, timeout=None):
            self.seen.append(copy.deepcopy(json))
            return _Resp(payloads[min(len(self.seen) - 1, len(payloads) - 1)])

        B.httpx = type("H", (), {"post": staticmethod(_post)})

    def _brain(self):
        return B.Brain(mem=None, api_config={
            "api_key": "test", "api_base": "http://example.invalid/v1",
            "model": "主模型"})

    def _check(self, payload, label):
        """**规则不再由代码拼进提示词**（2026-10-08 起）。

        原来这里钉的是"说话风格 / 去 AI 味 / 发图规则 / 说话多少 四段必须
        出现在请求里"—— 那套约束在代码里硬写，会跟人设打架（人设改成活泼话痨，
        代码还在说"别硬凑排比"），而且用户改不了。

        现在规则由人设文件定义，所以断言反过来：**代码不许再拼任何约束段**，
        拼了就是两套规则同时生效，又回到"人设改了但行为没变"的老问题。
        """
        blob = json.dumps(payload, ensure_ascii=False)
        for gone in ("下面是回话要求", "禁 AI 高频词", "【说话多少】",
                     "[tool:weather", "别恭维"):
            self.assertNotIn(gone, blob,
                             "%s：代码又拼了行为约束段（%s）——规则该写在人设里"
                             % (label, gone))
        # 人设本身必须在（关系/性格/边界靠它）
        users = [m for m in payload.get("messages", [])
                 if m.get("role") == "user"]
        self.assertTrue(users, "%s：连 user 消息都没有" % label)
        self.assertEqual(users[-1].get("content") or "", "在吗",
                         "%s：他的话被改写了（不该再拼任何东西）" % label)


    def test_draft_native_sends_persona_but_no_rules(self):
        """人设必须在、规则必须不在。

        这是这次改动的核心断言：约束不再由代码拼（拼了就等于两套规则同时
        生效，人设改了行为却不动），但人设本身一个字都不能少 —— 她是谁、
        跟他的关系、边界，全靠 system 里那段。
        """
        self._stub([_say("好")])
        br = self._brain()
        br.draft_native("在吗", "")
        payload = self.seen[-1]
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertIn("你和他是什么关系", blob,
                      "人设没进提示词 —— 约束删了但她是谁也得留着")
        self.assertNotIn("下面是回话要求", blob)


if __name__ == "__main__":
    unittest.main()
