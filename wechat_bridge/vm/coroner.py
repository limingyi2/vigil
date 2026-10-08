"""zhixia bridge coroner -- OUT-OF-PROCESS watchdog.

Why this exists (2026-10-08, proven on the bridge machine):
wxauto4's native UIA calls occasionally wedge while HOLDING THE GIL.
When that happens every Python thread inside the bridge freezes too:
the in-process watchdog thread, dump_traceback_later, the janitor
thread -- none of them can run. Only an external process can detect
and kill the wedged bridge.

Mechanism:
1. The bridge touches C:/zx/hb.stamp on every poll loop (~5s).
2. This coroner checks the stamp every 30s. Stale > 420s => the main
   loop is frozen => taskkill the wxauto_bridge.py python process.
   The launcher's relaunch loop brings a fresh bridge up in 20s.
3. Bonus duty (moved from the in-process janitor, for the same GIL
   reason): close leftover WeChat transient popup menus. EnumWindows
   + keybd_event are pure win32, no UIA, no GIL dependency on the
   bridge -- works even while the bridge is wedged.

Pure stdlib, ASCII only, runs headless from a scheduled task.
"""

import ctypes
import os
import subprocess
import time

HB = r"C:\zx\hb.stamp"
MYLOG = r"C:\zx\coroner.log"
STALE = 420          # seconds without heartbeat => wedged
MENU_PERSIST = 8.0   # seconds a popup menu must linger before Esc
CHECK_EVERY = 30

user32 = ctypes.windll.user32


def log(msg):
    try:
        with open(MYLOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def find_bridge_pids():
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where",
             "name='python.exe' and commandline like '%wxauto_bridge.py%'",
             "get", "processid"],
            text=True, errors="replace")
    except Exception as e:
        log("wmic failed: %r" % e)
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip().isdigit()]


def kill_stale_bridge():
    age = time.time() - os.path.getmtime(HB)
    pids = find_bridge_pids()
    if not pids:
        # bridge not running at all; launcher may be between restarts.
        # only complain if it stays absent for a long while.
        return
    log("heartbeat stale %.0fs -> killing bridge pid(s) %s" % (age, pids))
    for p in pids:
        try:
            subprocess.call(["taskkill", "/F", "/PID", p])
        except Exception as e:
            log("taskkill %s failed: %r" % (p, e))


# ---- leftover popup menu closer (pure win32) -------------------------

CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_menu_seen = {}


def _enum_transient_menus():
    found = []
    now = time.time()

    def _cb(h, _l):
        try:
            if not user32.IsWindowVisible(h):
                return 1
            c = ctypes.create_unicode_buffer(96)
            user32.GetClassNameW(h, c, 96)
            cn = c.value or ""
            if cn.startswith("Qt") and cn.endswith("ToolSaveBits"):
                found.append(int(h) & 0xFFFFFFFF)
        except Exception:
            pass
        return 1

    ref = CB(_cb)
    user32.EnumWindows(ref, 0)

    global _menu_seen
    _menu_seen = {h: ts for h, ts in _menu_seen.items() if h in found}
    for h in found:
        if h in _menu_seen and now - _menu_seen[h] >= MENU_PERSIST:
            fg = user32.GetForegroundWindow()
            fg = (int(fg) & 0xFFFFFFFF) if fg else 0
            if fg == h:
                user32.keybd_event(0x1B, 0, 0, 0)
                user32.keybd_event(0x1B, 0, 2, 0)
                log("closed leftover popup menu h=%08x" % h)
            _menu_seen.pop(h, None)
        elif h not in _menu_seen:
            _menu_seen[h] = now


def main():
    log("coroner started (pid %s), stale=%ss" % (os.getpid(), STALE))
    while True:
        time.sleep(CHECK_EVERY)
        try:
            if os.path.exists(HB):
                if time.time() - os.path.getmtime(HB) > STALE:
                    kill_stale_bridge()
            else:
                log("no heartbeat file yet: %s" % HB)
        except Exception as e:
            log("heartbeat check error: %r" % e)
        try:
            _enum_transient_menus()
        except Exception:
            pass


if __name__ == "__main__":
    main()
