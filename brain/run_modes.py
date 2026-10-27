# -*- coding: utf-8 -*-
"""运行模式（云端 / 本地 Ollama / 混合）的切换门，以及本机 Ollama 的生命周期。

为什么单独一层：以前切换模式只能去设置页改配置，改完没人验证"这台机器上
Ollama 到底能不能跑、模型在不在、显存够不够"，界面上还显示着"已切换"。
这里把"先证明能用，再落配置"固定下来；证明不了就原样退回并说清原因。

三条铁律：
1. 切换前必须实测（服务在不在 → 模型在不在 → 能不能真的加载并出字）。
   任何一步失败都不写配置，界面保持旧模式。
2. 退出本地/混合后才卸载模型，且只卸载**本进程用过**、**没有作业占用**的
   模型（见 acquire/release）。绝不结束 Ollama 进程 —— 别的应用可能在用。
3. 状态只报事实：没探测就说没探测，不把"配置里写着 local"当成"本地可用"。
"""
import os
import contextlib
import functools
import uuid
import shutil
import subprocess
import threading
import time

import httpx

MODES = ("cloud", "local", "hybrid")
LABELS = {"cloud": "云端", "local": "本地", "hybrid": "混合"}
DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_LOAD_TIMEOUT = 90          # 加载本地模型的上限；超时按失败处理
DEFAULT_START_TIMEOUT = 25         # 拉起 ollama serve 后等它就绪的上限
KEEP_ALIVE = "15m"

# 本进程租约：owner（作业 id / 会话 turn）→ 模型名。作业在跑时不允许卸载。
_LOCK = threading.RLock()
_LEASES = {}
_TOUCHED = set()                   # 本进程真正加载/调用过的模型（只卸载这些）
_START = {"at": 0.0, "err": ""}    # 最近一次拉起尝试，避免反复 spawn


def normalize(v):
    m = str(v or "").strip().lower()
    return m if m in MODES else "cloud"


def label(mode):
    return LABELS.get(normalize(mode), LABELS["cloud"])


def _ollama_cfg(cfg):
    ol = (cfg or {}).get("ollama")
    return dict(ol) if isinstance(ol, dict) else {}


def base_url(cfg):
    b = str(_ollama_cfg(cfg).get("base") or DEFAULT_BASE).strip().rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3]
    return b or DEFAULT_BASE


def local_model(cfg):
    return str(_ollama_cfg(cfg).get("model") or "").strip()


def cloud_ready(cfg):
    """云端要能真的发请求：有地址、有密钥、有模型名。"""
    cfg = cfg or {}
    return bool(str(cfg.get("api_key") or "").strip()
                and str(cfg.get("model") or "").strip())


# ── 租约：谁在用这个模型
def acquire(owner, model):
    """登记"某个作业正在用这个模型"，卸载时据此跳过。"""
    m = str(model or "").strip()
    if not owner or not m:
        return ""
    with _LOCK:
        _LEASES[str(owner)] = m
        _TOUCHED.add(m)
    return m


def release(owner):
    with _LOCK:
        _LEASES.pop(str(owner or ""), None)


@contextlib.contextmanager
def model_lease(model):
    owner = 'model:' + uuid.uuid4().hex
    acquire(owner, model)
    try:
        yield
    finally:
        release(owner)


def with_model_lease(fn):
    @functools.wraps(fn)
    def wrapped(rid, owner, cfg, *args, **kwargs):
        if normalize(cfg.get('model_mode')) == 'local':
            with model_lease(local_model(cfg)):
                return fn(rid, owner, cfg, *args, **kwargs)
        return fn(rid, owner, cfg, *args, **kwargs)
    return wrapped


def leases():
    with _LOCK:
        return dict(_LEASES)


def _busy_models():
    with _LOCK:
        return set(_LEASES.values())


# ── 与 Ollama 对话
def _tags(cfg, timeout=4):
    r = httpx.get(base_url(cfg) + "/api/tags", timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError("Ollama 返回 %s" % r.status_code)
    data = r.json() if r.content else {}
    out = []
    for m in (data.get("models") or []):
        if isinstance(m, dict) and m.get("name"):
            out.append(str(m["name"]))
    return out


def service_ready(cfg, timeout=4):
    """服务在不在。回 (ok, detail)；不抛异常（上层要拿它做状态显示）。"""
    try:
        _tags(cfg, timeout=timeout)
        return True, ""
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def find_executable():
    """找 ollama 可执行文件；找不到回空串。"""
    p = shutil.which("ollama")
    if p:
        return p
    cands = []
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        cands.append(os.path.join(local, "Programs", "Ollama", "ollama.exe"))
    for root in (os.environ.get("ProgramFiles") or "",
                 os.environ.get("ProgramW6432") or ""):
        if root:
            cands.append(os.path.join(root, "Ollama", "ollama.exe"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def ensure_service(cfg, timeout=DEFAULT_START_TIMEOUT):
    """服务没起就拉一次 `ollama serve`；已经在跑就绝不动它。

     只负责"起"，从不负责"停"：Ollama 可能是用户自己装的、别的应用也在用，
    结束进程会把别人的请求一起打断。
    """
    ok, detail = service_ready(cfg, timeout=4)
    if ok:
        return {"ok": True, "started": False, "detail": ""}
    exe = find_executable()
    if not exe:
        return {"ok": False, "started": False, "reason": "not_installed",
                "detail": "没找到 ollama 命令：先安装 Ollama（ollama.com），再回来切到本地"}
    with _LOCK:
        # 刚试过且没成功，60 秒内不重复 spawn（避免连点把进程拉一堆）
        if _START["err"] and time.time() - _START["at"] < 60:
            return {"ok": False, "started": False, "reason": "start_failed",
                    "detail": _START["err"]}
        _START["at"] = time.time()
        _START["err"] = ""
    flags = 0
    try:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    except Exception:
        flags = 0
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=flags)
    except Exception as e:
        msg = "启动 Ollama 失败：%s: %s" % (type(e).__name__, str(e)[:120])
        with _LOCK:
            _START["err"] = msg
        return {"ok": False, "started": False, "reason": "start_failed", "detail": msg}
    deadline = time.time() + max(5, int(timeout))
    while time.time() < deadline:
        ok, detail = service_ready(cfg, timeout=2)
        if ok:
            return {"ok": True, "started": True, "detail": ""}
        time.sleep(0.6)
    msg = ("Ollama 启动超时（等了 %d 秒仍连不上 %s）" % (int(timeout), base_url(cfg)))
    with _LOCK:
        _START["err"] = msg
    return {"ok": False, "started": False, "reason": "start_timeout", "detail": msg}


def _classify(status, body):
    low = str(body or "").lower()
    if any(h in low for h in ("out of memory", "outofmemory", "oom",
                              "insufficient", "gpu memory", "cuda error")):
        return "oom", "本地模型显存/内存不足"
    if status == 404 or any(h in low for h in ("not found", "no such model",
                                               "try pulling", "unable to find")):
        return "model_missing", "本地没有这个模型"
    if status in (400, 422):
        return "bad_request", "本地模型拒绝了请求参数"
    return "http_error", "本地模型返回 %s" % status


def warmup(cfg, model, timeout=None):
    """真的加载一次并让它出一个 token —— 这一步才是"能用"的证明。

    只做健康检查不够：模型文件在、但显存不足时 /api/tags 依然 200，
    第一次真请求才报错。宁可在这里等几十秒，也不要切过去之后每句都失败。
    """
    ol = _ollama_cfg(cfg)
    to = timeout or ol.get("load_timeout") or DEFAULT_LOAD_TIMEOUT
    body = {"model": model, "prompt": "ping", "stream": False,
            "keep_alive": KEEP_ALIVE, "options": {"num_predict": 1}}
    if ol.get("num_ctx"):
        body["options"]["num_ctx"] = ol["num_ctx"]
    t0 = time.time()
    try:
        r = httpx.post(base_url(cfg) + "/api/generate", json=body, timeout=float(to))
    except httpx.TimeoutException:
        return {"ok": False, "reason": "load_timeout", "ms": int((time.time() - t0) * 1000),
                "detail": "加载本地模型超时（>%d 秒）" % int(to)}
    except Exception as e:
        return {"ok": False, "reason": "service_down", "ms": int((time.time() - t0) * 1000),
                "detail": "连不上 Ollama：%s: %s" % (type(e).__name__, str(e)[:100])}
    ms = int((time.time() - t0) * 1000)
    if r.status_code != 200:
        reason, why = _classify(r.status_code, r.text)
        return {"ok": False, "reason": reason, "ms": ms,
                "detail": "%s（HTTP %s）" % (why, r.status_code)}
    with _LOCK:
        _TOUCHED.add(str(model))
    return {"ok": True, "ms": ms, "detail": ""}


def loaded_models(cfg, timeout=4):
    """/api/ps：现在真正驻留在内存里的模型。"""
    try:
        r = httpx.get(base_url(cfg) + "/api/ps", timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json() if r.content else {}
        return [str(m.get("name") or m.get("model") or "")
                for m in (data.get("models") or []) if isinstance(m, dict)]
    except Exception:
        return []


def unload_model(cfg, model, timeout=8):
    """让 Ollama 立刻释放这个模型（keep_alive=0）。

    只对**具体模型**下手，不碰服务进程；Ollama 自己会拒绝卸载正在生成的模型。
    """
    m = str(model or "").strip()
    if not m:
        return False
    try:
        r = httpx.post(base_url(cfg) + "/api/generate",
                       json={"model": m, "keep_alive": 0}, timeout=timeout)
        ok = r.status_code == 200
        if ok:
            with _LOCK:
                _TOUCHED.discard(m)
        return ok
    except Exception:
        return False


def unload_idle(cfg):
    """退出本地/混合时调用：卸载本进程用过、且没有作业占用的模型。

    返回 {"unloaded": [...], "kept": [{model, reason}], "failed": [...]}，
    界面据此如实说明释放了什么、为什么留着。
    """
    with _LOCK:
        targets = sorted(_TOUCHED)
    loaded = set(loaded_models(cfg))
    out = {"unloaded": [], "kept": [], "failed": []}
    for m in targets:
        with _LOCK:
            if m in _busy_models():
                out["kept"].append({"model": m, "reason": "还有任务在用"})
                continue
            if loaded and m not in loaded:
                _TOUCHED.discard(m)
                continue
            if unload_model(cfg, m):
                out["unloaded"].append(m)
            else:
                out["failed"].append(m)
    return out


# ── 状态
def status(cfg, probe=False):
    """给界面看的当前模式。probe=True 才真的去碰 Ollama（会慢一点）。"""
    cfg = cfg or {}
    mode = normalize(cfg.get("model_mode"))
    ol = _ollama_cfg(cfg)
    out = {
        "ok": True,
        "mode": mode,
        "mode_label": label(mode),
        "model": (local_model(cfg) if mode == "local" else str(cfg.get("model") or "")),
        "provider": str(cfg.get("provider") or "custom"),
        "cloud": {
            "model": str(cfg.get("model") or ""),
            "provider": str(cfg.get("provider") or "custom"),
            "ready": cloud_ready(cfg),
        },
        "local": {
            "base": base_url(cfg),
            "model": local_model(cfg),
            "num_ctx": ol.get("num_ctx"),
            "num_predict": ol.get("num_predict"),
            "reachable": None,
            "installed": [],
            "model_installed": None,
            "detail": "",
        },
        "hybrid": {"delegation": bool((cfg.get("delegation") or {}).get("enabled"))},
        "busy_jobs": sorted(_LEASES),
        "loaded": [],
    }
    if probe:
        ok, detail = service_ready(cfg)
        out["local"]["reachable"] = ok
        out["local"]["detail"] = detail
        if ok:
            try:
                names = _tags(cfg)
                out["local"]["installed"] = names
                out["local"]["model_installed"] = (not local_model(cfg)
                                                   or local_model(cfg) in names)
            except Exception as e:
                out["local"]["detail"] = str(e)[:120]
            out["loaded"] = loaded_models(cfg)
    return out


def switch(cfg, target, model=None, active_jobs=None):
    """切换运行模式。成功才返回新配置；失败原样退回。

    返回 {"ok": bool, "reason": str, "err": str, "detail": str, ...}
    reason 是给程序看的：busy / not_installed / start_failed / start_timeout /
    model_missing / model_missing_name / oom / load_timeout / cloud_not_ready /
    unknown。
    """
    cfg = dict(cfg or {})
    mode = str(target or "").strip().lower()
    if mode not in MODES:
        return {"ok": False, "reason": "unknown",
                "err": "运行模式只认 cloud / local / hybrid"}
    busy = [str(x) for x in (active_jobs or []) if x]
    if busy:
        return {"ok": False, "reason": "busy", "jobs": busy,
                "err": "还有 %d 个模型任务在跑：等它完成，或先取消再切模式" % len(busy)}
    current = normalize(cfg.get("model_mode"))
    want_model = str(model or "").strip()
    patch = {"model_mode": mode}
    warnings = []

    if mode in ("cloud", "hybrid") and not cloud_ready(cfg):
        return {"ok": False, "reason": "cloud_not_ready",
                "err": "云端模型没配好（缺密钥或模型名）：先在设置里补上"}

    if mode in ("local", "hybrid"):
        srv = ensure_service(cfg)
        if not srv["ok"]:
            return {"ok": False, "reason": srv.get("reason") or "start_failed",
                    "err": srv.get("detail") or "本机 Ollama 不可用"}
        try:
            installed = _tags(cfg)
        except Exception as e:
            return {"ok": False, "reason": "start_failed",
                    "err": "连上了 Ollama 但读不到模型清单：%s" % str(e)[:100]}
        use_model = want_model or local_model(cfg) or (installed[0] if installed else "")
        if not use_model:
            return {"ok": False, "reason": "model_missing_name",
                    "err": "这台机器上还没有本地模型：先在 Ollama 里 pull 一个"}
        if use_model not in installed:
            return {"ok": False, "reason": "model_missing",
                    "err": "本地没有模型「%s」：先 ollama pull %s" % (use_model, use_model),
                    "installed": installed}
        patch["ollama"] = {"model": use_model}
        if mode == "local":
            w = warmup(cfg, use_model)
            if not w["ok"]:
                return {"ok": False, "reason": w.get("reason") or "load_failed",
                        "err": w.get("detail") or "本地模型加载失败",
                        "installed": installed}
            warnings.append("本地模型已就绪（加载 %d ms）" % w["ms"])
        else:
            # 混合模式主模型仍在云端；本地只做只读委派，不预先占显存。
            warnings.append("混合模式：主模型走云端，本地 %s 只做只读委派" % use_model)

    if mode == current and (not want_model or want_model == local_model(cfg)):
        return {"ok": True, "unchanged": True, "mode": mode, "mode_label": label(mode),
                "warnings": warnings, "changed": []}

    # 真正落配置交给 model_hub（白名单收口 + 变更说明都只有一份实现）
    import model_hub
    new_cfg, changed = model_hub.apply_cfg(cfg, patch)
    if normalize(new_cfg.get("model_mode")) != mode:
        return {"ok": False, "reason": "not_applied",
                "err": "切换没生效：配置里仍是 %s" % label(new_cfg.get("model_mode"))}

    freed = {"unloaded": [], "kept": [], "failed": []}
    if current in ("local", "hybrid") and mode == "cloud":
        freed = unload_idle(cfg)          # 用旧配置的地址去卸（新配置指向云端）
        if freed["unloaded"]:
            warnings.append("已释放本地模型：%s" % "、".join(freed["unloaded"]))
        if freed["kept"]:
            warnings.append("有任务在用，暂不释放：%s"
                            % "、".join(x["model"] for x in freed["kept"]))
    return {"ok": True, "mode": mode, "mode_label": label(mode),
            "model": (patch.get("ollama") or {}).get("model") or str(new_cfg.get("model") or ""),
            "changed": changed, "warnings": warnings, "freed": freed,
            # 落盘由调用方负责（这里只算配置）；下划线前缀表示不发给客户端
            "_cfg": new_cfg,
            "status": status(new_cfg)}
