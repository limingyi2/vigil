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


class TestValidator(_Workspace, unittest.TestCase):
    def test_bug分析_证据真实(self):
        text = '{"root_cause":"None 传入","file":"src/auth.py",' \
               '"line":2,"reason":"login 未判空"}'
        events = [{"tool": "read_file", "path": "src/auth.py", "ok": True}]
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       text, self.ws, tool_events=events)
        self.assertTrue(v["ok"], v["problems"])
        self.assertEqual(v["structured"]["root_cause"], "None 传入")

    def test_引用不存在文件_invalid_evidence(self):
        text = '{"root_cause":"x","file":"src/ghost.py","reason":"y"}'
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       text, self.ws,
                       tool_events=[{"tool": "read_file",
                                     "path": "src/ghost.py"}])
        self.assertFalse(v["ok"])
        self.assertTrue(any("证据" in p or "不存在" in p or "工具" in p
                            for p in v["problems"]))

    def test_无工具调用的分析不算完成(self):
        v = V.validate("read_only_bug_analysis", "bug_analysis_v1",
                       '{"root_cause":"x","file":"src/auth.py","reason":"y"}',
                       self.ws, tool_events=[])
        self.assertFalse(v["ok"])

    def test_抽JSON失败(self):
        v = V.validate("structured_extraction", "extraction_v1",
                       "我觉得是张三，但没按 JSON 输出", "")
        self.assertFalse(v["ok"])
        self.assertTrue(any("JSON" in p for p in v["problems"]))

    def test_file_list证据校验(self):
        text = '{"files":["src/auth.py","src/missing.py"]}'
        v = V.validate("file_discovery", "file_list_v1", text, self.ws,
                       tool_events=[{"tool": "file_find"}])
        self.assertFalse(v["ok"])
        self.assertIn("src/missing.py", v["evidence"]["missing"])

    def test_plan永远待主模型审核(self):
        text = '{"steps":["读日志","定位","修复建议"]}'
        v = V.validate("plan_draft", "plan_draft_v1", text, "")
        self.assertTrue(v["ok"])
        self.assertTrue(v["needs_main_agent_review"])


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
        final = '{"root_cause":"None 被传入 issue_token","file":"src/auth.py",' \
                '"line":2,"reason":"login 未判空"}'
        r, p = self._run([
            _Msg.call("c1", "read_file", {"path": "src/auth.py"}),
            _Msg.say(final),
        ])
        self.assertEqual(r["status"], "succeeded", r["errors"])
        self.assertEqual(r["tool_calls"], 1)
        self.assertEqual(r["structured"]["file"], "src/auth.py")
        self.assertIn("src/auth.py", r["evidence"]["existing"])
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

    def test_验收失败重试一次后通过(self):
        bad = "我觉得是个问题"            # 不是 JSON
        good = '{"root_cause":"None 传入","file":"src/auth.py",' \
               '"line":2,"reason":"未判空"}'
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
        # file_discovery 期待 file_list_v1
        final = '{"files":["src/auth.py","error.log"]}'
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
            _Msg.say('{"files":["src/auth.py"]}'),
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


if __name__ == "__main__":
    unittest.main()

