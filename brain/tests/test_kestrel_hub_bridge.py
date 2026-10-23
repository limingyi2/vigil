import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kestrel_hub_bridge as bridge


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))


class HubBridgeTests(unittest.TestCase):
    def test_existing_info_call_returns_read_only_inventory(self):
        ws = FakeSocket()
        bridge._handle_message(ws, json.dumps({"type": "call", "id": "req-1", "action": "info", "args": {}}))
        self.assertEqual(len(ws.sent), 1)
        result = ws.sent[0]
        self.assertEqual((result["type"], result["id"], result["ok"]), ("result", "req-1", True))
        self.assertEqual(result["body"]["actions"], ["info"])
        self.assertTrue(result["body"]["name"])

    def test_all_non_info_calls_are_refused(self):
        ws = FakeSocket()
        bridge._handle_message(ws, json.dumps({"type": "call", "id": "req-2", "action": "shell", "args": {"command": "whoami"}}))
        self.assertEqual(ws.sent, [{
            "type": "result", "id": "req-2", "ok": False, "error": "not_allowed",
            "message": "This Bridge supports read-only device info only",
        }])

    def test_malformed_frame_does_not_raise_or_answer(self):
        ws = FakeSocket()
        bridge._handle_message(ws, "not-json")
        bridge._handle_message(ws, json.dumps({"type": "ping"}))
        self.assertEqual(ws.sent, [])

    def test_device_info_does_not_advertise_remote_execution(self):
        self.assertEqual(bridge.device_info()["actions"], ["info"])


if __name__ == "__main__":
    unittest.main()
