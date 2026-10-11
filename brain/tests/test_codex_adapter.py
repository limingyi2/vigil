import copy
import json
from pathlib import Path
import tempfile
import unittest

from codex_adapter import AdapterError, CodexAdapter, handle_http


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-p1-unit-")
        self.center = CodexAdapter(self.temp.name, auto_reconnect=False)
        self.center.generation = "generation-1"
        self.center.connection_status = "connected"
        self.task_id = "a" * 32
        self.center.tasks[self.task_id] = {
            "id": self.task_id, "thread_id": "owned-thread", "ownership": "owned",
            "connection_generation": self.center.generation, "workspace": str(self.center._workspace(self.task_id)),
            "status": "idle", "source": "realtime", "live": True,
            "turns": {}, "current_turn": None, "tool_failures": 0,
        }

    def tearDown(self):
        self.center.close()
        self.temp.cleanup()

    def event(self, method, **params):
        return {"method": method, "params": {"threadId": "owned-thread", **params}}

    def send(self, event, generation=None):
        return self.center.handle_event(event, generation or self.center.generation)

    def start(self, turn="turn-1"):
        self.center.tasks[self.task_id]["turn_request_generation"] = self.center.generation
        self.send(self.event("turn/started", turn={"id": turn, "status": "inProgress"}))

    def approval(self, request=0, turn="turn-1", item="item-1"):
        msg = self.event("item/commandExecution/requestApproval", turnId=turn, itemId=item,
                         command="SECRET-COMMAND", startedAtMs=1)
        msg["id"] = request
        return msg

    def test_running_and_normal_completion(self):
        self.start()
        self.assertEqual(self.center.get_task(self.task_id)["status"], "running")
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
        task = self.center.get_task(self.task_id)
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["turns"]["turn-1"]["source"], "realtime")
        self.assertIn("finished_at", task)

    def test_waiting_approval_and_duplicate_payload_variation(self):
        self.start()
        msg = self.approval()
        self.assertTrue(self.send(msg))
        self.assertFalse(self.send(msg))
        modified = copy.deepcopy(msg)
        modified["params"]["command"] = "changed duplicate payload"
        self.assertFalse(self.send(modified))
        self.assertEqual(len(self.center.approvals()), 1)
        self.assertEqual(self.center.get_task(self.task_id)["status"], "waiting_approval")
        pending = self.center.approvals()[0]
        self.assertEqual(pending["ownership"], "owned")
        self.assertFalse(pending["response_enabled"])
        self.assertNotIn("SECRET-COMMAND", (Path(self.temp.name) / "events.jsonl").read_text())
        self.assertNotIn("pending", json.loads((Path(self.temp.name) / "owned.json").read_text()))

    def test_failed_tool_does_not_fail_turn(self):
        self.start()
        tool = self.event("item/completed", turnId="turn-1", item={"id": "tool-1", "type": "commandExecution", "status": "failed", "exitCode": 7})
        self.send(tool)
        self.send(tool)
        self.assertEqual(self.center.get_task(self.task_id)["tool_failures"], 1)
        self.assertEqual(self.center.get_task(self.task_id)["status"], "running")
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
        self.assertEqual(self.center.get_task(self.task_id)["status"], "completed")

    def test_native_error_and_failed_turn(self):
        self.start()
        self.send(self.event("error", turnId="turn-1", error={"message": "SECRET-ERROR"}))
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "failed", "error": {"message": "PRIVATE"}}))
        self.assertEqual(self.center.get_task(self.task_id)["status"], "failed")
        self.assertNotIn("SECRET-ERROR", (Path(self.temp.name) / "events.jsonl").read_text())

    def test_disconnect_expires_approval_and_rejects_old_generation(self):
        self.start()
        msg = self.approval()
        self.send(msg)
        old = self.center.generation
        self.center._disconnect_generation(old, "transport_closed")
        self.assertEqual(self.center.get_task(self.task_id)["status"], "unknown")
        self.assertFalse(self.center.get_task(self.task_id)["live"])
        self.assertEqual(self.center.approvals()[0]["state"], "expired")
        self.center.generation = "generation-2"
        self.assertFalse(self.send(msg, old))
        self.assertFalse(self.send(msg))  # task not rebound to generation 2
        self.assertEqual(len(self.center.approvals()), 1)

    def test_restarted_adapter_never_restores_pending(self):
        self.start()
        self.send(self.approval())
        restarted = CodexAdapter(self.temp.name, auto_reconnect=False)
        try:
            self.assertEqual(restarted.approvals(), [])
            task = restarted.get_task(self.task_id)
            self.assertEqual((task["status"], task["source"], task["live"]), ("unknown", "history", False))
            self.assertEqual(task["turns"]["turn-1"]["source"], "history")
        finally:
            restarted.close()

    def test_new_generation_request_id_reuse_has_new_identity(self):
        self.start()
        self.send(self.approval())
        old = self.center.approvals()[0]["id"]
        self.center._disconnect_generation(self.center.generation, "transport_closed")
        self.center.generation = "generation-2"
        self.center.connection_status = "connected"
        self.center.tasks[self.task_id]["connection_generation"] = self.center.generation
        self.start("turn-2")
        self.send(self.approval(turn="turn-2"))
        items = self.center.approvals()
        self.assertEqual(len(items), 2)
        self.assertNotEqual(old, items[1]["id"])
        self.assertEqual(items[0]["state"], "expired")

    def test_terminal_turn_never_recreates_pending(self):
        self.start()
        self.send(self.approval())
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "interrupted"}))
        changed = self.approval(item="different-item")
        self.assertFalse(self.send(changed))
        self.assertEqual(self.center.approvals()[0]["state"], "expired")

    def test_observed_never_owns_or_controls(self):
        msg = self.approval()
        msg["params"]["threadId"] = "desktop-thread"
        self.assertFalse(self.send(msg))
        self.assertEqual(self.center.approvals(), [])
        for call in [lambda: self.center.get_task("desktop-thread"),
                     lambda: self.center.interrupt("desktop-thread"),
                     lambda: self.center.start_turn("desktop-thread", "anything")]:
            with self.assertRaisesRegex(AdapterError, "owned_task_required"):
                call()

    def test_wrong_turn_or_unbound_request_ignored(self):
        self.start()
        self.assertFalse(self.send(self.approval(turn="unrelated-turn")))
        msg = self.approval(); del msg["params"]["itemId"]
        self.assertFalse(self.send(msg))
        self.assertEqual(self.center.approvals(), [])

    def test_resolved_only_matches_thread_and_typed_request_id(self):
        self.start()
        self.send(self.approval(0))
        self.send(self.approval("0", item="item-string"))
        self.send(self.event("serverRequest/resolved", requestId=0))
        self.assertEqual([a["state"] for a in self.center.approvals()], ["resolved", "pending"])

    def test_separate_turns_do_not_dedup_legitimate_waiting_state(self):
        waiting = self.event("thread/status/changed", status={"type": "active", "activeFlags": ["waitingOnApproval"]})
        self.start()
        self.assertTrue(self.send(waiting))
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
        self.start("turn-2")
        self.assertTrue(self.send(waiting))
        self.assertEqual(self.center.get_task(self.task_id)["status"], "waiting_approval")

    def test_timeout_and_responses_always_disabled(self):
        self.start()
        self.send(self.approval())
        self.center.pending[next(iter(self.center.pending))]["expires_at"] = 0
        self.assertEqual(self.center.approvals()[0]["state"], "expired")
        with self.assertRaisesRegex(AdapterError, "approval_response_disabled"):
            self.center.respond("anything", "accept")
        with self.assertRaisesRegex(AdapterError, "rpc_not_allowed"):
            self.center._rpc("approval/respond", {"decision": "decline"})
        with self.assertRaisesRegex(AdapterError, "server_response_disabled"):
            self.center._write({"id": 0, "result": {"decision": "accept"}}, self.center.generation)
        with self.assertRaisesRegex(AdapterError, "server_response_disabled"):
            self.center._write({"id": 0, "result": {"decision": "decline"}}, self.center.generation)

    def test_http_local_admin_and_response_disabled(self):
        for admin, local in [(False, True), (True, False), (False, False)]:
            self.assertEqual(handle_http("POST", "/api/agent-center/connect", is_admin=admin, is_local=local, adapter=self.center)[0], 403)
        for decision in ("accept", "decline"):
            code, result = handle_http("POST", "/api/agent-center/approvals/a/respond", {"decision": decision}, is_admin=True, is_local=True, adapter=self.center)
            self.assertEqual((code, result["err"]), (409, "approval_response_disabled"))
        self.assertEqual(handle_http("GET", "/api/agent-center/status", is_admin=True, is_local=True, adapter=self.center)[0], 200)

    def test_history_never_claims_live_status_or_resumes_observed(self):
        self.center._rpc = lambda method, params: {"thread": {"id": "desktop-thread", "status": {"type": "active"}, "turns": [{"id": "history-turn", "status": "inProgress"}]}}
        out = self.center.observe("desktop-thread")["items"][0]
        self.assertEqual((out["ownership"], out["status"], out["source"], out["live"]), ("observed", "unknown", "history", False))
        self.assertEqual(out["turns"]["history-turn"]["source"], "history")

    def test_stream_deltas_not_deduped_and_cursor(self):
        self.start()
        delta = self.event("item/agentMessage/delta", turnId="turn-1", delta="PRIVATE-TEXT")
        self.assertTrue(self.send(delta)); self.assertTrue(self.send(delta))
        log = self.center.event_log()
        self.assertEqual(len(log["items"]), 3)
        self.assertEqual(self.center.event_log(log["cursor"])["items"], [])
        self.assertNotIn("PRIVATE-TEXT", (Path(self.temp.name) / "events.jsonl").read_text())

    def test_registry_path_escape_fail_closed(self):
        self.center._save()
        raw = json.loads((Path(self.temp.name) / "owned.json").read_text())
        raw["tasks"][0]["id"] = "../outside"
        (Path(self.temp.name) / "owned.json").write_text(json.dumps(raw))
        restored = CodexAdapter(self.temp.name, auto_reconnect=False)
        self.assertEqual(restored.tasks, {})
        self.assertEqual(restored.last_error, "registry_invalid")
        restored.close()

    def test_late_previous_turn_completion_cannot_finish_current_turn(self):
        self.start()
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "completed"}))
        self.start("turn-2")
        self.send(self.event("turn/completed", turn={"id": "turn-1", "status": "failed"}))
        task = self.center.get_task(self.task_id)
        self.assertEqual((task["current_turn"], task["status"]), ("turn-2", "running"))

    def test_inherited_unsafe_permissions_are_rejected(self):
        for sandbox in ({"type": "dangerFullAccess"}, {"type": "readOnly", "networkAccess": True}):
            with self.assertRaisesRegex(AdapterError, "unexpected_codex_permissions"):
                self.center._check_permissions({"approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": sandbox})

    def test_replayed_old_turn_approval_after_resume_is_not_restored(self):
        self.start()
        self.send(self.approval())
        self.center._disconnect_generation(self.center.generation, "transport_closed")
        self.center.generation = "new-generation"
        task = self.center.tasks[self.task_id]
        task["connection_generation"] = self.center.generation
        self.center._apply_history(task, {"turns": [{"id": "turn-1", "status": "inProgress"}]})
        self.assertFalse(self.send(self.approval()))
        self.assertFalse(any(a["state"] == "pending" for a in self.center.approvals()))
        self.send(self.event("thread/status/changed", status={"type": "active", "activeFlags": ["waitingOnApproval"]}))
        self.assertFalse(self.send(self.event("turn/started", turn={"id": "turn-1", "status": "inProgress"})))
        self.assertEqual((task["status"], task["source"], task["live"]), ("unknown", "history", False))


if __name__ == "__main__":
    unittest.main()
