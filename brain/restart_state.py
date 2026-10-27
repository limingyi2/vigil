# -*- coding: utf-8 -*-
"""「待重启」状态的持久化记录。

为什么要有这个模块：`restart_needed` 以前只活在 API 响应里，toast 弹 2.6 秒
就没了。用户刷新一下页面、或者过一会儿回来看，完全不知道"我刚才装的东西
其实还没真正生效"—— 他会继续以为新插件在干活，而内存里跑的还是旧的那份。
装卸类操作的结果必须是**可回看**的，不能只靠一次弹窗。

判据用时间戳比较，而不是"打勾 + 重启后取消"：

    pending = changed_at > started_at

进程每启动一次就把 `started_at` 推到当前时刻，于是：

* 不需要任何"重启后清除"的逻辑，也就不存在"忘了清除 → 永远显示需重启"；
* 更不可能"虚假显示已生效"—— 只要磁盘改动发生在本次启动之后，判据就一定
  会说需重启，跟界面刷没刷新、前端记不记得无关。判据的唯一来源是磁盘上
  这两个时间戳，没有前端临时变量参与。

两个 scope 各用一个文件，写者不重叠：

* `plugin` → `<plugins_dir>/_pending_restart.json`，只由大脑（Python）读写；
* `skill`  → `<skill_dir>/_pending_restart.json`，大脑写 `changed_at`，
  Node 宿主启动时写 `started_at`（宿主是独立进程，重启它才让技能状态落地）。

 **`note_start` 的调用时机就是"服务真的起来了"的那一刻**：大脑侧在
`start_remote_api` 里 HTTP 绑定成功之后调用，宿主侧在 `server.listen` 的
回调里调用。不能图省事放在进程入口 —— 绑定失败（端口被占）时进程不会
立即退出（run_server 只打印一行然后继续跑收尾循环），入口处的登记会被
这次失败的重启推新 `started_at`，"重启失败"反倒把待重启提示抹掉，
界面看着像重启成功了。真实 UI 验收（C3-7b/c）实测复现过，别改回去。

文件形状（两个 scope 同构，Node 侧只认这两个键）：

    {"changed_at": 1728.0, "started_at": 1728.0, "reasons": ["安装插件 foo"]}
"""
import json
import os
import tempfile
import time

# reasons 只留最近这么多条：它是给人看的说明，不是审计账，
# 无限增长会让文件变成又一份谁都看不懂的历史。
_MAX_REASONS = 8

_BLANK = {"changed_at": 0.0, "started_at": 0.0, "reasons": []}


def state_path(scope, directory):
    """这一 scope 的状态文件落在哪个目录下。"""
    return os.path.join(str(directory), "_pending_restart.json")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return dict(_BLANK)
    if not isinstance(d, dict):
        return dict(_BLANK)
    out = dict(_BLANK)
    for k in ("changed_at", "started_at"):
        try:
            out[k] = float(d.get(k) or 0.0)
        except (TypeError, ValueError):
            out[k] = 0.0
    rs = d.get("reasons")
    out["reasons"] = [str(x)[:120] for x in rs if isinstance(x, str)] \
        if isinstance(rs, list) else []
    return out


def _write(path, data):
    """原子落盘：先写同目录临时文件再 replace。

    非原子写会有半文件窗口，而读的一方（大脑 / Node 宿主）没有任何重试，
    读到半个 JSON 就退化成"没有状态"—— 那种失败是静默的。
    """
    d = os.path.dirname(path) or "."
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".pending_restart_", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except Exception:
        # 状态文件写不进去不该让装卸失败：它只是提示层，不是事务的一部分。
        # 真写不进去时界面退回"没有持久提示"，比整个操作报错要好。
        pass


def note_change(scope, directory, reason=""):
    """磁盘发生了变更（装 / 卸 / 更新）。记下时刻与原因。"""
    path = state_path(scope, directory)
    d = _read(path)
    # Keep same-process events strictly ordered even on filesystems / clocks
    # whose timestamp resolution rounds rapid operations to the same instant.
    d["changed_at"] = max(time.time(), d["started_at"] + 0.000001,
                           d["changed_at"] + 0.000001)
    if reason:
        d["reasons"] = ([str(reason)[:120]] + d["reasons"])[:_MAX_REASONS]
    _write(path, d)
    return d


def note_start(scope, directory):
    """进程启动了 —— 从这一刻起，磁盘状态就是运行状态。

    大脑启动调 plugin scope；Node 宿主启动调 skill scope。
    """
    path = state_path(scope, directory)
    d = _read(path)
    d["started_at"] = max(time.time(), d["changed_at"])
    _write(path, d)
    return d


def snapshot(scope, directory):
    """给界面用的一个字典：{pending, reasons, changed_at}。"""
    d = _read(state_path(scope, directory))
    return {"pending": bool(d["changed_at"] > d["started_at"]),
            "reasons": list(d["reasons"]),
            "changed_at": d["changed_at"]}


def is_pending(scope, directory):
    return snapshot(scope, directory)["pending"]


def clear(scope, directory):
    """人工清掉（重启之外的显式动作，比如用户点"我知道了"）。

    注意这不是"虚假显示已生效"的后门：它只改提示，不改任何磁盘文件，
    运行态该旧还是旧。界面仍会在下次装卸时重新置位。
    """
    path = state_path(scope, directory)
    _write(path, dict(_BLANK))
