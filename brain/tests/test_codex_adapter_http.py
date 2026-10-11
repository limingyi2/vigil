"""Actual Brain HTTP handler, isolated from runtime/production module imports."""
import ast
import contextlib
import hmac
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import codex_adapter


class BrainHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vigil-p1-http-")
        cls.center = codex_adapter.CodexAdapter(cls.temp.name, auto_reconnect=False)
        cls.old = codex_adapter._adapter
        codex_adapter._adapter = cls.center
        source = Path(__file__).resolve().parents[1] / "server.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {"start_remote_api", "_auth_ok", "_is_loopback", "_device_path_allowed"}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        constants = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("DEVICE_") for t in n.targets)]
        ns = {"http": http, "os": os, "json": json, "hmac": hmac, "threading": threading,
              "api_config": {"bind_host": "127.0.0.1"}, "MOB_CFG": {}, "BRAIN_TOKEN": "p1-http-test-token",
              "MAX_BODY": 1024 * 1024, "note_client_ip": lambda ip: None}
        cls.namespace = ns
        exec(compile(ast.Module(body=constants + nodes, type_ignores=[]), str(source), "exec"), ns)
        with patch.dict(os.environ, {"VIGIL_PORT": "0"}), contextlib.redirect_stdout(io.StringIO()):
            cls.server = ns["start_remote_api"](None)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        cls.center.close()
        codex_adapter._adapter = cls.old
        cls.temp.cleanup()

    def call(self, method, path, token="p1-http-test-token", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()

    def test_status_real_http_route(self):
        code, result = self.call("GET", "/api/agent-center/status")
        self.assertEqual(code, 200)
        self.assertTrue(result["approval_response_enabled"])
        self.assertEqual(result["connection_status"], "disconnected")
        code, approvals = self.call("GET", "/api/agent-center/approvals")
        self.assertEqual(code, 200)
        self.assertTrue(approvals["response_enabled"])

    def test_bad_auth_cannot_read(self):
        self.assertEqual(self.call("GET", "/api/agent-center/tasks", token="wrong")[0], 401)

    def test_non_loopback_admin_rejected_by_actual_http_gate(self):
        with patch.dict(self.namespace, {"_is_loopback": lambda ip: False}):
            code, result = self.call("GET", "/api/agent-center/status")
        self.assertEqual((code, result["err"]), (403, "local_admin_required"))

    def test_limited_device_rejected_by_actual_http_gate(self):
        import sys
        from types import SimpleNamespace
        with patch.dict(self.namespace, {"_device_role": lambda _: "device"}), patch.dict(sys.modules, {
                "devices": SimpleNamespace(verify=lambda *args: {"device_id": "isolated-device"})}):
            code, result = self.call("GET", "/api/agent-center/status", token="dt_isolated-fixture")
        self.assertEqual(code, 403)
        self.assertIn("管理员", result["err"])

    def test_unknown_approval_route_is_rejected(self):
        code, result = self.call("POST", "/api/agent-center/approvals/arbitrary/respond", body={"action": "decline"})
        self.assertEqual((code, result["err"]), (404, "agent_center_route_missing"))
        self.assertEqual(self.center.requests, 0)

    def test_invalid_cursor_and_unknown_route(self):
        self.assertEqual(self.call("GET", "/api/agent-center/events?after=bad")[0], 400)
        self.assertEqual(self.call("GET", "/api/agent-center/no-route")[0], 404)


if __name__ == "__main__":
    unittest.main()
