# -*- coding: utf-8 -*-
"""P4 联动验收：构造全部测试 zip 包（临时脚本，不入库）。"""
import json
import os
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


# ── 插件：单文件 .py，热加载需要重启 Brain ──────────────────────
def plug_py(ver, marker):
    return f'''# -*- coding: utf-8 -*-
"""P4 联动验收临时插件 {ver}（验收后随卸载/临时 HOME 清除）。"""
NAME = "p4plug"
DESC = "P4 验收测试插件 {ver}"
VERSION = "{ver}"
AUTHOR = "p4-accept"


def _t_ping(arg):
    return "{marker}"


TOOLS = {{"p4ping": _t_ping}}
'''


write_zip("p4plug-v1.zip", {"p4plug.py": plug_py("1.0.0", "P4PONG-V1"),
                           "zxplugin.json": json.dumps(
                               {"name": "p4plug", "version": "1.0.0",
                                "author": "p4-accept",
                                "desc": "P4 验收测试插件"})})
write_zip("p4plug-v2.zip", {"p4plug.py": plug_py("2.0.0", "P4PONG-V2"),
                           "zxplugin.json": json.dumps(
                               {"name": "p4plug", "version": "2.0.0",
                                "author": "p4-accept",
                                "desc": "P4 验收测试插件（更新）"})})

# 非法插件 1：zip 内含路径穿越名
write_zip("p4plug-evil-traversal.zip",
          {"../escape.py": "print('escaped')\n"})

# 非法插件 2：两个互不相干的 .py 根（结构检查拒绝）
write_zip("p4plug-evil-tworoots.zip",
          {"aaa.py": "NAME='aaa'\n", "bbb.py": "NAME='bbb'\n"})

# ── Skill：单层目录 skill.json + index.js（QQ Agent register 契约）──
def skill_js(ver, marker, *, broken=False, no_setup=False):
    if broken:
        return "export function setup(api) {{\n  this is not javascript (((\n"
    if no_setup:
        return "export function hello(api) { return 'no-setup' }\n"
    return f'''// P4 联动验收临时技能 {ver}
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


def skill_zip(fname, ver, marker, entry="index.js",
              meta_id="p4skill", **kw):
    meta = {"id": meta_id, "name": "P4验收技能", "version": ver,
            "description": "P4 Windows 联动验收用临时技能", "entry": entry}
    files = {f"p4skill/skill.json": json.dumps(meta, ensure_ascii=False),
             f"p4skill/{entry}": skill_js(ver, marker, **kw)}
    write_zip(fname, files)


skill_zip("p4skill-v1.zip", "1.0.0", "P4-SKILL-V1")
skill_zip("p4skill-v2.zip", "2.0.0", "P4-SKILL-V2")
skill_zip("p4skill-v2-broken.zip", "2.0.0-broken", "X", broken=True)
skill_zip("p4skill-nosetup.zip", "0.9.0", "X", no_setup=True)

# 非法 Skill：entry 越出 skill 根
write_zip("p4skill-evil-entry.zip", {
    "p4skill/skill.json": json.dumps({"id": "p4skill", "entry": "../../evil.js"}),
    "p4skill/index.js": skill_js("x", "X"),
})

# 双层包（外层同名目录，宿主应钻一层）
write_zip("p4skill-double.zip", {
    "p4skill/p4skill/skill.json": json.dumps(
        {"id": "p4skill", "name": "P4验收技能", "version": "1.1.0",
         "description": "双层包"}, ensure_ascii=False),
    "p4skill/p4skill/index.js": skill_js("1.1.0", "P4-SKILL-DOUBLE"),
})
print("done")
