"""Authenticated, isolated actual Brain HTTP routes for the Electron P2 test.

Only structural events are deterministic fixtures. No Codex login or model API.
Commands arrive on the parent's private stdin; no fixture control HTTP route.
"""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "brain"))
from tests.test_codex_adapter_http import BrainHTTPTests


def main():
    BrainHTTPTests.setUpClass()
    center = BrainHTTPTests.center
    center.runtime.info = {"installed": True, "version": "0.162.0-fixture", "auth_status": "logged_in", "error": None}
    task_id = "a" * 32
    thread_id = "p2-fixture-thread"
    def connected():
        center.runtime.info = {"installed": True, "version": "0.162.0-fixture", "auth_status": "logged_in", "error": None}
        center.connection_status = "connected"
        center.generation = "p2-generation-" + str(time.time_ns())
        center.models = {"local-fixture"}
        center.default_model = "local-fixture"
        center.last_error = None
        for task in center.tasks.values():
            task["connection_generation"] = center.generation
            center._apply_history(task, {"turns": list(task.get("turns", {}).values())})
        center._record("adapter/connected")
        return center.status()
    center.connect = connected
    center.reconnect = lambda: (center.disconnect(), connected())[1]
    def login():
        center.runtime.info = {**center.runtime.info, "auth_status": "logging_in", "error": None}
        center.last_error = None
        return center.status()
    center.login = login
    connected()
    center.tasks[task_id] = {"id":task_id,"thread_id":thread_id,"workspace":str(center._workspace(task_id)),
        "ownership":"owned","status":"completed","source":"history","live":False,
        "model":"local-fixture","created_at":time.time(),"last_activity":time.time(),
        "connection_generation":center.generation,"turns":{},"current_turn":None,"tool_failures":0}
    center.observed["p2-desktop-history"] = {"thread_id":"p2-desktop-history","ownership":"observed",
        "status":"unknown","source":"history","live":False,"history_read_at":time.time(),
        "turns":{"old-turn":{"status":"inProgress","source":"history"}}}
    center._record("thread/status/changed",task=center.tasks[task_id],state="waiting_approval")
    if os.environ.get("VIGIL_TEST_START_DISCONNECTED") == "1":
        center.disconnect()
    center.observe = lambda _=None: {"items":list(center.observed.values()),"source":"history","live":False}
    def rpc(method, params):
        if method == "thread/start":
            return {"thread":{"id":"created-"+str(time.time_ns())},"model":"local-fixture",
                    "approvalPolicy":"on-request","approvalsReviewer":"user","sandbox":{"type":"readOnly","networkAccess":False}}
        if method == "turn/start":
            turn = {"id":"created-turn-"+str(time.time_ns()),"status":"inProgress"}
            center.handle_event({"method":"turn/started","params":{"threadId":params["threadId"],"turn":turn}},center.generation)
            return {"turn":turn}
        if method == "turn/interrupt":
            center.handle_event({"method":"turn/completed","params":{"threadId":params["threadId"],"turn":{"id":params["turnId"],"status":"interrupted"}}},center.generation)
            return {}
        raise RuntimeError("unexpected fixture RPC")
    center._rpc = rpc
    def send(method, **params):
        return center.handle_event({"method":method,"params":{"threadId":thread_id,**params}},center.generation)
    print(json.dumps({"ready":True,"port":BrainHTTPTests.server.server_port,"token":"p1-http-test-token"}),flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line); op=command["op"]
            with center.lock:
                task = center.tasks[task_id]
                if op == "start":
                    task["turn_request_generation"] = center.generation
                    send("turn/started",turn={"id":command.get("turn","p2-turn"),"status":"inProgress"})
                elif op == "tool":
                    send("item/started",turnId=task["current_turn"],item={"id":"p2-tool","type":"commandExecution","command":"PRIVATE-FIXTURE-COMMAND"})
                elif op == "tool_failed":
                    send("item/completed",turnId=task["current_turn"],item={"id":"p2-tool","type":"commandExecution","status":"failed","exitCode":7,"durationMs":250})
                elif op == "tool_complete":
                    send("item/started",turnId=task["current_turn"],item={"id":"p2-second-tool","type":"mcpToolCall","arguments":{"secret":"PRIVATE-FIXTURE"}})
                    send("item/completed",turnId=task["current_turn"],item={"id":"p2-second-tool","type":"mcpToolCall","status":"completed","durationMs":42})
                elif op == "reasoning_summary":
                    send("item/reasoning/summaryPartAdded",turnId=task["current_turn"],itemId="p2-reasoning",summaryIndex=0)
                    send("item/reasoning/summaryTextDelta",turnId=task["current_turn"],itemId="p2-reasoning",summaryIndex=0,
                         delta="继续检查。" if command.get("more") else "检查输入。<img src=x onerror=alert(1)>")
                    send("item/reasoning/summaryPartAdded",turnId=task["current_turn"],itemId="p2-reasoning",summaryIndex=1)
                    send("item/reasoning/summaryTextDelta",turnId=task["current_turn"],itemId="p2-reasoning",summaryIndex=1,delta="确认结果。")
                    send("item/reasoning/textDelta",turnId=task["current_turn"],itemId="p2-reasoning",delta="RAW-CHAIN-MARKER")
                elif op in ("approval","input"):
                    method="item/tool/requestUserInput" if op=="input" else "item/commandExecution/requestApproval"
                    msg={"id":0,"method":method,"params":{"threadId":thread_id,"turnId":task["current_turn"],"itemId":"p2-request","isBlocking":True}}
                    if command.get("duplicate"): msg["params"]["private"]="different duplicate payload"
                    center.handle_event(msg,center.generation)
                elif op == "waiting_status":
                    send("thread/status/changed",status={"type":"active","activeFlags":["waitingOnApproval"]})
                elif op in ("complete","fail"):
                    send("turn/completed",turn={"id":task["current_turn"],"status":"completed" if op=="complete" else "failed"})
                elif op == "disconnect": center.disconnect()
                elif op == "runtime":
                    center.disconnect()
                    center.runtime.info = {"installed": command.get("installed", True),
                                           "version": command.get("version", "0.162.0-fixture"),
                                           "auth_status": command.get("auth_status", "unknown"),
                                           "error": command["error"]}
                    center.last_error = command["error"]
                elif op == "reconnect": center.reconnect()
                elif op == "gap":
                    for _ in range(2005): center._record("fixture/rollover",task=task)
                elif op == "restart":
                    center.connection_id="p2-restarted-connection";center.events.clear();center.seq=0
                    connected()
                elif op == "snapshot": pass
                elif op == "quit": break
                else: raise ValueError("unexpected fixture command")
                print(json.dumps({"id":command["id"],"cursor":center.seq,"snapshot":center.snapshot(),"events":center.event_log()["items"]}),flush=True)
    finally:
        BrainHTTPTests.tearDownClass()


if __name__ == "__main__": main()
