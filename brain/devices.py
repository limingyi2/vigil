# -*- coding: utf-8 -*-
"""设备表：谁被允许连进来。

**为什么要有它。** brain_token 是"主人钥匙"：一把开所有门，泄露了就得换一把、
所有端一起重配。设备 token 是"每台机器一把"，能单独吊销，也能看见谁什么时候连过。

**三条设计决定：**

1. **不存明文 token，只存 sha256。** devices.json 会跟着备份、快照、同步走，
   存明文等于密钥在仓库里到处跑（本仓有 test_secret_not_leaked 盯着这类事）。
   验证时算哈希再 `compare_digest`，明文只在注册那一次返回给客户端，
   之后存在客户端的系统钥匙串里，服务端再也拿不到。

2. **老客户端继续用 brain_token。** 方案里写"brain_token 只能调注册接口"，
   那会一次性打断现网所有手机端和桌宠 —— 不做。设备 token 是**叠加**的通道，
   不是替换；想收紧的时候（比如确定所有端都升完了）再加开关。

3. **文件是程序自己写的**，所以放 `data/`，原子替换 + 锁，跟记忆库同一套纪律。
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

# 🔴 这里必须是 `import paths` 而不是 `from paths import DEVICES_PATH`：
# 后者在 import 那一刻就把值绑死了，测试（或将来想换目录的调用方）
# 改 paths.DEVICES_PATH 根本不生效 —— 结果就是测试往真实设备表里写数据。
import paths

_LOCK = threading.RLock()
_cache = None
_PERSIST = {}          # device_id -> 上次落盘时间（只活在内存里）

# last_seen 多久落一次盘。客户端轮询很密，这值别调太小。
PERSIST_EVERY = 60


def _path():
    return paths.DEVICES_PATH


TOKEN_PREFIX = "dt_"          # device token
DEVICE_PREFIX = "dev_"        # device id

# 设备类型：只影响界面上的图标和提示，不参与鉴权判定
TYPES = ("desktop", "mobile", "pet", "wechat", "other")

# 设备角色：
# - device：受限端。只能聊天、认领/提交自己的远程工具调用、读自己的数据，
#   管理面（装插件、改配置、改人设、记忆编辑、路由切换…）一律 403。
# - admin：管理端（用户自己的桌面客户端）。API 面与主人钥匙相同。
# 注册接口本身只接受主人钥匙，所以由主人在注册时授予角色；设备无法自己提权。
ROLES = ("device", "admin")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _read():
    try:
        with open(_path(), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _write(data):
    """原子写：半截的设备表比没有更糟 —— 所有已绑设备会一起失效。"""
    p = _path()
    d = os.path.dirname(p)
    try:
        os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print("[设备] 写入失败：%s %s" % (type(e).__name__, e), flush=True)
        return False


def _all():
    global _cache
    with _LOCK:
        if _cache is None:
            _cache = _read()
        return _cache


def _reload():
    global _cache
    with _LOCK:
        _cache = None
    return _all()


def _public(d):
    """对外的一份设备信息：**不带哈希**，只留够认出是哪台的字段。"""
    return {
        "device_id": d.get("device_id", ""),
        "device_name": d.get("device_name", ""),
        "device_type": d.get("device_type", "other"),
        "role": d.get("role") if d.get("role") in ROLES else "device",
        "created_at": d.get("created_at", ""),
        "last_seen": d.get("last_seen", ""),
        "last_ip": d.get("last_ip", ""),
        "status": d.get("status", "active"),
        "token_hint": d.get("token_hint", ""),
        "capabilities": list(d.get("capabilities") or []),
    }


def _clean_caps(caps):
    """能力列表收口：只留短字符串、去重、限量。

    存它有两个用处：设备列表上看得见"这台能干什么"，以及派活时拿它跟
    客户端**当次**声明的能力取交集（谁也别自作主张多报）。
    """
    out = []
    for c in (caps or []):
        s = str(c or "").strip()[:40]
        if s and s not in out:
            out.append(s)
        if len(out) >= 40:
            break
    return out


def register(device_name, device_type="other", ip="", capabilities=None,
             role="device"):
    """注册一台设备，返回**一次性的明文 token**；写盘失败返回 None。

    同名设备重复注册不覆盖、而是新建一条 —— 两台电脑可能都叫"我的电脑"，
    按名字去重会让第二台把第一台顶掉，用户完全看不出发生了什么。

    role 只在 ROLES 里收口，调用方（server 的注册路由）本身已要求主人钥匙，
    所以由主人决定这台是不是管理端；设备不能自己注册，也就无法自我提权。
    """
    token = TOKEN_PREFIX + secrets.token_hex(16)
    did = DEVICE_PREFIX + secrets.token_hex(4)
    name = str(device_name or "").strip()[:40] or "未命名设备"
    dtype = str(device_type or "other").strip().lower()
    if dtype not in TYPES:
        dtype = "other"
    role = str(role or "device").strip().lower()
    if role not in ROLES:
        role = "device"
    rec = {
        "device_id": did,
        "device_name": name,
        "device_type": dtype,
        "role": role,
        "token_hash": _hash(token),
        "token_hint": token[-6:],       # 只留尾巴，够人认"是不是这把"
        "capabilities": _clean_caps(capabilities),
        "created_at": _now(),
        "last_seen": _now(),
        "last_ip": str(ip or ""),
        "status": "active",
    }
    with _LOCK:
        data = _all()
        data[did] = rec
        # 🔴 必须检查落盘结果：写失败时把 cache 里的临时记录回滚。
        # 否则客户端拿到 token 以为绑定成功，服务端一重启这台设备就"消失"，
        # 排查起来像 token 失效，其实是从来没写进去过。
        if not _write(data):
            data.pop(did, None)
            _cache = data
            return None
        _cache = data
    return {"device_id": did, "device_token": token, "device_name": name,
            "device_type": dtype, "role": role,
            "created_at": rec["created_at"],
            "capabilities": rec["capabilities"]}


def verify(token, ip=""):
    """校验一个设备 token。有效就返回该设备（并记一次 last_seen）。

    **last_seen 不是每次都落盘**：客户端每 1.5 秒轮询一次本地操控，
    每次都写文件等于拿闪存当草稿纸。内存里的值照常更新（设备列表看得到
    实时状态），文件最多每分钟写一次 —— 掉电丢的也就是这一分钟。
    """
    t = str(token or "").strip()
    if not t.startswith(TOKEN_PREFIX):
        return None
    h = _hash(t)
    with _LOCK:
        data = _all()
        for did, d in data.items():
            if not isinstance(d, dict):
                continue
            try:
                if not hmac.compare_digest(h, str(d.get("token_hash") or "")):
                    continue
            except Exception:
                continue
            if d.get("status") != "active":
                return None
            d["last_seen"] = _now()
            if ip:
                d["last_ip"] = str(ip)
            if time.time() - float(_PERSIST.get(did) or 0) > PERSIST_EVERY:
                _write(data)
                _PERSIST[did] = time.time()
            return _public(d)
    return None


def get(device_id):
    with _LOCK:
        d = _all().get(str(device_id or ""))
    return _public(d) if isinstance(d, dict) else None


def list_all():
    """设备列表：吊销的排在后面，同状态按最近上线倒序。"""
    with _LOCK:
        items = [_public(d) for d in _all().values() if isinstance(d, dict)]
    items.sort(key=lambda x: (x.get("status") != "active", x.get("last_seen") or ""), reverse=False)
    items.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    return items


def revoke(device_id):
    """吊销：**不删记录**，把 status 改成 revoked。

    留着是为了"这台是几号被吊销的"查得到；删掉就什么都看不出来了。
    已经吊销过的再吊销返回 False —— 调用方要能区分"我刚解绑了"
    和"它本来就是解绑状态"，否则界面会报一个假的成功。
    """
    did = str(device_id or "")
    with _LOCK:
        data = _all()
        d = data.get(did)
        if not isinstance(d, dict):
            return False
        if d.get("status") == "revoked":
            return False
        d["status"] = "revoked"
        d["revoked_at"] = _now()
        _write(data)
        _cache = data
    return True


def tokens_seen():
    """给测试用：当前表里出现过几个 token 哈希（不该含明文）。"""
    with _LOCK:
        return [str(d.get("token_hash") or "") for d in _all().values() if isinstance(d, dict)]
