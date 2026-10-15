import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_adapter import CodexAdapter, SUMMARY_MAX_CHARS, SUMMARY_MAX_ITEMS, handle_http


class ReasoningSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-summary-")
        self.center = CodexAdapter(self.temp.name, auto_reconnect=False)
        self.center.connection_status = "connected"
        self.center.generation = "generation-1"
        self.task_id = "a" * 32
        self.center.tasks[self.task_id] = {
            "id": self.task_id, "thread_id": "owned-thread", "ownership": "owned",
            "connection_generation": self.center.generation, "workspace": str(self.center._workspace(self.task_id)),
            "status": "idle", "source": "realtime", "live": True, "turns": {},
            "current_turn": None, "tool_failures": 0, "turn_request_generation": self.center.generation,
        }
        self.start("turn-1")

    def tearDown(self):
        self.center.close()
        self.temp.cleanup()

    def send(self, method, **params):
        return self.center.handle_event({"method": method, "params": {"threadId": "owned-thread", **params}}, self.center.generation)

    def start(self, turn):
        self.send("turn/started", turn={"id": turn, "status": "inProgress"})

    def summary(self, delta=None, index=0, item="reasoning-1", **extra):
        method = "item/reasoning/summaryPartAdded" if delta is None else "item/reasoning/summaryTextDelta"
        params = {"turnId": "turn-1", "itemId": item, "summaryIndex": index, **extra}
        if delta is not None:
            params["delta"] = delta
        return self.send(method, **params)

    def text(self):
        summary = self.center.get_task(self.task_id)["reasoning_summary"]
        return "".join(p["text"] for item in summary["items"] for p in item["parts"])

    def test_parts_deltas_and_public_http_snapshot(self):
        self.summary()
        self.summary("先检查输入。")
        self.summary(index=1)
        self.summary("再确认结果。", index=1)
        summary = self.center.get_task(self.task_id)["reasoning_summary"]
        self.assertEqual(summary["turn_id"], "turn-1")
        self.assertEqual([p["text"] for p in summary["items"][0]["parts"]], ["先检查输入。", "再确认结果。"])
        for route in ("tasks", "tasks/" + self.task_id):
            code, body = handle_http("GET", "/api/agent-center/" + route, is_admin=True, is_local=True, adapter=self.center)
            self.assertEqual(code, 200)
            self.assertIn("先检查输入。", json.dumps(body, ensure_ascii=False))
        events = self.center.event_log()["items"]
        self.assertTrue(any(e["method"] == "item/reasoning/summaryPartAdded" and e["summary_index"] == 1 for e in events))
        self.assertTrue(any(e.get("summary_delta") == "再确认结果。" for e in events))

    def test_identical_deltas_are_not_deduplicated_and_part_does_not_clear_text(self):
        self.summary("重复")
        self.summary("重复")
        self.summary()
        self.summary()
        self.assertEqual(self.text(), "重复重复")
        self.assertEqual(len(self.center.get_task(self.task_id)["reasoning_summary"]["items"][0]["parts"]), 1)

    def test_full_chain_and_extra_payload_never_reach_storage_or_api(self):
        self.assertFalse(self.send("item/reasoning/textDelta", turnId="turn-1", itemId="reasoning-1", delta="RAW-CHAIN"))
        self.summary("可读摘要", content="RAW-CHAIN", text="RAW-CHAIN", private="RAW-CHAIN")
        self.send("item/completed", turnId="turn-1", item={"id": "reasoning-1", "type": "reasoning", "content": ["RAW-CHAIN"]})
        self.assertNotIn("RAW-CHAIN", json.dumps(self.center.snapshot()))
        self.assertNotIn("RAW-CHAIN", json.dumps(self.center.event_log()))
        for name in ("owned.json", "events.jsonl"):
            self.assertNotIn("RAW-CHAIN", (Path(self.temp.name) / name).read_text(encoding="utf-8"))

    def test_wrong_identity_invalid_fields_and_ended_turn_are_rejected(self):
        for extra in ({"turnId": "other-turn"}, {"threadId": "other-thread"}, {"summaryIndex": -1},
                      {"summaryIndex": True}, {"summaryIndex": 32}, {"itemId": ""}):
            self.assertFalse(self.summary("wrong", **extra))
        self.assertFalse(self.summary(123))
        self.assertNotIn("reasoning_summary", self.center.get_task(self.task_id))
        self.summary("accepted")
        self.assertFalse(self.center.handle_event({"method": "item/reasoning/summaryTextDelta", "params": {
            "threadId": "owned-thread", "turnId": "turn-1", "itemId": "reasoning-1", "summaryIndex": 0, "delta": "old"}}, "older-generation"))
        self.send("turn/completed", turn={"id": "turn-1", "status": "completed"})
        self.assertFalse(self.summary("late"))
        self.assertEqual(self.text(), "accepted")

    def test_restart_retains_summary_but_not_live_status(self):
        self.summary("已保存摘要")
        restarted = CodexAdapter(self.temp.name, auto_reconnect=False)
        try:
            task = restarted.get_task(self.task_id)
            self.assertEqual(task["reasoning_summary"]["items"][0]["parts"][0]["text"], "已保存摘要")
            self.assertEqual((task["live"], task["source"], task["status"]), (False, "history", "unknown"))
        finally:
            restarted.close()

    def test_next_turn_clears_previous_summary(self):
        self.summary("上一轮")
        self.send("turn/completed", turn={"id": "turn-1", "status": "completed"})
        self.start("turn-2")
        self.assertNotIn("reasoning_summary", self.center.get_task(self.task_id))
        self.assertFalse(self.summary("迟到的上一轮"))
        self.send("item/reasoning/summaryTextDelta", turnId="turn-2", itemId="reasoning-2", summaryIndex=0, delta="新一轮")
        self.assertEqual(self.text(), "新一轮")

    def test_summary_text_and_item_count_are_bounded(self):
        self.summary("x" * (SUMMARY_MAX_CHARS + 1))
        self.summary("y")
        self.assertEqual(len(self.text()), SUMMARY_MAX_CHARS)
        self.assertTrue(self.center.get_task(self.task_id)["reasoning_summary"]["truncated"])
        for i in range(SUMMARY_MAX_ITEMS + 2):
            self.summary(item="item-" + str(i))
        self.assertEqual(len(self.center.get_task(self.task_id)["reasoning_summary"]["items"]), SUMMARY_MAX_ITEMS)

    def test_start_turn_requests_summary_without_changing_approval_policy(self):
        self.send("turn/completed", turn={"id": "turn-1", "status": "completed"})
        with patch.object(self.center, "_rpc", return_value={"turn": {"id": "turn-2", "status": "inProgress"}}) as rpc:
            self.center.start_turn(self.task_id, "只读任务")
        method, params = rpc.call_args.args
        self.assertEqual(method, "turn/start")
        self.assertEqual(params["summary"], "auto")
        self.assertEqual(params["approvalPolicy"], "on-request")
        self.assertEqual(params["approvalsReviewer"], "user")
        self.assertEqual(params["sandboxPolicy"], {"type": "readOnly", "networkAccess": False})


if __name__ == "__main__":
    unittest.main()
