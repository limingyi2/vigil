# -*- coding: utf-8 -*-
"""P4 效率优化 A/B/C 对照评测（任务书 vigil-hybrid-efficiency-optimization-v1）。

三组 + 一个离线可达性补充，全部真实云端主模型 + 真实 Ollama：

- A cloud_online ：云端主模型 + **在线 PC 只读工具**（评测进程内用
  ReadOnlySandbox 桩一个在线客户端，授权根就是该任务 fixture），无委派工具；
- B hybrid_old   ：已提交旧版（worktree checkout 8ae1d8f）的委派逻辑，
  PC 离线，子进程运行，保证跑的真的是旧代码；
- C hybrid_opt   ：当前代码（短证据回灌 + 程序核验 + 工具优化 + 三路路由），
  PC 离线，委派在大脑进程内完成；
- O cloud_offline：云端直答且 PC 离线（仅委派类任务），可达性维度，
  不混进 A/B/C 的性能均值。

交错顺序、可配重复次数、中位数统计；指标按任务书 P0.2 采集。
事件增量落盘，中途断掉也保留已跑完的轮次。

用法：
    python vigil-eval/run_hybrid_compare2.py [--repeats 3] [--only H01,H03]
        [--groups ABCO] [--think true] [--tag 自定义后缀]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BRAIN = REPO / "brain"
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(HERE))

import run_hybrid_compare as base      # noqa: E402  复用任务/夹具/判定/组切换
import localtools                       # noqa: E402
from brain import Brain                 # noqa: E402
from delegation import bridge as dbridge  # noqa: E402
from delegation.sandbox import ReadOnlySandbox  # noqa: E402

OLD_COMMIT = "8ae1d8f"                  # 优化前最后一次提交（P3 对照批次之后）
_REAL_LOCAL_CALL = localtools.call      # A 组打桩后要靠它恢复


# ── 在线 PC 桩：等价于一个授权了该工作区的在线 Electron 客户端 ──────────
class OnlinePCStub(object):
    """把 local_file_* 四个只读工具落到任务沙箱执行；其余工具一律"不在线"。

    与真实客户端的差异只在传输：没有 HTTP 排队，执行的权限边界、只读语义、
    路径白名单与 ReadOnlySandbox 完全一致（同一套 safe_path）。
    """

    def __init__(self, ws):
        self.sb = ReadOnlySandbox(ws)
        self.ws = os.path.realpath(ws)
        self.calls = 0
        self.seconds = 0.0

    def _rel(self, p):
        p = os.path.realpath(os.path.abspath(os.path.expanduser(str(p or ""))))
        if os.path.commonpath([self.ws, p]) != self.ws:
            return None
        return os.path.relpath(p, self.ws)

    def call(self, name, args, wait=None):
        args = args if isinstance(args, dict) else {}
        t0 = time.time()
        try:
            if name == "local_file_list":
                rel = self._rel(args.get("path") or self.ws)
                if rel is None:
                    return "路径不在授权工作区内"
                out = self.sb.file_list(rel)
            elif name == "local_file_read":
                rel = self._rel(args.get("path"))
                if rel is None:
                    return "路径不在授权工作区内"
                out = self.sb.read_file(rel)
            elif name == "local_file_find":
                target = args.get("name") or args.get("pattern") or ""
                rel = self._rel(args.get("path") or self.ws)
                if rel is None:
                    return "路径不在授权工作区内"
                out = self.sb.file_find("*%s*" % target.strip(), rel)
            elif name == "local_file_grep":
                rel = self._rel(args.get("path") or self.ws)
                if rel is None:
                    return "路径不在授权工作区内"
                out = self.sb.file_grep(str(args.get("pattern") or ""), rel)
            else:
                return ("没有在线的电脑能执行「%s」—— 在线基线只开放只读文件工具"
                        % name)
            self.calls += 1
            return out
        except Exception as e:
            return "工具失败（%s）：%s" % (type(e).__name__, str(e)[:120])
        finally:
            self.seconds += round(time.time() - t0, 3)


def configure_online(ws):
    """A 组：云端模式 + 在线 PC 桩。"""
    base.configure_group("A", ws)
    localtools.call = _REAL_LOCAL_CALL  # 先恢复，防上一轮残留
    stub = OnlinePCStub(ws)
    localtools.call = stub.call
    localtools._LAST_POLL = time.time() + 3600
    return stub


def configure_offline():
    """O 组：云端模式，PC 明确离线（复现首批对照的 A 组）。"""
    base.configure_group("A", "")
    # A 组把 localtools.call 换成过桩；这里必须恢复真函数，否则后面的组
    # 会拿着上一个任务工作区的沙箱桩执行工具，结果全串。
    localtools.call = _REAL_LOCAL_CALL
    localtools._LAST_POLL = 0.0
    return None


def run_current(group, task, ws):
    """跑当前代码的一轮（C 优化版 / O 离线云端）。返回事件 dict。"""
    request = task["request"].replace("{ws}", ws or "（无）")
    if group == "C":
        base.configure_group("B", os.path.dirname(ws) if ws else "")
        localtools.call = _REAL_LOCAL_CALL
        localtools._LAST_POLL = 0.0
        online = None
    else:
        online = configure_offline()
        stub = None
    plugin_host = sys.modules.get("plugin_host")
    plugin_host.load(force=True)
    br = base.make_brain()

    captured = {"calls": []}
    if group == "C":
        real_run = dbridge.run_delegation

        def spy(contract, **kw):
            res = real_run(contract, **kw)
            captured["calls"].append(dict(res))
            return res
        dbridge.run_delegation = spy

    t0 = time.time()
    try:
        ans, mode = br.chat(request, log=False, src="pc")
    except Exception as e:
        ans, mode = "", "EXC: %s" % type(e).__name__
    wall = round(time.time() - t0, 1)
    if group == "C":
        dbridge.run_delegation = real_run  # noqa: F821

    usage = getattr(br, "last_usage", None) or {"in": 0, "out": 0}
    used = list(getattr(br, "last_tools_used", []) or [])
    dl = captured["calls"][0] if captured["calls"] else None
    route = getattr(br, "last_route", None)
    return {
        "group": group, "task_id": task["id"],
        "category": task["category"],
        "should_delegate": task["should_delegate"],
        "request": request, "answer": ans or "", "mode": mode,
        "wall_seconds": wall,
        "cloud_requests": getattr(br, "last_cloud_requests", 0),
        "cloud_model_seconds": getattr(br, "last_cloud_seconds", 0.0),
        "main_model": {"in_tokens": usage.get("in", 0),
                       "out_tokens": usage.get("out", 0),
                       "tools_used": used},
        "route": route,
        "pc_tool_calls": 0, "pc_tool_seconds": 0.0,
        "delegated": bool(dl),
        "delegation": _dl_summary(dl),
    }


def run_online(task, ws):
    """A 组：云端 + 在线 PC 只读工具。"""
    request = task["request"].replace("{ws}", ws or "（无）")
    online = configure_online(ws)
    sys.modules.get("plugin_host").load(force=True)
    br = base.make_brain()
    t0 = time.time()
    try:
        ans, mode = br.chat(request, log=False, src="pc")
    except Exception as e:
        ans, mode = "", "EXC: %s" % type(e).__name__
    wall = round(time.time() - t0, 1)
    usage = getattr(br, "last_usage", None) or {"in": 0, "out": 0}
    used = list(getattr(br, "last_tools_used", []) or [])
    return {
        "group": "A", "task_id": task["id"],
        "category": task["category"],
        "should_delegate": task["should_delegate"],
        "request": request, "answer": ans or "", "mode": mode,
        "wall_seconds": wall,
        "cloud_requests": getattr(br, "last_cloud_requests", 0),
        "cloud_model_seconds": getattr(br, "last_cloud_seconds", 0.0),
        "main_model": {"in_tokens": usage.get("in", 0),
                       "out_tokens": usage.get("out", 0),
                       "tools_used": used},
        "route": {"route": "cloud_online_pc_tools"},
        "pc_tool_calls": online.calls,
        "pc_tool_seconds": online.seconds,
        "delegated": False,
        "delegation": None,
    }


def _dl_summary(res):
    if not res:
        return None
    return {
        "kind": res.get("kind"), "status": res.get("status"),
        "model_requests": res.get("model_requests"),
        "tool_calls": res.get("tool_calls"),
        "input_tokens": res.get("input_tokens"),
        "output_tokens": res.get("output_tokens"),
        "model_seconds": res.get("model_seconds"),
        "tool_seconds": res.get("tool_seconds"),
        "validation_seconds": res.get("validation_seconds"),
        "elapsed_seconds": res.get("elapsed_seconds"),
        "cache_hits": res.get("cache_hits"),
        "verified": res.get("verified"),
        "degraded": res.get("degraded"),
        "retry_used": res.get("retry_used"),
        "errors": (res.get("errors") or [])[:2],
        "structured": res.get("structured"),
    }


# ── B 组：旧版代码 worktree 子进程 ────────────────────────────────────
def run_old_in_worktree(worktree, task_id, env_extra):
    """用旧版脚本在 worktree 跑单任务 B 组，解析它落盘的 raw_events。"""
    old_script = os.path.join(worktree, "vigil-eval", "run_hybrid_compare.py")
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, old_script, "--only", task_id, "--groups", "B"],
        cwd=worktree, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    runs = sorted(Path(worktree, "results").glob("hybrid-compare-*"),
                  key=lambda p: p.stat().st_mtime)
    if not runs:
        raise RuntimeError("旧版评测没有产出目录：%s"
                           % (proc.stderr[-500:] if proc.stderr else ""))
    ev_file = runs[-1] / "raw_events.jsonl"
    line = ev_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    ev = json.loads(line)
    # 旧事件字段映射进新指标骨架（旧版没有分段计时/云端请求数，留 None）
    mm = ev.get("main_model", {})
    dl0 = ev.get("delegation") or {}
    return {
        "group": "B", "task_id": ev["task_id"],
        "category": ev["category"], "should_delegate": ev["should_delegate"],
        "request": ev["request"], "answer": ev["answer"], "mode": ev["mode"],
        "wall_seconds": ev["wall_seconds"],
        "cloud_requests": None,
        "cloud_model_seconds": None,
        "main_model": {"in_tokens": mm.get("in_tokens", 0),
                       "out_tokens": mm.get("out_tokens", 0),
                       "tools_used": mm.get("tools_used", [])},
        "route": {"route": "hybrid_old"},
        "pc_tool_calls": 0, "pc_tool_seconds": 0.0,
        "delegation": {
            "kind": dl0.get("kind"), "status": dl0.get("status"),
            "model_requests": dl0.get("model_requests"),
            "tool_calls": dl0.get("tool_calls"),
            "input_tokens": dl0.get("input_tokens"),
            "output_tokens": dl0.get("output_tokens"),
            "model_seconds": None, "tool_seconds": None,
            "validation_seconds": None,
            "elapsed_seconds": dl0.get("elapsed_seconds"),
            "cache_hits": None, "verified": None, "degraded": None,
            "retry_used": dl0.get("retry_used"),
            "errors": dl0.get("errors"),
            "structured": dl0.get("structured"),
        } if dl0 else None,
        "old_stdout_tail": (proc.stdout or "")[-300:],
        "delegated": bool(dl0),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--groups", default="ABC")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--think", default="true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    tasks_all = base.load_tasks()
    sel = [t for t in tasks_all
           if not args.only or t["id"] in args.only.split(",")]
    # 任务书：预算受限时 H02/H04 只跑 1 次（安全闸偶发 + 实时信息）
    reps = {}
    for t in sel:
        reps[t["id"]] = 1 if t["id"] in ("H02", "H04") else args.repeats

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = ("-" + args.tag) if args.tag else ""
    run_dir = REPO / "results" / ("hybrid-abc-%s%s" % (stamp, tag))
    (run_dir / "ws").mkdir(parents=True, exist_ok=True)
    events_file = run_dir / "raw_events.jsonl"

    think = str(args.think).lower() not in ("false", "0", "")
    base.OLLAMA_CFG["think"] = think
    env_extra = {"PYTHONIOENCODING": "utf-8"}

    # B 组 worktree（旧版代码），config.json 是 gitignore 的，手动拷一份
    wt = None
    events = []

    def append_event(ev):
        ok, notes = base.judge(next(t for t in sel if t["id"] == ev["task_id"]), ev)
        ev["oracle_pass"] = ok
        ev["oracle_notes"] = notes
        should = ev["should_delegate"]
        if ev["delegated"]:
            ev["decision"] = ("wrong_delegate" if should is False
                              else ("optional_delegated"
                                    if should == "optional" else "hit"))
        else:
            ev["decision"] = ("correct_skip" if should is False
                              else ("optional_self"
                                    if should == "optional" else "miss"))
        events.append(ev)
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        dl = ev["delegation"]
        print("  → %s %s 决策=%s 云tokens=%s/%s 云请求=%s 墙钟=%.0fs %s"
              % ("PASS" if ok else "FAIL", ev["group"], ev["decision"],
                 ev["main_model"]["in_tokens"], ev["main_model"]["out_tokens"],
                 ev["cloud_requests"], ev["wall_seconds"],
                 ("委派=%s" % dl["status"]) if dl else ""), flush=True)

    try:
        if "B" in args.groups:
            wt = os.path.join(str(REPO), "results", "_wt_old_" + stamp)
            print("[B] worktree add %s @ %s …" % (wt, OLD_COMMIT), flush=True)
            subprocess.run(["git", "worktree", "add", "--detach", wt, OLD_COMMIT],
                           cwd=str(REPO), check=True, capture_output=True, text=True)
            os.makedirs(os.path.join(wt, "brain", "config"), exist_ok=True)
            shutil.copy2(REPO / "brain" / "config" / "config.json",
                         os.path.join(wt, "brain", "config", "config.json"))

        # 交错：按重复轮次，每轮换组起始顺序，降低顺序偏差
        group_rot = list(args.groups)
        for r in range(max(reps.values())):
            order = group_rot[r % len(group_rot):] + group_rot[:r % len(group_rot)]
            for t in sel:
                if r >= reps[t["id"]]:
                    continue
                ws = base.prepare_workspace(t, run_dir)
                ws_root = os.path.join(run_dir, "ws", t["id"])
                for g in order:
                    # O 组只补委派类任务的离线可达性
                    if g == "O" and (t["should_delegate"] is False):
                        continue
                    print("[r%d %s/%s] %s …" %
                          (r + 1, t["id"], g, t["category"]), flush=True)
                    try:
                        if g == "A":
                            ev = run_online(t, ws_root)
                        elif g == "C":
                            ev = run_current("C", t, ws_root)
                        elif g == "O":
                            ev = run_current("O", t, ws_root)
                        elif g == "B":
                            ev = run_old_in_worktree(wt, t["id"], env_extra)
                        else:
                            continue
                    except Exception as e:
                        ev = {"group": g, "task_id": t["id"],
                              "category": t["category"],
                              "should_delegate": t["should_delegate"],
                              "request": "", "answer": "",
                              "mode": "RUN_EXC: %s" % type(e).__name__,
                              "wall_seconds": 0, "cloud_requests": None,
                              "cloud_model_seconds": None,
                              "main_model": {"in_tokens": 0, "out_tokens": 0,
                                             "tools_used": []},
                              "route": None, "pc_tool_calls": 0,
                              "pc_tool_seconds": 0.0, "delegated": False,
                              "delegation": None,
                              "run_error": str(e)[:200]}
                    append_event(ev)
    finally:
        if wt and os.path.isdir(wt):
            cfg_copy = os.path.join(wt, "brain", "config", "config.json")
            if os.path.exists(cfg_copy):
                os.remove(cfg_copy)
            subprocess.run(["git", "worktree", "remove", "--force", wt],
                           cwd=str(REPO), capture_output=True)

    write_summary(events, run_dir, sel, reps, think)
    print("\n报告：", run_dir)


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)


def _sum(xs):
    return sum(x for x in xs if x is not None)


def write_summary(events, run_dir, tasks, reps, think):
    groups = sorted({e["group"] for e in events})
    lines = ["# 效率优化 A/B/C 对照（think=%s）" % ("on" if think else "off"),
             "",
             "A=云端+在线PC只读工具；B=旧版委派(8ae1d8f)；C=优化版；O=云端离线（可达性补充，不进性能均值）。",
             "中位数按同一任务的重复轮次计；成功率单独列，不只对成功样本计时。", ""]

    agg = {}
    for g in groups:
        agg[g] = {}
        for t in tasks:
            es = [e for e in events if e["group"] == g and e["task_id"] == t["id"]]
            if not es:
                continue
            n = len(es)
            okn = sum(1 for e in es if e["oracle_pass"])
            succ_dl = [e for e in es if e.get("delegation")
                       and e["delegation"]["status"] == "succeeded"]
            agg[g][t["id"]] = {
                "n": n, "pass": "%d/%d" % (okn, n),
                "wall_med": _med([e["wall_seconds"] for e in es]),
                "cin_med": _med([e["main_model"]["in_tokens"] for e in es]),
                "cout_med": _med([e["main_model"]["out_tokens"] for e in es]),
                "cloud_req_med": _med([e["cloud_requests"] for e in es]),
                "pc_calls_med": _med([e["pc_tool_calls"] for e in es]),
                "local_in": _sum((e.get("delegation") or {}).get("input_tokens")
                                 for e in es if e.get("delegation")),
                "local_out": _sum((e.get("delegation") or {}).get("output_tokens")
                                  for e in es if e.get("delegation")),
                "dl_succ": len(succ_dl), "dl_n": sum(
                    1 for e in es if e.get("delegation")),
                "verified": sum(1 for e in succ_dl
                                if (e["delegation"].get("verified"))),
                "degraded": sum(1 for e in es
                                if (e.get("delegation") or {}).get("degraded")),
                "routes": _route_counts(es),
            }

    lines += ["## 逐任务中位数", "",
              "| 任务 | 组 | 通过 | 墙钟中位(s) | 云输入tok | 云输出tok | 云请求 | PC工具 | 委派成/次 | 证据核验 | 路由 |",
              "|---|---|---|---:|---:|---:|---:|---:|---|---:|---|"]
    for t in tasks:
        for g in groups:
            a = agg[g].get(t["id"])
            if not a:
                continue
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s | %d/%d | %d | %s |"
                % (t["id"], g, a["pass"], a["wall_med"], a["cin_med"],
                   a["cout_med"], a["cloud_req_med"], a["pc_calls_med"],
                   a["dl_succ"], a["dl_n"], a["verified"], a["routes"]))
    lines.append("")

    # C vs B 首要业务指标（同任务、同成功集分开汇报）
    if "B" in agg and "C" in agg:
        lines += ["## C(优化) vs B(旧版)", "",
                  "| 任务 | 墙钟 B→C | 云输入 B→C | 云输出 B→C | 通过 B/C |",
                  "|---|---|---|---|---|"]
        for t in tasks:
            b, c = agg["B"].get(t["id"]), agg["C"].get(t["id"])
            if not b or not c:
                continue
            lines.append("| %s | %s→%s | %s→%s | %s→%s | %s / %s |" % (
                t["id"], b["wall_med"], c["wall_med"],
                b["cin_med"], c["cin_med"], b["cout_med"], c["cout_med"],
                b["pass"], c["pass"]))
        lines.append("")

    # A vs C 逐任务胜负
    if "A" in agg and "C" in agg:
        lines += ["## C(hybrid) vs A(云端在线PC)", "",
                  "| 任务 | 墙钟 A→C | 云输入 A→C | 云输出 A→C | 通过 A/C |",
                  "|---|---|---|---|---|"]
        for t in tasks:
            a, c = agg["A"].get(t["id"]), agg["C"].get(t["id"])
            if not a or not c:
                continue
            lines.append("| %s | %s→%s | %s→%s | %s→%s | %s / %s |" % (
                t["id"], a["wall_med"], c["wall_med"],
                a["cin_med"], c["cin_med"], a["cout_med"], c["cout_med"],
                a["pass"], c["pass"]))
        lines.append("")

    (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")


def _route_counts(es):
    d = {}
    for e in es:
        r = (e.get("route") or {}).get("route") or "?"
        d[r] = d.get(r, 0) + 1
    return json.dumps(d, ensure_ascii=False)


if __name__ == "__main__":
    main()
