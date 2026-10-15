"""Native Codex App Server, isolated home and authenticated loopback model fixture.

Zero hosted-model requests. Skip only when the Codex executable is absent.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from codex_adapter import CodexAdapter


class ModelFixture:
    def __init__(self):
        self.mode = "normal"
        self.requests = 0
        self.secret = secrets.token_urlsafe(32)
        self.command_emitted = False
        self.command_output = ""
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer " + owner.secret:
                    self.send_response(401); self.end_headers(); return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                for entry in body.get("input", []):
                    if isinstance(entry, dict) and entry.get("type") == "function_call_output":
                        owner.command_output = str(entry.get("output", ""))
                owner.requests += 1
                mode = owner.mode
                if mode == "failure":
                    self.send_response(400); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(b'{"error":{"message":"VIGIL_P1_FIXTURE_ERROR","type":"invalid_request_error"}}')
                    return
                time.sleep(.2)
                response_id = "resp_p1_" + str(owner.requests)
                if mode == "approval" or (mode == "command" and not owner.command_emitted):
                    owner.command_emitted = True
                    command = {"cmd": "Write-Output VIGIL_P1_TOOL_OK"}
                    if mode == "approval":
                        command = {"cmd": "Write-Output VIGIL_P1_APPROVAL", "sandbox_permissions": "require_escalated", "justification": "Deterministic isolated protocol probe; no approval reply."}
                    args = json.dumps(command)
                    item = {"type": "function_call", "id": "fc_p1", "call_id": "call_p1", "name": "exec_command", "arguments": args, "status": "completed"}
                    middle = [
                        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": "", "status": "in_progress"}},
                        {"type": "response.function_call_arguments.delta", "item_id": "fc_p1", "output_index": 0, "delta": args},
                        {"type": "response.function_call_arguments.done", "item_id": "fc_p1", "output_index": 0, "arguments": args},
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                    ]
                else:
                    text = "VIGIL_P1_FIXTURE_DONE"
                    content = {"type": "output_text", "text": text, "annotations": []}
                    item = {"type": "message", "id": "msg_p1", "role": "assistant", "status": "completed", "content": [content]}
                    middle = [
                        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": [], "status": "in_progress"}},
                        {"type": "response.content_part.added", "item_id": "msg_p1", "output_index": 0, "content_index": 0, "part": {**content, "text": ""}},
                        {"type": "response.output_text.delta", "item_id": "msg_p1", "output_index": 0, "content_index": 0, "delta": text},
                        {"type": "response.output_text.done", "item_id": "msg_p1", "output_index": 0, "content_index": 0, "text": text},
                        {"type": "response.content_part.done", "item_id": "msg_p1", "output_index": 0, "content_index": 0, "part": content},
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                    ]
                response = {"id": response_id, "object": "response", "created_at": int(time.time()), "status": "in_progress", "output": []}
                events = [{"type": "response.created", "response": response}, *middle,
                          {"type": "response.completed", "response": {**response, "status": "completed", "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}]
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                try:
                    for event in events:
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode()); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def make_adapter(self, root, auto_reconnect=True):
        root = Path(root)
        home = root / "codex-home"
        home.mkdir(exist_ok=True)
        # Auth preflight uses only this synthetic loopback fixture key and isolated home.
        # No production login or account credentials participate in native regressions.
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        subprocess.run([shutil.which("codex"), "login", "--with-api-key"],
                       input=self.secret.encode(), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env, timeout=10, check=True, **flags)
        args = ["-c", 'model_provider="p1_fixture"',
                "-c", 'model_providers.p1_fixture.name="P1 fixture"',
                "-c", 'model_providers.p1_fixture.base_url="http://127.0.0.1:%d/v1"' % self.server.server_port,
                "-c", 'model_providers.p1_fixture.wire_api="responses"',
                "-c", 'model_providers.p1_fixture.env_key="VIGIL_P1_FIXTURE_KEY"',
                "-c", "model_providers.p1_fixture.requires_openai_auth=false",
                "-c", "model_providers.p1_fixture.request_max_retries=0",
                "-c", "model_providers.p1_fixture.stream_max_retries=0"]
        return CodexAdapter(root / "center", home=home, launch_args=args,
                            environment={"VIGIL_P1_FIXTURE_KEY": self.secret},
                            auto_reconnect=auto_reconnect, request_timeout=15)

    def close(self):
        self.server.shutdown(); self.server.server_close()


def wait_for(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("native protocol test timed out")


@unittest.skipUnless(shutil.which("codex"), "Codex CLI not installed")
class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-p1-native-")
        self.fixture = ModelFixture()
        self.center = self.fixture.make_adapter(self.temp.name)

    def tearDown(self):
        self.center.close(); self.fixture.close(); self.temp.cleanup()

    def run_task(self, mode):
        self.fixture.mode = mode
        task = self.center.create_task()
        self.center.start_turn(task["id"], "Bounded local protocol fixture.")
        return task["id"]

    def test_normal_running_and_failed_turn(self):
        task_id = self.run_task("normal")
        wait_for(lambda: self.center.get_task(task_id)["status"] == "completed")
        events = self.center.event_log()["items"]
        self.assertTrue(any(e.get("state") == "running" for e in events))
        failed = self.run_task("failure")
        wait_for(lambda: self.center.get_task(failed)["status"] == "failed")
        self.assertTrue(any(e["method"] == "error" for e in self.center.event_log()["items"]))
        self.assertEqual(self.fixture.requests, 2)

    def test_native_command_never_bypasses_required_approval(self):
        task_id = self.run_task("command")
        wait_for(lambda: self.center.get_task(task_id)["status"] in ("completed", "waiting_approval"))
        task = self.center.get_task(task_id)
        if task["status"] == "waiting_approval":
            self.assertEqual(self.fixture.requests, 1)
            self.assertTrue(self.center.approvals())
            self.assertFalse(self.fixture.command_output)
            self.center.interrupt(task_id)
            wait_for(lambda: self.center.get_task(task_id)["status"] == "interrupted")
        else:
            self.assertEqual(task["tool_failures"], 0, self.fixture.command_output[:700])
            self.assertIn("VIGIL_P1_TOOL_OK", self.fixture.command_output)

    def test_approval_dedup_process_exit_and_automatic_reconnect(self):
        task_id = self.run_task("approval")
        wait_for(lambda: self.center.get_task(task_id)["status"] == "waiting_approval")
        pending = wait_for(lambda: self.center.approvals())
        request = pending[0]
        native_duplicate = {"id": request["request_id"], "method": request["method"], "params": {"threadId": request["thread_id"], "turnId": request["turn_id"], "itemId": request["item_id"], "startedAtMs": 1}}
        self.center.handle_event(native_duplicate, self.center.generation)
        self.center.handle_event(native_duplicate, self.center.generation)
        self.assertEqual(len(self.center.approvals()), 1)
        generation = self.center.generation
        self.center.process.kill()
        wait_for(lambda: self.center.status()["connection_status"] == "disconnected")
        self.assertEqual(self.center.approvals()[0]["state"], "expired")
        wait_for(lambda: self.center.status()["connection_status"] == "connected" and self.center.generation != generation)
        wait_for(lambda: self.center.get_task(task_id)["connection_generation"] == self.center.generation)
        self.assertFalse(any(a["state"] == "pending" for a in self.center.approvals()))
        task = self.center.get_task(task_id)
        self.assertEqual((task["status"], task["source"], task["live"]), ("unknown", "history", False))
        self.fixture.mode = "normal"
        self.center.start_turn(task_id, "New local fixture turn after explicit recovery.")
        wait_for(lambda: self.center.get_task(task_id)["status"] == "completed")
        self.assertEqual(self.fixture.requests, 2)  # no replay of the lost turn


if __name__ == "__main__":
    unittest.main()
