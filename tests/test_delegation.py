# -*- coding: utf-8 -*-
"""P2 混合委派：合同 / 沙箱 / 验证器 / Dispatcher / 门控。

全部不联网：Dispatcher 用脚本化假 provider（返回 OpenAI 形态响应），
沙箱用临时目录的真实文件。真实 Ollama 端到端另有手动验收（见 P2 记录）。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
while BRAIN in sys.path:
    sys.path.remove(BRAIN)
sys.path.insert(0, BRAIN)

from delegation import contracts as C          # noqa: E402
from delegation.sandbox import ReadOnlySandbox, SandboxError  # noqa: E402
from delegation import validator as V          # noqa: E402
from delegation import bridge as DBridge       # noqa: E402
from delegation.dispatcher import run_delegation  # noqa: E402


class _Workspace(object):
    """工作区夹具 mixin（不继承 TestCase，否则会被卫生检查当成空用例类）。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dlg_test_")
        self.ws = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.ws, "src"))
        open(os.path.join(self.ws, "src", "auth.py"), "w",
             encoding="utf-8").write(
            "def login(u):\n    return issue_token(None)\n")
        open(os.path.join(self.ws, "error.log"), "w",
             encoding="utf-8").write("TypeError: NoneType has no id\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def contract(self, **over):
        base = {"kind": "read_only_bug_analysis",
                "goal": "定位 500 根因",
                "workspace": self.ws,
                "input_refs": ["src/auth.py", "error.log"]}
        base.update(over)
        return C.validate_contract(base, src="pc", allowed_roots=[self.tmp])


class TestContracts(_Workspace, unittest.TestCase):
    def test_正常合同(self):
        c = self.contract()
        self.assertEqual(c["kind"], "read_only_bug_analysis")
        self.assertEqual(c["input_refs"], ["src/auth.py", "error.log"])
        self.assertTrue(c["limits"]["max_model_requests"] <= 6)
        self.assertEqual(c["src"], "pc")

    def test_kind白名单(self):
        for bad in ("", "sandbox_patch_and_test", "rm_rf", "write_file"):
            with self.assertRaises(C.ContractError) as cm:
                self.contract(kind=bad)
            self.assertEqual(cm.exception.reason, "kind_not_allowed")

    def test_限额被硬顶夹住(self):
        c = self.contract(
            limits={"max_model_requests": 999, "max_tool_calls": 999,
                    "timeout_seconds": 99999})
        self.assertEqual(c["limits"]["max_model_requests"], 6)
        self.assertEqual(c["limits"]["max_tool_calls"], 8)
        self.assertEqual(c["limits"]["timeout_seconds"], 180)

    def test_限额非法回默认(self):
        c = self.contract(limits={"max_model_requests": "abc"})
        self.assertGreater(c["limits"]["max_model_requests"], 0)

    def test_workspace越界拒绝(self):
        other = tempfile.mkdtemp(prefix="dlg_out_")
        try:
            with self.assertRaises(C.ContractError) as cm:
                self.contract(workspace=other)
            self.assertEqual(cm.exception.reason, "workspace_not_authorized")
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_相对路径穿越被滤掉(self):
        with self.assertRaises(C.ContractError) as cm:
            self.contract(input_refs=["../../../Windows/win.ini"])
        self.assertEqual(cm.exception.reason, "input_refs_invalid")

    def test_空根名单fail_closed(self):
        # 即使给了真实目录，没有授权根也不许
        with self.assertRaises(C.ContractError):
            C.validate_contract(
                {"kind": "file_discovery", "goal": "x", "workspace": self.ws},
                src="pc", allowed_roots=[])

    def test_无工作区kind不需要ws(self):
        c = C.validate_contract(
            {"kind": "structured_extraction",
             "goal": "从文本抽取人名"}, src="pc", allowed_roots=[])
        self.assertEqual(c["workspace"], "")
        self.assertEqual(c["allowed_tools"], ())

    def test_非法schema拒绝(self):
        with self.assertRaises(C.ContractError) as cm:
            self.contract(expected_schema="i_can_write_files")
        self.assertEqual(cm.exception.reason, "schema_not_allowed")

    def test_缺goal(self):
        with self.assertRaises(C.ContractError):
            self.contract(goal="  ")


class TestSandbox(_Workspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.sb = ReadOnlySandbox(self.ws)

    def test_列目录(self):
        out = json.loads(self.sb.file_list("."))
        names = [e["name"] for e in out]
        self.assertIn("src", names)
        self.assertIn("error.log", names)

    def test_查找(self):
        out = json.loads(self.sb.file_find("*.py", "."))
        self.assertEqual(out, ["src/auth.py"])

    def test_读文件(self):
        self.assertIn("issue_token", self.sb.read_file("src/auth.py"))

    def test_grep命中(self):
        out = self.sb.file_grep("NoneType", ".")
        self.assertIn("error.log:1", out)

    def test_绝对路径拒绝(self):
        for p in (r"C:\Windows\win.ini", "/etc/passwd"):
            with self.assertRaises(SandboxError) as cm:
                self.sb.read_file(p)
            self.assertEqual(cm.exception.category, "path_escape")

    def test_dotdot拒绝(self):
        with self.assertRaises(SandboxError):
            self.sb.read_file("../../secret.txt")

    def test_不存在(self):
        with self.assertRaises(SandboxError) as cm:
            self.sb.read_file("nope.txt")
        self.assertEqual(cm.exception.category, "not_found")

    def test_junction越界不跟随(self):
        # 在工作区放一个指向外部目录的 junction，read/list 都不能顺着出去
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside, exist_ok=True)
        open(os.path.join(outside, "secret.txt"), "w").write("TOPSECRET")
        link = os.path.join(self.ws, "out_link")
        try:
            # Windows 上目录联接（junction）不需要管理员权限；
            # target_is_directory=True 时 CPython 自动走 junction。
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境建不了 junction")
        with self.assertRaises(SandboxError):
            self.sb.read_file("out_link/secret.txt")
        found = json.loads(self.sb.file_find("secret.txt", "."))
        self.assertNotIn("out_link/secret.txt", found)

    def test_未授权工具拒绝(self):
        with self.assertRaises(SandboxError) as cm:
            self.sb.execute("write_file", {"path": "x"})
        self.assertEqual(cm.exception.category, "tool_not_allowed")

    def test_grep传文件路径_直接搜该文件(self):
        # P3 修复 H03：path 是文件时不再报 "不是目录"
        out = self.sb.file_grep("NoneType", "error.log")
        self.assertIn("error.log:1", out)

    def test_grep文件无匹配也给明确提示(self):
        out = self.sb.file_grep("ZZZZNOTEXIST", "error.log")
        self.assertIn("没有匹配", out)

    def test_缓存版本戳随文件内容变化(self):
        v1 = self.sb.version_of("src/auth.py")
        p = os.path.join(self.ws, "src", "auth.py")
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n# changed\n")
        v2 = self.sb.version_of("src/auth.py")
        self.assertNotEqual(v1, v2)


class TestValidator(_Workspace, unittest.TestCase):
    def _blob(self, rel):
        with open(os.path.join(self.ws, rel.replace("/", os.sep)),
                  encoding="utf-8") as f:
            return f.read()

    def test_bug分析_证据真实(self):
        text = ('{"answer":"login 未判空，None 传进 issue_token",'
                '"evidence":[{"path":"src/auth.py","line":2,'
                '"excerpt":"return issue_token(None)","observation":"未判空"}]}')
        events = [{"tool": "read_file", "path": "src/auth.py", "ok": True}]
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       text, self.ws, tool_events=events,
                       read_blobs={"src/auth.py": self._blob("src/auth.py")})
        self.assertTrue(v["ok"], v["problems"])
        self.assertEqual(v["structured"]["answer"],
                         "login 未判空，None 传进 issue_token")
        self.assertTrue(v["verified"])
        self.assertEqual(v["evidence"]["verified"][0]["path"], "src/auth.py")

    def test_摘录必须来自实读内容_否则不通过(self):
        text = ('{"answer":"x",'
                '"evidence":[{"path":"src/auth.py","excerpt":"伪造的原文行"}]}')
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       text, self.ws,
                       tool_events=[{"tool": "read_file",
                                     "path": "src/auth.py", "ok": True}],
                       read_blobs={"src/auth.py": self._blob("src/auth.py")})
        self.assertFalse(v["ok"])
        self.assertTrue(any("找不到" in p or "编造" in p for p in v["problems"]))

    def test_引用不存在文件_invalid_evidence(self):
        text = '{"answer":"x","evidence":[{"path":"src/ghost.py","excerpt":"y"}]}'
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       text, self.ws,
                       tool_events=[{"tool": "read_file",
                                     "path": "src/ghost.py"}])
        self.assertFalse(v["ok"])
        self.assertTrue(any("不存在" in p for p in v["problems"]))

    def test_无工具调用的分析不算完成(self):
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       '{"answer":"x","evidence":[]}',
                       self.ws, tool_events=[])
        self.assertFalse(v["ok"])

    def test_answer超长不通过(self):
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       json.dumps({"answer": "长" * 901, "evidence": []}),
                       self.ws, tool_events=[])
        self.assertFalse(v["ok"])
        self.assertTrue(any("answer" in p for p in v["problems"]))

    def test_抽JSON失败(self):
        v = V.validate("structured_extraction", "extraction_v1",
                       "我觉得是张三，但没按 JSON 输出", "")
        self.assertFalse(v["ok"])
        self.assertTrue(any("JSON" in p for p in v["problems"]))

    def test_file_list证据校验(self):
        text = '{"answer":"发现 2 个文件","files":["src/auth.py","src/missing.py"]}'
        v = V.validate("file_discovery", "file_list_v1", text, self.ws,
                       tool_events=[{"tool": "file_find"}])
        self.assertFalse(v["ok"])
        self.assertIn("src/missing.py", v["evidence"]["missing"])

    def test_plan无工作区_通用草案可过(self):
        text = '{"steps":["读日志","定位","修复建议"]}'
        v = V.validate("plan_draft", "plan_draft_v1", text, "")
        self.assertTrue(v["ok"], v["problems"])
        self.assertTrue(v["generic"])
        self.assertTrue(v["needs_main_agent_review"])

    def test_plan有工作区_零取证不通过(self):
        text = '{"steps":["读日志","定位","修复建议"]}'
        v = V.validate("plan_draft", "plan_draft_v1", text, self.ws,
                       tool_events=[])
        self.assertFalse(v["ok"])
        self.assertTrue(any("取证" in p for p in v["problems"]))

    def test_plan有工作区_取证后通过(self):
        text = ('{"steps":["读堆栈","统一 user_id/uid 字段","跑测试回归"],'
                '"evidence_paths":["src/auth.py"]}')
        events = [{"tool": "read_file", "path": "src/auth.py", "ok": True}]
        v = V.validate("plan_draft", "plan_draft_v1", text, self.ws,
                       tool_events=events)
        self.assertTrue(v["ok"], v["problems"])
        self.assertFalse(v["generic"])
        self.assertTrue(v["verified"])


# --- 脚本化 provider：按调用次数返回预设响应 ---
class _Msg(object):
    @staticmethod
    def say(text):
        return {"choices": [{"message": {"content": text}}]}

    @staticmethod
    def call(cid, name, args):
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


class _ScriptedProvider(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.payloads = []

    def ready(self):
        return True

    def chat(self, payload, timeout=None):
        self.payloads.append(payload)
        i = min(self.calls, len(self.responses) - 1)
        r = self.responses[i]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r


class TestDispatcher(_Workspace, unittest.TestCase):
    def _run(self, responses, **over):
        c = self.contract(**over) if over.get("use_contract", True) else None
        if c is None:
            c = self.contract()
        p = _ScriptedProvider(responses)
        return run_delegation(c, ollama_cfg={"model": "m"},
                              provider_factory=lambda cfg: p,
                              persist=False), p

    def test_工具取证后出JSON_成功(self):
        final = ('{"answer":"login 未判空，None 传进 issue_token",'
                 '"evidence":[{"path":"src/auth.py","line":2,'
                 '"excerpt":"return issue_token(None)","observation":"未判空"}]}')
        r, p = self._run([
            _Msg.call("c1", "read_file", {"path": "src/auth.py"}),
            _Msg.say(final),
        ])
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertEqual(r["tool_calls"], 1)
        self.assertIn("login 未判空", r["structured"]["answer"])
        self.assertIn("src/auth.py", r["evidence"]["existing"])
        self.assertTrue(r["verified"])
        # 沙箱工具结果确实回灌了
        tool_msgs = [m for m in p.payloads[1]["messages"]
                     if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("issue_token", tool_msgs[0]["content"])

    def test_工具数熔断(self):
        # structured_extraction 没有工具额度；构造一个 tools 非空但合同 0 额度
        c = C.validate_contract(
            {"kind": "structured_extraction", "goal": "抽取"},
            src="pc", allowed_roots=[])
        p = _ScriptedProvider([
            _Msg.call("c1", "read_file", {"path": "x"}),
        ])
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p, persist=False)
        self.assertEqual(r["status"], "failed")
        self.assertTrue(any(e["category"] == "tool_limit" for e in r["errors"]))

    def test_模型不可用立即失败(self):
        from model_providers.base import ProviderUnavailable
        r, _ = self._run([ProviderUnavailable("Ollama down")])
        self.assertEqual(r["status"], "failed")
        self.assertTrue(any(e["category"] == "unavailable" for e in r["errors"]))

    def test_空壳响应在预算内续跑一次后成功(self):
        # 取证后撞上 qwen think 偶发的 content/tool_calls 全空（provider 抛
        # bad_response）：不立即失败，在现有请求预算内续跑一次，第二轮给
        # 合法 JSON 即验收通过；坏响应本身留档可观测。
        from model_providers.base import ProviderResponseError
        final = ('{"answer":"login 未判空，None 传进 issue_token",'
                 '"evidence":[{"path":"src/auth.py","line":2,'
                 '"excerpt":"return issue_token(None)","observation":"未判空"}]}')
        r, p = self._run([
            _Msg.call("c1", "read_file", {"path": "src/auth.py"}),
            ProviderResponseError("Ollama 响应里没有正文也没有工具调用"),
            _Msg.say(final),
        ])
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertEqual(p.calls, 3)
        self.assertTrue(r["verified"])
        self.assertTrue(any(e["category"] == "bad_response"
                            for e in r["errors"]))

    def test_连续两次空壳响应仍失败且不无限重试(self):
        # 续跑最多一次：第二次仍空 → failed，总请求 = 2（首次 + 续跑），
        # 不吃掉 6 轮预算，更不提权云端。
        from model_providers.base import ProviderResponseError
        empty = ProviderResponseError("Ollama 响应里没有正文也没有工具调用")
        r, p = self._run([empty, empty, empty, empty, empty, empty])
        self.assertEqual(r["status"], "failed")
        self.assertEqual(p.calls, 2)
        self.assertEqual(
            sum(1 for e in r["errors"] if e["category"] == "bad_response"), 2)

    def test_验收失败重试一次后通过(self):
        bad = "我觉得是个问题"            # 不是 JSON
        good = ('{"answer":"login 未判空","evidence":[{"path":"src/auth.py",'
                '"line":2,"excerpt":"return issue_token(None)"}]}')
        r, p = self._run([
            _Msg.call("c1", "read_file", {"path": "src/auth.py"}),
            _Msg.say(bad),
            _Msg.say(good),                 # 第 3 次响应 = 修正轮
        ])
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertTrue(r["retry_used"])
        self.assertEqual(p.calls, 3)

    def test_重试仍失败(self):
        c = self.contract()
        p = _ScriptedProvider([
            _Msg.call("c1", "read_file", {"path": "src/auth.py"}),
            _Msg.say("还是不对"),
            _Msg.say("依然不对"),
        ])
        # 两次正文都不过 → failed，且只重试一次（总请求 = 工具轮+首文+修正=3）
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p, persist=False)
        self.assertEqual(r["status"], "failed")
        self.assertTrue(any(e["category"] == "validation_failed"
                            for e in r["errors"]))
        self.assertLessEqual(p.calls, 3)

    def test_取消(self):
        ev = threading.Event()
        ev.set()
        c = self.contract()
        p = _ScriptedProvider([_Msg.say("x")])
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p,
                           cancel_event=ev, persist=False)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(p.calls, 0)

    def test_结果落盘(self):
        c = self.contract(kind="file_discovery",
                          expected_schema="file_list_v1")
        # file_discovery 期待 file_list_v1（紧凑版带 answer）
        final = '{"answer":"发现 2 个相关文件","files":["src/auth.py","error.log"]}'
        p = _ScriptedProvider([
            _Msg.call("c1", "file_find", {"pattern": "*"}),
            _Msg.say(final),
        ])
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p, persist=True)
        self.assertEqual(r["status"], "succeeded", r["errors"])
        import paths
        fp = os.path.join(paths.DATA_DIR, "delegations", r["task_id"] + ".json")
        self.assertTrue(os.path.isfile(fp))
        data = json.load(open(fp, encoding="utf-8"))
        self.assertEqual(data["status"], "succeeded")

    def test_最近委记录读回是摘要且倒序(self):
        import paths
        c = self.contract(kind="file_discovery",
                          expected_schema="file_list_v1")
        p = _ScriptedProvider([
            _Msg.call("c1", "file_find", {"pattern": "*"}),
            _Msg.say('{"answer":"找到 src/auth.py","files":["src/auth.py"]}'),
        ])
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p, persist=True)
        fp = os.path.join(paths.DATA_DIR, "delegations", r["task_id"] + ".json")
        try:
            items = DBridge.recent_records(20)
            mine = [x for x in items if x["task_id"] == r["task_id"]]
            self.assertEqual(len(mine), 1)
            rec = mine[0]
            # 摘要字段齐全；output 全文不许进面板数据（可能含文件内容片段）
            self.assertEqual(rec["status"], "succeeded")
            self.assertEqual(rec["kind"], "file_discovery")
            self.assertGreaterEqual(rec["created_at"], r["created_at"] - 1)
            self.assertNotIn("output", rec)
            self.assertIsInstance(rec["errors"], list)
            # 倒序：自己这条刚写，应排在最前
            self.assertEqual(items[0]["task_id"], r["task_id"])
        finally:
            if os.path.exists(fp):
                os.remove(fp)


    def test_预算用尽_留最终回答机会_降级成功(self):
        # P3.1：8 次工具用完后不再硬失败，给一次无工具收口；
        # 凭已有真实证据出 JSON → succeeded 但标 degraded，不伪造。
        c = self.contract(kind="file_discovery",
                          expected_schema="file_list_v1")
        # 第 1 个响应：单轮并行 8 个 read_file（H01 式撒网）
        parallel = {"choices": [{"message": {"role": "assistant", "content": "",
                      "tool_calls": [
                          _Msg.call("c%d" % i, "read_file",
                                    {"path": "src/auth.py"})["choices"][0]
                          ["message"]["tool_calls"][0]
                          for i in range(8)]}}]}
        final = '{"answer":"已读到认证文件","files":["src/auth.py"]}'
        p = _ScriptedProvider([
            parallel,
            _Msg.call("c9", "read_file", {"path": "src/auth.py"}),
            _Msg.say(final),
        ])
        r = run_delegation(c, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: p, persist=False)
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertTrue(r["degraded"])
        self.assertEqual(r["tool_calls"], 8)
        self.assertEqual(r["model_requests"], 3)
        self.assertTrue(any(e["category"] == "tool_limit" for e in r["errors"]))
        # 同参重复读命中任务内缓存
        self.assertGreaterEqual(r["cache_hits"], 1)

    def test_分段计时字段存在(self):
        r, _ = self._run([
            _Msg.say('{"fields":{"name":"张三"}}'),
        ], use_contract=False)
        # use_contract=False 实际仍给 bug 合同；这里只验计时键齐全
        for k in ("model_seconds", "tool_seconds", "validation_seconds"):
            self.assertIn(k, r)


class TestRouter(_Workspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from delegation import router
        self.router = router

    def test_授权目录内的简单查询_本地直答(self):
        d = self.router.classify(
            "这个项目配置里的重试次数是多少？在哪个文件里？工作区在 %s"
            % self.ws, allowed_roots=[self.tmp])
        self.assertEqual(d["route"], "local_direct", d)
        self.assertEqual(d["workspace"], os.path.abspath(self.ws))
        self.assertEqual(d["kind"], "file_discovery")

    def test_实时信息_即使带路径也走云端(self):
        d = self.router.classify(
            "%s 这个目录所在城市现在天气怎么样？" % self.ws,
            allowed_roots=[self.tmp])
        self.assertEqual(d["route"], "cloud_direct")
        self.assertEqual(d["reason"], "realtime_or_external_intent")

    def test_多步根因分析_委派链路(self):
        d = self.router.classify(
            "程序报错，分析 %s 里的日志找根因，后面都是连锁反应" % self.ws,
            allowed_roots=[self.tmp])
        self.assertEqual(d["route"], "hybrid_delegate")

    def test_无授权路径_不猜测_走云端(self):
        d = self.router.classify("配置里重试次数是多少？",
                                 allowed_roots=[self.tmp])
        self.assertEqual(d["route"], "cloud_direct")
        self.assertEqual(d["reason"], "no_authorized_path_in_text")

    def test_授权根外路径_不认(self):
        other = tempfile.mkdtemp(prefix="dlg_route_out_")
        try:
            d = self.router.classify(
                "%s 里重试次数是多少？" % other, allowed_roots=[self.tmp])
            self.assertEqual(d["route"], "cloud_direct")
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_长句歧义_保守走云端(self):
        d = self.router.classify(
            "在 %s 里帮我看看" % self.ws + "细节" * 200,
            allowed_roots=[self.tmp])
        self.assertEqual(d["route"], "cloud_direct")


class TestGateAndBridge(_Workspace, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import runtime
        self._old = dict(runtime.API_CFG)
        import tools as tl
        self.tl = tl

    def tearDown(self):
        import runtime
        runtime.API_CFG.clear()
        runtime.API_CFG.update(self._old)
        super().tearDown()

    def _set_mode(self, mode, ol_model="m", enabled=True):
        import runtime
        runtime.API_CFG["model_mode"] = mode
        runtime.API_CFG["ollama"] = {"model": ol_model} if ol_model else {}
        # delegation 开关走 config.json；打桩成测试值
        from delegation import bridge
        bridge.load_delegation_config = lambda: {
            "enabled": enabled, "allowed_roots": [self.tmp]}

    def test_cloud模式工具表不含委派(self):
        self._set_mode("cloud")
        names = [t["function"]["name"]
                 for t in self.tl.all_specs(brain=object(), src="pc")]
        self.assertNotIn("delegate_to_local", names)

    def test_hybrid关闭开关也不含(self):
        self._set_mode("hybrid", enabled=False)
        names = [t["function"]["name"]
                 for t in self.tl.all_specs(brain=object(), src="pc")]
        self.assertNotIn("delegate_to_local", names)

    def test_hybrid开启才含(self):
        self._set_mode("hybrid")
        names = [t["function"]["name"]
                 for t in self.tl.all_specs(brain=object(), src="pc")]
        self.assertIn("delegate_to_local", names)

    def test_未配置本地模型不含(self):
        self._set_mode("hybrid", ol_model="")
        names = [t["function"]["name"]
                 for t in self.tl.all_specs(brain=object(), src="pc")]
        self.assertNotIn("delegate_to_local", names)

    def test_桥_cloud模式直接拒绝(self):
        self._set_mode("cloud")
        from delegation.bridge import delegate_to_local

        class Ctx:
            src = "pc"
        out = delegate_to_local(Ctx(), kind="file_discovery", goal="x",
                                workspace=self.ws)
        self.assertIn("未启用", out)

    def test_桥_合同非法返回原因不抛异常(self):
        self._set_mode("hybrid")
        from delegation.bridge import delegate_to_local

        class Ctx:
            src = "pc"
        out = delegate_to_local(Ctx(), kind="rm_everything", goal="x")
        self.assertIn("合同被拒绝", out)
        self.assertIn("kind_not_allowed", out)

    def test_桥_真委派跑通(self):
        self._set_mode("hybrid")
        from delegation import bridge
        final = '{"files":["src/auth.py","error.log"]}'
        # bridge 顶部 from .dispatcher import 绑定了名字，要打桩 bridge 自己的
        old = bridge.run_delegation
        bridge.run_delegation = lambda contract, **k: {
            "task_id": "dlg_x", "status": "succeeded",
            "model_requests": 2, "tool_calls": 1, "elapsed_seconds": 3,
            "output": final,
            "evidence": {"existing": ["src/auth.py"], "missing": []},
            "needs_main_agent_review": False, "errors": []}
        try:
            class Ctx:
                src = "pc"
            out = bridge.delegate_to_local(
                Ctx(), kind="file_discovery", goal="列文件",
                workspace=self.ws)
        finally:
            bridge.run_delegation = old
        self.assertIn("succeeded", out)
        self.assertIn("src/auth.py", out)

    def test_local_direct命中并跳过云端(self):
        self._set_mode("hybrid")
        from delegation import bridge
        old_cfg = bridge.load_delegation_config
        old_run = bridge.run_delegation
        bridge.load_delegation_config = lambda: {
            "enabled": True, "router_enabled": True,
            "allowed_roots": [self.tmp]}
        bridge.run_delegation = lambda contract, **k: {
            "task_id": "dlg_d", "kind": "file_discovery",
            "status": "succeeded", "verified": True, "degraded": False,
            "model_requests": 2, "tool_calls": 2, "elapsed_seconds": 5,
            "output": "",
            "structured": {"answer": "重试次数 3，在 config/app.conf",
                           "files": ["config/app.conf"]},
            "evidence": {"verified": [{"path": "config/app.conf",
                                       "line": 1,
                                       "excerpt": "retry_count = 3"}]},
            "errors": []}
        try:
            r = bridge.run_local_direct(
                "%s 里的重试次数是多少？在哪个文件？" % self.ws, src="pc")
        finally:
            bridge.load_delegation_config = old_cfg
            bridge.run_delegation = old_run
        self.assertTrue(r["ok"], r)
        self.assertIn("3", r["reply"])
        self.assertEqual(r["decision"]["route"], "local_direct")

    def test_local_direct路由关闭_回云端(self):
        self._set_mode("hybrid")
        from delegation import bridge
        old_cfg = bridge.load_delegation_config
        bridge.load_delegation_config = lambda: {
            "enabled": True, "router_enabled": False,
            "allowed_roots": [self.tmp]}
        try:
            r = bridge.run_local_direct(
                "%s 里的重试次数是多少？" % self.ws, src="pc")
        finally:
            bridge.load_delegation_config = old_cfg
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "router_disabled")


class TestKindInference(unittest.TestCase):
    """D1：Planner 字符串入口的 kind 推断。"""

    def setUp(self):
        from delegation import router
        self.router = router

    def test_kind映射(self):
        k = self.router.kind_for_goal
        self.assertEqual(k("分析日志找第一个错误和连锁反应"), "log_root_cause")
        self.assertEqual(k("跨文件查 KeyError 根因，uid 字段对不上"),
                         "read_only_bug_analysis")
        self.assertEqual(k("帮我列修复步骤计划，先别动代码"), "plan_draft")
        self.assertEqual(k("这个项目里重试次数配置在哪个文件"), "file_discovery")


def _ok_delegation():
    """一个经 _render 成功渲染的最小委派结果。"""
    return {
        "task_id": "dlg_t", "kind": "log_root_cause", "status": "succeeded",
        "model_requests": 2, "tool_calls": 3, "elapsed_seconds": 4,
        "verified": True, "degraded": False,
        "structured": {"answer": "根因是 crypto 模块缺失",
                       "evidence": [{"path": "build.log", "line": 39,
                                     "excerpt": "No module named crypto",
                                     "observation": "首个 error"}]},
        "evidence": {"verified": [{"path": "build.log"}]},
        "needs_main_agent_review": False, "generic": False, "errors": []}


class TestD1PlannerDispatch(_Workspace, unittest.TestCase):
    """D1：tools.run_tool（Planner/文本标签链路）与 invoke_spec（FC）
    必须共用 delegation.bridge 同一道门。"""

    def setUp(self):
        super().setUp()
        import runtime
        self._old = dict(runtime.API_CFG)
        runtime.API_CFG.clear()
        runtime.API_CFG.update(self._old)
        import tools as tl
        self.tl = tl

    def tearDown(self):
        import runtime
        runtime.API_CFG.clear()
        runtime.API_CFG.update(self._old)
        super().tearDown()

    def _hybrid(self):
        import runtime
        runtime.API_CFG["model_mode"] = "hybrid"
        runtime.API_CFG["ollama"] = {"model": "m"}
        from delegation import bridge
        bridge.load_delegation_config = lambda: {
            "enabled": True, "router_enabled": True,
            "allowed_roots": [self.tmp]}

    def test_cloud模式_字符串委派入口返回None(self):
        import runtime
        runtime.API_CFG["model_mode"] = "cloud"
        runtime.API_CFG["ollama"] = {"model": "m"}
        out = self.tl.run_tool(
            "delegate_to_local",
            "分析 %s 里的日志找根因" % self.ws, src="pc")
        self.assertIsNone(out)

    def test_无授权路径_返回None让Planner改道(self):
        self._hybrid()
        out = self.tl.run_tool(
            "delegate_to_local", "分析日志找根因（没给路径）", src="pc")
        self.assertIsNone(out)

    def test_授权根内多步任务_与FC同入口执行并回传verified结果(self):
        self._hybrid()
        from delegation import bridge
        old = bridge.run_delegation
        seen = {}

        def fake_run(contract, **kw):
            seen["kind"] = contract["kind"]
            seen["workspace"] = contract["workspace"]
            return _ok_delegation()
        bridge.run_delegation = fake_run
        try:
            out = self.tl.run_tool(
                "delegate_to_local",
                "分析 %s 里的构建日志，找连锁报错的根因" % self.ws, src="pc")
        finally:
            bridge.run_delegation = old
        self.assertIsNotNone(out)
        self.assertIn("succeeded", out)
        self.assertIn("crypto", out)
        self.assertEqual(seen["kind"], "log_root_cause")
        self.assertEqual(seen["workspace"], os.path.abspath(self.ws))

    def test_委派失败_错误类别经同一入口返回(self):
        self._hybrid()
        from delegation import bridge
        old = bridge.run_delegation
        bridge.run_delegation = lambda contract, **kw: {
            "task_id": "dlg_f", "status": "failed",
            "model_requests": 6, "tool_calls": 8, "elapsed_seconds": 10,
            "structured": None, "output": "",
            "evidence": {}, "errors": [{"category": "tool_limit",
                                         "detail": "x"}]}
        try:
            out = self.tl.run_tool(
                "delegate_to_local",
                "分析 %s 里的日志找根因" % self.ws, src="pc")
        finally:
            bridge.run_delegation = old
        self.assertIn("tool_limit", out)
        self.assertIn("未成功", out)

    def test_planner执行委派_步骤描述并入目标以识别工作区(self):
        """execute_plan 调 delegate 时把 step_desc 拼进 arg（路径常在步骤里）。"""
        self._hybrid()
        import tools as tl
        import planner
        old_run = tl.run_tool
        captured = {}

        def fake_run(tool, arg, src=None):
            captured["tool"] = tool
            captured["arg"] = arg
            return "【本地子任务结果 task=t status=succeeded】结论 ok"
        tl.run_tool = fake_run
        try:
            # all_specs 在 hybrid+门控下含 delegate，permitted 才放行
            steps = [("在 %s 工作区分析日志找首个错误" % self.ws,
                      "delegate_to_local", None),
                     ("整理回答", None, None)]
            results = planner.execute_plan(
                steps, src="pc",
                allowed_tools=["delegate_to_local"], brain=object())
        finally:
            tl.run_tool = old_run
        self.assertEqual(captured["tool"], "delegate_to_local")
        self.assertIn(self.ws, captured["arg"])
        # 第一步拿到结果字符串而非 None
        self.assertTrue(results[0][3])

    def test_规划前确定性委派入口_成功回证据失败回退(self):
        """D1：run_hybrid_delegation_direct 是 HTTP 规划链路的截获点。"""
        self._hybrid()
        from delegation import bridge
        old = bridge.run_delegation
        # 成功：授权根内多步勘察 → 回证据文本
        bridge.run_delegation = lambda contract, **kw: _ok_delegation()
        try:
            r = bridge.run_hybrid_delegation_direct(
                "分析 %s 里的构建日志找连锁报错的根因" % self.ws, src="pc")
        finally:
            bridge.run_delegation = old
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["decision"]["route"], "hybrid_delegate")
        self.assertIn("crypto", r["evidence_text"])
        # 简单查询不被该入口截获（留给 local_direct）
        r2 = bridge.run_hybrid_delegation_direct(
            "%s 里重试次数是多少？在哪个文件？" % self.ws, src="pc")
        self.assertFalse(r2["ok"])
        # 委派失败 → ok=False，调用方回退规划链路（不吞错）
        bridge.run_delegation = lambda contract, **kw: {
            "task_id": "x", "status": "failed", "errors": [
                {"category": "tool_limit"}], "evidence": {}}
        try:
            r3 = bridge.run_hybrid_delegation_direct(
                "分析 %s 里的日志找根因" % self.ws, src="pc")
        finally:
            bridge.run_delegation = old
        self.assertFalse(r3["ok"])
        self.assertEqual(r3["reason"], "delegation_failed")


class TestD2PCRouting(_Workspace, unittest.TestCase):
    """D2：PC 在线时授权路径优先 local_file_*（路由逻辑，非提示词）。"""

    def setUp(self):
        super().setUp()
        import localtools
        import runtime
        self.lt = localtools
        self._old_poll = localtools._LAST_POLL
        self._old_cfg = dict(runtime.API_CFG)
        runtime.API_CFG["model_mode"] = "hybrid"
        runtime.API_CFG["ollama"] = {"model": "m"}
        from delegation import bridge
        self.bridge = bridge
        bridge.load_delegation_config = lambda: {
            "enabled": True, "router_enabled": True,
            "allowed_roots": [self.tmp]}

    def tearDown(self):
        import time as _t
        self.lt._LAST_POLL = self._old_poll
        self.bridge.lock_delegation(False)
        import runtime
        runtime.API_CFG.clear()
        runtime.API_CFG.update(self._old_cfg)
        super().tearDown()

    def test_pc_online新鲜期判定(self):
        import time
        self.lt._LAST_POLL = time.time()
        self.assertTrue(self.lt.pc_online())
        self.lt._LAST_POLL = 0.0
        self.assertFalse(self.lt.pc_online())

    def test_越权工作区_PC在线_重定向到local_file(self):
        import time
        self.lt._LAST_POLL = time.time()
        other = tempfile.mkdtemp(prefix="dlg_d2_out_")
        try:
            class Ctx:
                src = "pc"
            out = self.bridge.delegate_to_local(
                Ctx(), kind="log_root_cause", goal="分析日志",
                workspace=other)
        finally:
            shutil.rmtree(other, ignore_errors=True)
        self.assertIn("local_file", out)
        self.assertIn("不要再调用 delegate_to_local", out)

    def test_越权工作区_PC离线_普通拒绝不给重定向(self):
        self.lt._LAST_POLL = 0.0
        other = tempfile.mkdtemp(prefix="dlg_d2_off_")
        try:
            class Ctx:
                src = "pc"
            out = self.bridge.delegate_to_local(
                Ctx(), kind="log_root_cause", goal="分析日志",
                workspace=other)
        finally:
            shutil.rmtree(other, ignore_errors=True)
        self.assertIn("workspace_not_authorized", out)
        self.assertNotIn("local_file", out)

    def test_工具表描述随PC在线状态变化(self):
        import time
        import tools as tl
        self.lt._LAST_POLL = 0.0
        off = [t for t in tl.all_specs(brain=object(), src="pc")
               if t["function"]["name"] == "delegate_to_local"][0]
        self.assertNotIn("PC 客户端在线", off["function"]["description"])
        self.lt._LAST_POLL = time.time()
        on = [t for t in tl.all_specs(brain=object(), src="pc")
              if t["function"]["name"] == "delegate_to_local"][0]
        self.assertIn("PC 客户端在线", on["function"]["description"])
        self.assertIn("local_file", on["function"]["description"])

    def test_路由锁_工具表整项摘除而非描述劝退(self):
        """D2 硬门控：上锁后工具表里根本没有 delegate_to_local。"""
        import tools as tl
        names = lambda: [t["function"]["name"]
                         for t in tl.all_specs(brain=object(), src="pc")]
        self.assertIn("delegate_to_local", names())
        self.bridge.lock_delegation(True)
        try:
            self.assertNotIn("delegate_to_local", names())
        finally:
            self.bridge.lock_delegation(False)
        self.assertIn("delegate_to_local", names())

    def test_路由锁_FC入口硬拒且一个委派都不发(self):
        """锁着时即使绕过工具表（XML 文本兜底）调 invoke_spec，
        delegate_to_local 也必须硬拒改道，run_delegation 不被调用。"""
        import tools as tl

        class Ctx:
            src = "pc"
        called = []
        old = self.bridge.run_delegation
        self.bridge.run_delegation = (
            lambda contract, **k: called.append(contract) or _ok_delegation())
        self.bridge.lock_delegation(True)
        try:
            out = tl.invoke_spec(
                "delegate_to_local",
                {"kind": "log_root_cause", "goal": "分析桌面日志",
                 "workspace": self.tmp}, ctx=Ctx())
        finally:
            self.bridge.lock_delegation(False)
            self.bridge.run_delegation = old
        self.assertEqual(called, [])
        self.assertIn("local_file", out)
        self.assertIn("不要再调用 delegate_to_local", out)

    def test_路由锁_Planner字符串入口返回None(self):
        """run_tool（Planner/文本标签链路）与 FC 入口同锁。"""
        import tools as tl
        self.bridge.lock_delegation(True)
        try:
            out = tl.run_tool(
                "delegate_to_local",
                "分析 %s 里的日志找根因" % self.ws, src="pc")
        finally:
            self.bridge.lock_delegation(False)
        self.assertIsNone(out)


class TestD3JsonRepair(_Workspace, unittest.TestCase):
    """D3：有限 JSON 修复 + 原始响应/失败原因留档，不增重试不提预算。"""

    def _run(self, responses, contract=None):
        """与 TestDispatcher._run 同构的脚本化 provider 执行。"""
        p = contract or self.contract()

        class P:
            def __init__(self, rs):
                self.rs = list(rs)
                self.calls = 0

            def ready(self):
                return True

            def chat(self, payload, timeout=None):
                i = min(self.calls, len(self.rs) - 1)
                self.calls += 1
                return self.rs[i]
        prov = P(responses)
        r = run_delegation(p, ollama_cfg={"model": "m"},
                           provider_factory=lambda cfg: prov,
                           persist=False)
        return r, prov

    def test_机械修复只做语义无损处理(self):
        from delegation.dispatcher import _repair_json_text
        fixed, how = _repair_json_text(
            '好的，结果是：\n```json\n{"fields": {"name": "张三"},}\n```')
        self.assertIsNotNone(fixed)
        self.assertIn("fence", how)
        self.assertIn("trailing_comma", how)
        import json
        self.assertEqual(json.loads(fixed)["fields"]["name"], "张三")

    def test_纯文本无JSON_不强行修复(self):
        from delegation.dispatcher import _repair_json_text
        fixed, why = _repair_json_text("我读不到文件，没法给 JSON")
        self.assertIsNone(fixed)
        self.assertTrue(why)

    def test_围栏尾逗号输出_零模型修复即成功并标记(self):
        final = '```json\n{"fields": {"name": "张三"},}\n```'
        c = self.contract(kind="structured_extraction", expected_schema="extraction_v1")
        r, _ = self._run([_Msg.say(final)], c)
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertTrue(r["json_repaired"])
        self.assertEqual(r["structured"]["fields"]["name"], "张三")
        # 只用了 1 个模型请求（修复零调用），预算没有被提高
        self.assertEqual(r["model_requests"], 1)

    def test_始终非JSON_保留原始输出与原因_不超过一次重试(self):
        c = self.contract(kind="structured_extraction", expected_schema="extraction_v1")
        r, _ = self._run([_Msg.say("不是 JSON 哦"),
                          _Msg.say("我还是不想输出 JSON")], c)
        self.assertEqual(r["status"], "failed")
        self.assertFalse(r["json_repaired"])
        self.assertTrue(r["json_repair"].startswith("unrepairable"))
        self.assertEqual(r["last_raw"], "我还是不想输出 JSON")
        # 首答 + 唯一一次修正 = 2 次请求，绝不更多
        self.assertEqual(r["model_requests"], 2)
        cats = [e["category"] for e in r["errors"]]
        self.assertIn("validation_failed", cats)
        # 重试轮的验收问题也留了档
        self.assertTrue(r["retry_problems"])


if __name__ == "__main__":
    unittest.main()


