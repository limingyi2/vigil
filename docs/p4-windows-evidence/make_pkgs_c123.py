# -*- coding: utf-8 -*-
"""C1/C2/C3 真实 UI 验收专用造包脚本（临时脚本，随证据目录入库）。

与 make_pkgs.py 的关系：那个给 28 项原生用例造包，本文件只造 C123 轮新增的包，
两者互不影响、可分别重跑。关键差异是 p4plugbg —— 它带真正的 start() 后台线程
（每 0.5s 往 VIGIL_HOME 写一行心跳），用来在进程级证明：
"卸载只删了文件，start() 起的线程还在跑"—— 这正是 C2 要用户知道的事。
"""
import json
import os
import threading
import zipfile

ROOT = os.path.join(os.environ["TEMP"], "vigil_p4")
PKG = os.path.join(ROOT, "pkgs")
os.makedirs(PKG, exist_ok=True)


def write_zip(name, files):
    p = os.path.join(PKG, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in files.items():
            z.writestr(arc, data)
    print("wrote", name, sorted(files))


# ── 插件：无 start 钩子（卸载不该声称"后台线程还在跑"） ──────────────
def plug_py(ver, marker):
    return f'''# -*- coding: utf-8 -*-
"""P4 C123 验收临时插件 {ver}（无 start 钩子）。"""
NAME = "p4plug"
DESC = "P4 验收测试插件 {ver}"
VERSION = "{ver}"
AUTHOR = "p4-accept"


def _t_ping(arg):
    return "{marker}"


TOOLS = {{"p4ping": _t_ping}}
'''


# ── 插件：带 start() 后台线程（卸载后线程仍存活，直到大脑重启） ──────
def plugbg_py(ver):
    return f'''# -*- coding: utf-8 -*-
"""P4 C123 验收临时插件 {ver}：带 start() 后台线程。

start() 起一个守护线程，每 0.5s 往 <VIGIL_HOME>/p4plugbg_heartbeat.log 追一行。
心跳文件放在 plugins/ 外面：卸载只会删 plugins/ 里的 .py，碰不到它 ——
于是"文件没了、线程还在跑"这件事可以被直接观测（心跳继续长），而不是只靠
界面上一句话。大脑重启后插件文件没了，线程随之消失，心跳停止。
"""
import os
import threading
import time

NAME = "p4plugbg"
DESC = "P4 验收：带后台线程的插件"
VERSION = "{ver}"
AUTHOR = "p4-accept"

_HB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "p4plugbg_heartbeat.log")


def _loop():
    while True:
        try:
            with open(_HB, "a", encoding="utf-8") as f:
                f.write("%.3f\\n" % time.time())
        except Exception:
            pass
        time.sleep(0.5)


def start(brain):
    threading.Thread(target=_loop, daemon=True).start()


def _t_ping(arg):
    return "P4BG-PONG"


TOOLS = {{"p4bgping": _t_ping}}
'''


def plug_zip(fname, name, ver, py):
    write_zip(fname, {name + ".py": py,
                      "zxplugin.json": json.dumps(
                          {"name": name, "version": ver,
                           "author": "p4-accept",
                           "desc": "P4 C123 验收用插件"})})


plug_zip("p4plug-v1.zip", "p4plug", "1.0.0", plug_py("1.0.0", "P4PONG-V1"))
plug_zip("p4plug-v2.zip", "p4plug", "2.0.0", plug_py("2.0.0", "P4PONG-V2"))
plug_zip("p4plugbg-v1.zip", "p4plugbg", "1.0.0", plugbg_py("1.0.0"))


# ── Skill：与 28 项轮相同的最小可过检形状 ────────────────────────────
def skill_js(ver, marker):
    return f'''// P4 C123 验收临时技能 {ver}
export function setup(api) {{
  api.registerTool({{
    id: 'echo',
    name: 'p4echo',
    description: 'P4 验收用回声工具',
    parameters: {{ type: 'object', properties: {{}} }},
    async execute(ctx, args) {{
      return {{ content: '{marker}' }};
    }},
  }});
}}
export const version = '{ver}';
'''


def skill_zip(fname, ver, marker):
    meta = {"id": "p4skill", "name": "P4验收技能", "version": ver,
            "description": "P4 C123 验收用临时技能", "entry": "index.js"}
    write_zip(fname, {"p4skill/skill.json": json.dumps(meta, ensure_ascii=False),
                      "p4skill/index.js": skill_js(ver, marker)})


skill_zip("p4skill-v1.zip", "1.0.0", "P4-SKILL-V1")
skill_zip("p4skill-v2.zip", "2.0.0", "P4-SKILL-V2")
print("done")
