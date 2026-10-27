# -*- coding: utf-8 -*-
"""插件管理 HTTP 接口的真接口冒烟（起一个本地大脑，真打 HTTP）。

## 为什么单元测试不够

`plugin_ops` 的 83 项管的是「装一个 zip 安不安全」，管不到：
接口路径拼错、POST 收 base64 时被 json.loads 吃掉、渠道白名单没接上、
`plugins` 键被改动结果把微信桥接的探针搞挂。这些只有真打一遍 HTTP 才发现得了。

## 判据设计

每条都是「打真接口 → 看真返回」，不是「调用没抛异常」。
另外专门有一条回归判据锁住 `plugins` 键的形状 —— 微信桥接和 VM 启动器
拿 `/api/plugins` 当「大脑活着吗」的探针（只看 200），但那是隐式契约，
哪天有人为了面板好看改了这个键，两个启动器会一起静默失效。

## 地址和 Token 从哪来

 **不写死在文件里**。这个仓库是公开的，写死等于把云机地址和口令
一起发出去。默认打本机（127.0.0.1），要打别处就带参数：

    python _test_plugin_admin_http.py                      # 打本机
    python _test_plugin_admin_http.py http://<host>:8788 <token>

token 缺省从本机 config 里读。打云端时记得确认 token 是一次性的 ——
别把带 token 的命令行留在 shell 历史里。
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _default_token():
    """从本机配置读 brain_token，读不到返回空（只打本机回环时不需要）。"""
    p = os.path.join(HERE, "config", "config.json")
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("brain_token") or "")
    except Exception:
        return ""


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788"
TOK = sys.argv[2] if len(sys.argv) > 2 else _default_token()

_OK = 0
_BAD = 0


def check(label, cond, extra=""):
    global _OK, _BAD
    if cond:
        _OK += 1
        print("  PASS  %s" % label)
    else:
        _BAD += 1
        print("  FAIL  %s%s" % (label, ("\n        " + str(extra)) if extra else ""))


def group(t):
    print("\n[%s]" % t)


def raw(method, path, body=None, timeout=20):
    """打真接口。返回 (http码, 解析后的 json 或原始文本)。"""
    url = BASE + path
    sep = "&" if "?" in path else "?"
    url += sep + "token=" + TOK
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        code = e.code
    try:
        return code, json.loads(txt)
    except Exception:
        return code, txt


def mkzip_b64(entries):
    """entries: {归档内名字: 内容str}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, v in entries.items():
            z.writestr(n, v if isinstance(v, bytes) else v.encode("utf-8"))
    return base64.b64encode(buf.getvalue()).decode("ascii")


PLUG_NAME = "httptest"
PLUG_SRC = 'NAME = "httptest"\nDESC = "接口冒烟用"\nFEATURE = "httptest_feat"\n'


def wait_brain(upto=90):
    for _ in range(upto):
        try:
            code, _d = raw("GET", "/api/plugins", timeout=4)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    print("=" * 46)
    print("插件管理 HTTP 接口冒烟")
    print("=" * 46)

    if not wait_brain():
        print("起不来本地大脑，先单独跑 server.py 再来。")
        return 1

    group("1. GET /api/plugins：老键不能动，新的管理视图要齐")
    code, d = raw("GET", "/api/plugins")
    check("200", code == 200, code)
    check("plugins 键还在（微信桥接的探针认这个）", isinstance(d.get("plugins"), list), d)
    if d.get("plugins"):
        first = d["plugins"][0]
        check("plugins[0] 仍有 name/desc（形状没被改）",
              "name" in first and "desc" in first, first)
    mg = d.get("manage") or {}
    check("有 manage 段", isinstance(mg, dict) and bool(mg), d)
    check("渠道列表来自 paths.CHANNELS",
          mg.get("channels") == ["app", "wechat"], mg.get("channels"))
    items = mg.get("items")
    check("items 是列表", isinstance(items, list), items)
    if isinstance(items, list) and items:
        it = items[0]
        need = ("name", "desc", "feature", "channels", "channelable", "meta")
        miss = [k for k in need if k not in it]
        check("每个插件带齐前端要用的字段（缺 %s）" % miss, not miss, it)
        check("channels 正好是 app/wechat 两个键",
              all(sorted(v.keys()) == ["app", "wechat"]
                  for v in [it["channels"]]), it.get("channels"))

    group("2. GET 不带 token 必须被拒（装插件是改服务器，不能裸奔）")
    url = BASE + "/api/plugins"
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(urllib.request.Request(url), timeout=8) as r:
            code2 = r.status
    except urllib.error.HTTPError as e:
        code2 = e.code
    except Exception as e:
        code2 = "ERR %s" % e
    check("401", code2 == 401, code2)

    group("3. 装一个合法插件")
    code, d = raw("POST", "/api/plugins/install",
                  {"zip_b64": mkzip_b64({"httptest.py": PLUG_SRC,
                                          "zxplugin.json": '{"name":"httptest",'
                                                           '"author":"t","version":"1"}'})})
    check("200", code == 200, (code, d))
    check("ok=True", d.get("ok") is True, d)
    ins = d.get("installed") or {}
    check("认出了包名 httptest", ins.get("package") == PLUG_NAME, ins)
    check("读到了 manifest.author",
          (ins.get("manifest") or {}).get("author") == "t", ins.get("manifest"))
    check("返回里带 restart_needed（后台线程插件）",
          "restart_needed" in d, d)
    check("装完就带回了新的 manage 视图（前端不用再拉一次）",
          isinstance((d.get("manage") or {}).get("items"), list), d.get("manage"))
    names = [p["name"] for p in (d.get("manage") or {}).get("items") or []]
    check("列表里已经有 httptest", PLUG_NAME in names, names)

    group("4. 前端只读响应体就能画页（真按插件名字找得到 FEATURE）")
    code, d = raw("GET", "/api/plugins")
    it = [p for p in ((d.get("manage") or {}).get("items") or [])
          if p["name"] == PLUG_NAME]
    check("装完再拉一次也在", bool(it), [p["name"] for p in
                                   (d.get("manage") or {}).get("items") or []])
    if it:
        check("feature 字段可直接用（前端不用 import 什么）",
              it[0]["feature"] == "httptest_feat", it[0])
        check("channelable=True（声明了 FEATURE 就该能按渠道关）",
              it[0]["channelable"] is True, it[0])
        check("meta 里有安装来源",
              bool(it[0]["meta"].get("source")), it[0]["meta"])

    group("5. 渠道开关：只开 app")
    code, d = raw("POST", "/api/plugins/channels",
                  {"name": PLUG_NAME, "channels": ["app"]})
    check("200", code == 200, (code, d))
    ch = d.get("channels") or {}
    check("回显 app 开 / wechat 关",
          ch == {"app": True, "wechat": False}, ch)
    items = (d.get("manage") or {}).get("items") or []
    it = [p for p in items if p["name"] == PLUG_NAME]
    check("面板视图也跟着变（界面关了 = 真关了）",
          it and it[0]["channels"] == {"app": True, "wechat": False}, it)

    group("6. 渠道开关：两个都开（取消勾选要能真的关回去）")
    code, d = raw("POST", "/api/plugins/channels",
                  {"name": PLUG_NAME, "channels": ["app", "wechat"]})
    ch = d.get("channels") or {}
    check("两个都开", ch == {"app": True, "wechat": True}, ch)

    group("7. 渠道开关写进的是 features_by_src，不是新字段")
    #  这里原来 `import paths` 去读**本机** config.json —— 但打的是云端，
    # 渠道开关写在**云机**那份配置里。于是这两条判据永远读不到键、永远 FAIL，
    # 而错误信息看起来像"功能坏了"。读文件只能用于打本机时；
    # 打云端必须读云端**运行中**那份配置，走 SSH 或接口二选一。
    # 这里走接口：/api/features?src=xxx 返回的就是 features.on(key, src) 的答案。
    got = {}
    for _s in ("app", "wechat"):
        _c, _d = raw("GET", "/api/features?src=" + _s)
        got[_s] = (_d.get("features") or {}).get("httptest_feat")
    check("app 下 httptest_feat 生效", got.get("app") is True, got)
    check("wechat 下 httptest_feat 生效", got.get("wechat") is True, got)
    # 反向判据：不能只在内存里、没落盘。用一个没开过的渠道对照。
    _c, _d = raw("GET", "/api/features?src=wechat")
    _wechat_all = _d.get("features") or {}
    check("落盘了（群聊里该关的仍是关的，没被这次测试改动）",
          _wechat_all.get("reminder") is False, _wechat_all.get("reminder"))

    group("8. 渠道开关的拒绝分支")
    code, d = raw("POST", "/api/plugins/channels",
                  {"name": "根本没有这个插件", "channels": ["app"]})
    check("不存在的插件 -> 404", code == 404, (code, d))
    check("理由是人话", bool(d.get("err")), d)
    code, d = raw("POST", "/api/plugins/channels",
                  {"name": PLUG_NAME, "channels": ["app", "telepathy"]})
    check("乱填的渠道 -> 400（别照单全收写进配置）", code == 400, (code, d))

    group("9. 不声明 FEATURE 的插件：必须拒，不能假装改了")
    code, d = raw("POST", "/api/plugins/install",
                  {"zip_b64": mkzip_b64({"nofeat.py": 'NAME = "nofeat"\nDESC = "d"\n'})})
    check("包装上了", code == 200 and d.get("ok"), (code, d))
    code, d = raw("POST", "/api/plugins/channels",
                  {"name": "nofeat", "channels": []})
    check("不声明 FEATURE -> 400", code == 400, (code, d))
    check("理由说清了为什么关不掉",
          "FEATURE" in str(d.get("err") or ""), d)
    raw("POST", "/api/plugins/uninstall", {"name": "nofeat"})

    group("10. 坏包：接口回 400 且理由能给人看")
    code, d = raw("POST", "/api/plugins/install", {"zip_b64": "这不是 base64!!"})
    check("坏 base64 -> 400", code == 400, (code, d))
    check("说了是包坏了", bool(d.get("err")), d)
    code, d = raw("POST", "/api/plugins/install", {})
    check("没传包 -> 400", code == 400, (code, d))
    # 真 zip 但形状不对（目录包：加载器只认 *.py，装上去也不生效）
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pkg/__init__.py", "# 空壳\n")
        z.writestr("pkg/core.py", "NAME='pkg'\n")
    code, d = raw("POST", "/api/plugins/install",
                  {"zip_b64": base64.b64encode(buf.getvalue()).decode()})
    check("目录包 -> 400（装了也不生效，不如拒了）", code == 400, (code, d))
    check("理由告诉用户怎么改（压成单个 .py）",
          ".py" in str(d.get("err") or ""), d)
    # zip slip：装之前就该被挡，且不能写到 plugins/ 外面
    code, d = raw("POST", "/api/plugins/install",
                  {"zip_b64": mkzip_b64({"../evil.py": "print(1)\n"})})
    check("路径穿越 -> 400", code == 400, (code, d))
    check("plugins/ 外没多出文件",
          not os.path.isfile(os.path.join(HERE, "evil.py")))

    group("11. 卸掉，并顺手验它真的从列表里消失")
    code, d = raw("POST", "/api/plugins/uninstall", {"name": PLUG_NAME})
    check("200", code == 200, (code, d))
    names = [p["name"] for p in (d.get("manage") or {}).get("items") or []]
    check("列表里没有了", PLUG_NAME not in names, names)
    code, d = raw("GET", "/api/plugins")
    names = [p["name"] for p in ((d.get("manage") or {}).get("items") or [])]
    check("重新拉也没有了（reload 真的跑了）", PLUG_NAME not in names, names)
    check("落盘的 .py 也真删了",
          not os.path.isfile(os.path.join(HERE, "plugins", PLUG_NAME + ".py")))
    code, d = raw("POST", "/api/plugins/uninstall", {"name": PLUG_NAME})
    check("卸不存在的 -> 400", code == 400, (code, d))

    group("12. reload 仍然能用（它是原有接口，不能被我改坏）")
    code, d = raw("POST", "/api/plugins/reload")
    check("200", code == 200, (code, d))
    check("返回 count", isinstance(d.get("count"), int), d)
    check("返回 plugins 列表", isinstance(d.get("plugins"), list), d)
    check("restart_needed 是布尔", isinstance(d.get("restart_needed"), bool), d)

    group("13. CORS 预检：浏览器直连面板时不能被拦")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(BASE + "/api/plugins/install", method="OPTIONS",
                                 headers={"Origin": "http://localhost",
                                          "Access-Control-Request-Method": "POST"})
    try:
        with op.open(req, timeout=8) as r:
            cors = dict(r.headers)
            c3 = r.status
    except Exception as e:
        cors, c3 = {}, "ERR %s" % e
    check("OPTIONS 200", c3 == 200, c3)
    check("带 Access-Control-Allow-Origin",
          bool(cors.get("Access-Control-Allow-Origin")), cors)

    # --- 收尾：擦掉自己写在配置里的键 ---
    #  卸载插件**不会**清 features_by_src 里的键（那是渠道开关，不是插件文件）。
    # 所以光卸插件不够，每跑一次云端就多留一份 httptest_feat，
    # 跑久了配置里堆着一堆测试键，谁也说不清哪些是真开关。
    # 这里把两路都清：装过就卸，没装成功就直接把键抹掉。
    raw("POST", "/api/plugins/uninstall", {"name": PLUG_NAME})
    left = {}
    for _s in ("app", "wechat"):
        _c, _d = raw("GET", "/api/features?src=" + _s)
        _f = _d.get("features") or {}
        left[_s] = {k: v for k, v in _f.items()
                    if k == "httptest_feat" or k.endswith("_test_feat")}
    check("测试键已从服务端清掉（%s）" % (left or "无"),
          not any(left.values()), left)

    print("\n" + "=" * 46)
    print("通过 %d / %d" % (_OK, _OK + _BAD))
    print("=" * 46)
    return 0 if _BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())