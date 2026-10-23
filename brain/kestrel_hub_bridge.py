"""Optional, read-only connection to a Kestrel-compatible nanoMuse Hub.

The Hub is an outbound WebSocket router. This bridge never exposes Vigil's HTTP API
and answers only the existing ``info`` call. Credentials are protected with Windows DPAPI.
"""
import ctypes
import ctypes.wintypes
import json
import os
import platform
import socket
import sys
import threading
import uuid

import httpx
import paths

DEFAULT_RELAY = "https://relay.palimpsestai.xyz"
_stop = threading.Event()
_thread = None


def _credential_path():
    return os.path.join(paths.DATA_DIR, "kestrel_hub.dpapi")


def _dpapi(data, protect):
    if os.name != "nt":
        raise RuntimeError("Kestrel Hub credentials require Windows DPAPI")
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    raw = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.windll.crypt32
    ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)) if protect else \
        crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def load_credentials():
    try:
        with open(_credential_path(), "rb") as f:
            return json.loads(_dpapi(f.read(), False).decode("utf-8"))
    except FileNotFoundError:
        return None


def save_credentials(value):
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    encrypted = _dpapi(json.dumps(value, separators=(",", ":")).encode("utf-8"), True)
    path = _credential_path()
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(encrypted)
    os.replace(tmp, path)


def _device_id():
    saved = load_credentials() or {}
    return saved.get("device_id") or "pc-" + uuid.uuid4().hex[:12]


def device_info():
    """A small public inventory; does not query Brain, files, processes, or secrets."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "version.json"), encoding="utf-8") as f:
            version = str(json.load(f).get("version") or "development")
    except (OSError, TypeError, ValueError):
        version = "development"
    return {
        "name": socket.gethostname()[:60],
        "os": platform.platform()[:120],
        "version": version,
        "actions": ["info"],
    }


def _handle_message(ws, raw):
    try:
        frame = json.loads(raw)
        if not isinstance(frame, dict) or frame.get("type") != "call":
            return
        call_id = str(frame.get("id") or "")[:80]
        if not call_id:
            return
        if frame.get("action") != "info":
            reply = {"type": "result", "id": call_id, "ok": False,
                     "error": "not_allowed", "message": "This Bridge supports read-only device info only"}
        else:
            reply = {"type": "result", "id": call_id, "ok": True, "body": device_info()}
        ws.send(json.dumps(reply, separators=(",", ":")))
    except Exception as exc:  # noqa: BLE001 - isolate malformed frames from the socket worker
        print(f"[Kestrel Hub] ignored an invalid call ({type(exc).__name__})", flush=True)


def _connect_loop(credentials):
    try:
        import websocket
    except ImportError:
        print("[Kestrel Hub] websocket-client is not installed; Bridge is disabled", flush=True)
        return
    base = str(credentials.get("relay") or DEFAULT_RELAY).rstrip("/")
    url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/hub"
    delay = 1
    while not _stop.is_set():
        def opened(ws):
            ws.send(json.dumps({
                "type": "hello",
                "device": {
                    "id": credentials["device_id"], "name": device_info()["name"],
                    "kind": "computer", "os": device_info()["os"],
                    "version": device_info()["version"], "actions": ["info"],
                },
            }, separators=(",", ":")))

        ws = websocket.WebSocketApp(
            url, header={"Authorization": "Bearer " + credentials["api_key"]},
            on_open=opened, on_message=_handle_message,
            on_error=lambda _ws, exc: print(f"[Kestrel Hub] connection error: {type(exc).__name__}", flush=True),
        )
        try:
            ws.run_forever(ping_interval=25, ping_timeout=10)
        except websocket.WebSocketException as exc:
            print(f"[Kestrel Hub] connection stopped: {type(exc).__name__}", flush=True)
        if _stop.wait(delay):
            break
        delay = min(delay * 2, 30)


def start_from_config(_api_config=None):
    """Start only when the owner has explicitly configured a Relay credential."""
    global _thread
    credentials = load_credentials()
    if not credentials or not credentials.get("api_key"):
        return False
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("[Kestrel Hub] websocket-client is missing; install the Brain requirements", flush=True)
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_connect_loop, args=(credentials,), name="kestrel-hub", daemon=True)
    _thread.start()
    return True


def _login():
    if os.name != "nt":
        raise RuntimeError("Relay Bridge credential setup is available on Windows only")
    relay = (input("Relay URL [https://relay.palimpsestai.xyz]: ").strip() or DEFAULT_RELAY).rstrip("/")
    if not relay.startswith("https://"):
        raise ValueError("Relay URL must use HTTPS")
    identifier = input("Account email: ").strip()
    with httpx.Client(timeout=20) as client:
        response = client.post(relay + "/v1/auth/code", json={"identifier": identifier})
        response.raise_for_status()
        code = input("Email code: ").strip()
        did = _device_id()
        result = client.post(relay + "/v1/auth/verify", json={
            "identifier": identifier, "code": code, "device": socket.gethostname(),
            "device_id": did, "device_kind": "computer",
        })
        result.raise_for_status()
        payload = result.json()
    if not payload.get("api_key"):
        raise RuntimeError("Relay response did not include an account credential")
    save_credentials({"relay": relay, "api_key": payload["api_key"], "device_id": did})
    print("Relay credential saved with Windows DPAPI. Restart Vigil Brain to connect.")


def _logout():
    credentials = load_credentials()
    if credentials:
        with httpx.Client(timeout=10) as client:
            try:
                client.post(credentials["relay"].rstrip("/") + "/v1/auth/sign-out",
                            headers={"Authorization": "Bearer " + credentials["api_key"]})
            except httpx.HTTPError as exc:
                print(f"Relay sign-out could not be confirmed: {type(exc).__name__}", flush=True)
    try:
        os.remove(_credential_path())
    except FileNotFoundError:
        pass
    _stop.set()
    print("Local Hub credential removed.")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "login":
        _login()
    elif action == "logout":
        _logout()
    elif action == "status":
        value = load_credentials()
        print("Connected credentials configured" if value else "Not configured")
    else:
        raise SystemExit("Usage: python brain/kestrel_hub_bridge.py [login|logout|status]")


if __name__ == "__main__":
    main()
