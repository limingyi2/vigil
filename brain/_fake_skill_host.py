# -*- coding: utf-8 -*-
"""起一个假 skill 宿主，让「真装一个技能」那两条也能真跑。

## 为什么需要它

`_test_skill_admin_http.py` 的 6/7 两条（真装、真卸）在没装 Node 的机器上
只能 SKIP。可这两条恰好是**最容易出错**的：包根判断、备份不覆盖、
装完打 /reload、卸完留 .bak_ —— 全在 `_install_zip` / `_uninstall` 里，
本地那 43 项单元测试打的是桩函数，不是真的 HTTP 往返。

所以这里用 http.server 照 `skill_host/server.js` 的契约实现一个假宿主
（/health、/skills、/reload），不下载 Node、不装任何东西。
它验的是**大脑跟宿主之间的协议对不对**，不是验 server.js 本身。

## 契约从哪来（照抄 server.js，改了这里要跟那边一起改）

    GET  /health      -> {ok:true, skills:N, dir:...}
    GET  /skills      -> {ok:true, skills:[{id,name,version,description,tools:[...]}]}
    POST /reload      -> {ok:true, reloaded:N}

🔴 **`/skills` 的 `tools[].name` 必须是全名**（`skill前缀.工具名`），
`server.js` 里存的是 `t.full`，而 `skill.py` 的 `list_skills` 直接透传。
假宿主要是图省事只写短名，测出来的「工具对得上」是假的。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


class _Host(BaseHTTPRequestHandler):
    """假宿主。技能清单存在 self.server.skills。"""

    def log_message(self, *a):
        pass                      # 别把请求日志糊满输出

    def _write(self, raw, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, obj, code=200):
        self._write(json.dumps(obj).encode("utf-8"), code)

    def do_GET(self):
        if self.path == "/health":
            return self._json({"ok": True, "skills": len(self.server.skills),
                               "dir": self.server.skill_dir})
        if self.path == "/skills":
            return self._json({"ok": True, "skills": self.server.skills})
        return self._json({"ok": False, "err": "no route"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        if self.path == "/reload":
            # 真宿主这里是 loadAll()，会重扫目录。假的最逼真的做法也是重扫 ——
            # 不重扫的话，"装完 reload 之后列表才有" 这条判据就永远不成立，
            # 等于没测。
            self.server.reload_count += 1
            self.server.rescan()
            return self._json({"ok": True, "reloaded": len(self.server.skills)})
        return self._json({"ok": False, "err": "no route"}, 404)


class FakeHost(object):
    def __init__(self, skill_dir, port=0):
        self.skill_dir = skill_dir
        self.skills = []
        self.reload_count = 0
        self.srv = HTTPServer(("127.0.0.1", port), _Host)
        self.srv.skill_dir = skill_dir
        self.srv.skills = self.skills
        self.srv.reload_count = 0
        self.srv.rescan = self.rescan
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def start(self):
        self.th.start()
        return self

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def rescan(self):
        """重扫技能目录，模仿 loadAll()。"""
        out = []
        if not os.path.isdir(self.skill_dir):
            self.skills[:] = out
            return
        for name in sorted(os.listdir(self.skill_dir)):
            d = os.path.join(self.skill_dir, name)
            if name.startswith(".") or not os.path.isdir(d):
                continue
            if ".bak_" in name:
                continue
            meta = {}
            for root, _dirs, files in os.walk(d):
                if "skill.json" in files:
                    try:
                        with open(os.path.join(root, "skill.json"),
                                  encoding="utf-8") as f:
                            meta = json.load(f) or {}
                    except Exception:
                        meta = {}
                    break
            out.append({
                "id": name,
                "name": meta.get("name") or name,
                "version": meta.get("version") or "",
                "description": str(meta.get("description") or ""),
                # 🔴 全名：真宿主存的是 t.full（"包名.工具名"）。
                # 这里写短名的话，"工具名对得上" 这条判据就是假的。
                "tools": [{"name": "%s.hello" % name,
                           "title": "打招呼",
                           "desc": "测试用工具",
                           "parameters": {}}] if _has_entry(d) else [],
            })
        self.skills[:] = out


def _has_entry(d):
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f in ("index.js", "index.mjs", "index.cjs"):
                return True
    return False


if __name__ == "__main__":
    h = FakeHost(sys.argv[1] if len(sys.argv) > 1 else ".").start()
    print("fake host on 127.0.0.1:%d dir=%s" % (h.port, h.skill_dir))
    try:
        h.th.join()
    except KeyboardInterrupt:
        h.stop()
