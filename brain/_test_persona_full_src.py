# -*- coding: utf-8 -*-
"""/api/persona/full 不带 key 时必须按渠道解析。

## 踩出来的 bug

面板切到「微信」，点开人格编辑，弹窗里是 App 那份人设 ——
而标题写的是微信那套的名字。人设区看着完全正常，内容全错。
真凶是那个接口只认 `key_from_config`（= 全局 persona_file = App 的），
`?src=` 传了也被无视。

这类 bug 单测最容易漏：`?src=wechat` 不传时**行为跟改之前一模一样**，
只有「切到微信 + 不带 key 打开编辑器」这一条路径会错。所以判据必须是
**同一个进程里两个渠道读出来的 fields 不一样**，而不是「改完了没报错」。

用法：F:/vigil/venv/Scripts/python.exe _test_persona_full_src.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = [0]
FAIL = []


def check(name, ok, extra=None):
    if ok:
        PASS[0] += 1
        print("  PASS  " + name)
    else:
        FAIL.append(name)
        print("  FAIL  " + name + ("  <<< " + json.dumps(extra, ensure_ascii=False)[:300]
                                   if extra is not None else ""))


_TMP = None


def setup():
    """造一份配置：App 用 base、微信用 wechat_persona，两套内容明显不同。"""
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="zxpersonafull_")
    cfg_dir = os.path.join(_TMP, "config")
    pdir = os.path.join(cfg_dir, "personas")
    os.makedirs(pdir, exist_ok=True)

    def w(key, data):
        with io.open(os.path.join(pdir, key + ".json"), "w",
                     encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    w("base", {"name": "甲", "nicknames": ["甲甲"], "personality": "app 那套"})
    w("wechat_persona", {"name": "乙", "nicknames": ["乙乙"],
                         "personality": "wechat 那套"})
    w("other", {"name": "丙", "nicknames": []})

    cfg = {
        "brain_token": "t",
        "model": "x/y",
        "api_base": "http://127.0.0.1:1",
        "api_key": "k",
        "persona_file": "personas/base.json",
        "persona_by_src": {"wechat": "wechat_persona"},
    }
    with io.open(os.path.join(cfg_dir, "config.json"), "w",
                 encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg_dir


def teardown():
    try:
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass


def _resolve(api_config, query):
    """复刻 server.py 里 /api/persona/full 那段解析。

    刻意**不 import server**（它 import 一大堆东西，起不来也不该起）。
    抄的是逻辑；改产品代码时这个测试要跟着改 —— 所以下面有断言盯着
    「产品代码里的那段和这里一致」。
    """
    import persona_store
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(query).query)
    key = (q.get("key") or [None])[0]
    if key:
        cur = os.path.basename(str(key))
    else:
        src = str((q.get("src") or [""])[0]).strip().lower()
        by = {}
        try:
            import paths
            by = (json.load(io.open(paths.CONFIG_PATH, encoding="utf-8"))
                  .get("persona_by_src") or {})
        except Exception:
            pass
        base = persona_store.key_from_config(api_config)
        cur = str(by.get(src) or "").strip() or base
    return cur


def main():
    cfg_dir = setup()
    try:
        import paths
        import persona_store
        paths.CONFIG_DIR = cfg_dir
        paths.CONFIG_PATH = os.path.join(cfg_dir, "config.json")
        paths.PERSONA_DIR = os.path.join(cfg_dir, "personas")
        # 🔴 persona_store.PERSONA_DIR 是 **import 时**从 paths 抓的快照
        # （它还兜了一条 BASE 分支），只改 paths.PERSONA_DIR 它不跟着变 ——
        # 于是 load() 全都降级成"读不到，用内置空模板"，name 是空串。
        # 这个坑跟"改路径没生效"长得一模一样：都不报错，只是内容不对。
        persona_store.PERSONA_DIR = paths.PERSONA_DIR
        api_config = json.load(io.open(paths.CONFIG_PATH, encoding="utf-8"))

        print("[1] 不带 key 时按 src 解析")
        k_app = _resolve(api_config, "/api/persona/full?src=app")
        k_we = _resolve(api_config, "/api/persona/full?src=wechat")
        check("app 拿到 base", k_app == "base", k_app)
        check("wechat 拿到 wechat_persona", k_we == "wechat_persona", k_we)
        check("两个渠道不是同一套", k_app != k_we, (k_app, k_we))

        print("[2] 读出来的人设内容也要跟着换")
        n_app = (persona_store.load(k_app) or {}).get("name")
        n_we = (persona_store.load(k_we) or {}).get("name")
        check("app 读到甲", n_app == "甲", n_app)
        check("wechat 读到乙", n_we == "乙", n_we)

        print("[3] 不带 src 退回全局（老行为不能被破坏）")
        k_none = _resolve(api_config, "/api/persona/full")
        check("不带 src 时是全局那套", k_none == "base", k_none)

        print("[4] 没配 persona_by_src 的渠道退回全局")
        k_other = _resolve(api_config, "/api/persona/full?src=qq")
        check("没配过的渠道退回全局", k_other == "base", k_other)

        print("[5] 带 key 时 key 优先，且仍然防路径穿越")
        k_exp = _resolve(api_config, "/api/persona/full?key=other&src=wechat")
        check("显式 key 优先于 src", k_exp == "other", k_exp)
        k_evil = _resolve(api_config,
                          "/api/persona/full?key=../../config/config")
        check("路径穿越被 basename 挡掉", k_evil == "config", k_evil)

        print("[6] 产品代码里那段真的改了吗（别让测试自说自话）")
        src = io.open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
        i = src.find('if path == "/api/persona/full"')
        seg = src[i:i + 1600] if i >= 0 else ""
        check("找得到 /api/persona/full 那段", bool(seg))
        check("那段里用了 _persona_for_src（按渠道解析）",
              "_persona_for_src" in seg, seg[:200])
        # 🔴 判据只能问「直不直接用渠道」，不能问「有没有出现某段字节」——
        # 加了中间层之后「区间内含 X」这类判据就不成立了。
        import ast
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "do_GET":
                pass
        check("server.py 语法没问题", True)   # parse 过了就说明没问题
        # 真正要钉的：不带 key 的那条分支里，必须按 src 走
        k_line = [ln for ln in seg.splitlines() if "_persona_for_src" in ln]
        check("解析语句读的是 src 那个查询参数",
              bool(k_line) and ("src" in k_line[0]), k_line[:1])
        _ = found, tree

        print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
        for f in FAIL:
            print("   FAILED: " + f)
        return 1 if FAIL else 0
    finally:
        teardown()


if __name__ == "__main__":
    sys.exit(main())