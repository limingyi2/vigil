# -*- coding: utf-8 -*-
"""插件安装包的安全边界（拖 zip 进云端服务器 = 远程代码执行入口）。

## 为什么这类测试不能省

面板会接收一个 zip、在**云端服务器**上解压、然后 `exec` 里面的 .py。
一个精心构造的 zip 能做的事：写到 plugins/ 外面去（覆盖核心代码）、
写到配置里（改人设/改 token）、撑爆 2C2G 的内存和磁盘。
所以每个"拒绝"分支都得有测试 —— 这些分支一旦被改松，就是一次静默的失守。

## 判据设计

**只测拒绝，不测"应该成功"**。因为 `inspect_zip` 的职责就是判断形状，
它不该关心包内容好不好。安装流程的成功路径由 `_test_plugins.py` 那边保证。

每条判据都是「给定这样的 zip，必须被拒且理由能给人看」——
不写成"调用不抛异常"这种没信息量的断言。
"""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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


def mkzip(path, entries, mode="w"):
    """entries: {归档内名字: bytes或 str}"""
    with zipfile.ZipFile(path, mode) as z:
        for n, v in entries.items():
            if isinstance(v, str):
                v = v.encode("utf-8")
            z.writestr(n, v)
    return path


TMP = None


def z(name, entries):
    return mkzip(os.path.join(TMP, name), entries)


GOOD = 'NAME = "demo"\nDESC = "d"\nFEATURE = "demo_feat"\n'


def main():
    global TMP
    import plugin_ops as po
    TMP = tempfile.mkdtemp(prefix="pops_")

    group("0. 基线：一个规范包必须被接受（否则下面的拒绝毫无意义）")
    ok, names, err = po.inspect_zip(z("good1.zip", {"demo.py": GOOD}))
    check("单个 .py 的包被接受", ok, err)
    check("条目名正确", names == ["demo.py"], names)

    ok, names, err = po.inspect_zip(
        z("good3.zip", {"demo2.py": GOOD, "zxplugin.json": "{}"}))
    check("带 zxplugin.json 的单文件包被接受", ok, err)

    # 目录包由加载器支持；只允许入口与同目录前端资源，不允许嵌套 Python 模块。
    ok, names, err = po.inspect_zip(z("good2.zip", {
        "mypkg/__init__.py": GOOD,
        "mypkg/frontend.js": "window.VigilPlugin = {};\n",
        "mypkg/frontend.css": ".demo {}\n",
    }))
    check("接受：目录插件和前端资源", ok, err)
    check("  保留目录包内全部文件", len(names) == 3, names)

    ok, _, err = po.inspect_zip(z("t3.zip", {"pkg/helper.py": "X=1"}))
    check("拒绝：目录里连 __init__.py 都没有", not ok)
    ok, _, err = po.inspect_zip(z("t3b.zip", {
        "pkg/__init__.py": GOOD, "pkg/nested/helper.py": "X=1"}))
    check("拒绝：目录插件含嵌套子目录", not ok)
    ok, _, err = po.inspect_zip(z("t4.zip", {"a/b/c.py": GOOD}))
    check("拒绝：多了一层目录", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t5.zip", {"demo.js": "console.log(1)"}))
    check("拒绝：平铺 .js 不是插件入口", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t6.zip", {"demo.exe": "MZ"}))
    check("拒绝：.exe 不在白名单", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t7.zip", {}))
    check("拒绝：空包", not ok)
    ok, _, err = po.inspect_zip(z("t8.zip", {"data.json": "{}"}))
    check("拒绝：平铺但不是 .py", not ok, "通过了")

    group("1. 路径穿越 —— 最要紧的一类")
    for label, entries in (
        (".. 跳出plugins/", {"../evil.py": GOOD}),
        (".. 藏在中间", {"pkg/../../evil.py": GOOD}),
        ("反斜杠形式的 ..", {"..\\evil.py": GOOD}),
        ("绝对路径 /etc", {"/etc/cron.d/evil": "x"}),
        ("Windows 盘符", {"C:/Windows/evil.py": GOOD}),
        ("家目录展开", {"~/evil.py": GOOD}),
        ("深层 ..", {"a/b/c/../../../../evil.py": GOOD}),
    ):
        ok, names, err = po.inspect_zip(z("t1.zip", entries))
        check("拒绝：%s" % label, not ok, "竟然通过了：%s" % names)
        check("  理由能给人看（非空、非术语）",
              bool(err) and len(err) > 4, repr(err))

    group("2. NTFS 的 ADS（evil.py:hidden 这种）")
    ok, names, err = po.inspect_zip(z("t2.zip", {"evil.py:stream": GOOD}))
    check("拒绝：文件名带冒号", not ok, names)
    check("  理由提到了冒号", "冒号" in err, err)

    group("3. 形状不对的包")
    ok, _, err = po.inspect_zip(z("t3b.zip", {"demo.tar.gz": "x"}))
    check("拒绝：.tar.gz 不在白名单", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t3c.zip", {"a.py": GOOD, "b.py": GOOD}))
    check("拒绝：塞了两个 .py（第二个会被静默丢掉）", not ok, "通过了")
    check("  理由说清是文件太多", "2 个" in err or "多个" in err, err)

    group("4. 大小限制（防 zip 炸弹撑爆 2C2G）")
    # 构造一个高压缩比的大文件：1MB 的重复内容能压到很小
    big = "A" * (1024 * 1024)
    p = z("bomb.zip", {"bomb.py": big * 20})
    ok, _, err = po.inspect_zip(p)
    check("拒绝：解开后超上限（zip 炸弹）", not ok, "通过了")
    check("  理由提到了炸弹或上限",
          ("炸弹" in err) or ("上限" in err), err)

    p = z("toobig.zip", {"big.py": "B" * (po.MAX_FILE_BYTES + 1024)})
    ok, _, err = po.inspect_zip(p)
    check("拒绝：单文件超限", not ok, "通过了")

    p = mkzip(os.path.join(TMP, "many.zip"),
              {("f%d.py" % i): "X=1" for i in range(po.MAX_FILES + 5)})
    ok, _, err = po.inspect_zip(p)
    check("拒绝：条目数超限", not ok, "通过了")

    group("5. 不是 zip 的东西")
    p = os.path.join(TMP, "fake.zip")
    open(p, "wb").write(b"this is not a zip at all")
    ok, _, err = po.inspect_zip(p)
    check("拒绝：内容不是 zip", not ok)
    ok, _, err = po.inspect_zip(os.path.join(TMP, "nope.zip"))
    check("拒绝：文件不存在", not ok)

    group("6. 🔴 名字怪异的条目")
    # 点开头/下划线开头的 .py 会被现有加载器跳过（它只收非下划线 .py），
    # 装了也是白装 —— 所以这里拒绝是对的，不是漏网。
    ok, _, err = po.inspect_zip(z("t9.zip", {".hidden.py": GOOD}))
    check("拒绝：.开头的 .py（加载器不会收，装了白装）", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t9b.zip", {"_priv.py": GOOD}))
    check("拒绝：_开头的 .py（同上）", not ok, "通过了")
    ok, _, err = po.inspect_zip(z("t10.zip", {"CON.py": GOOD}))
    print("        CON.py（Windows 保留名）-> accepted=%s%s"
          % (ok, ("  " + err) if err else ""))
    check("  至少不是静默改名成别的形状", isinstance(ok, bool))

    group("7. 🔴 装坏包不能碰到已有的插件（这是最隐蔽的失败模式）")
    # 装之前先在目录里放一个"已有插件"
    keep = os.path.join(po._PLUG_DIR, "_po_keepme.py")
    open(keep, "w", encoding="utf-8").write(GOOD)
    try:
        for label, entries in (
            ("zip 炸弹", {"bomb.py": "A" * (1024 * 1024) * 20}),
            ("语法错", {"broken.py": "def f(:\n  pass"}),
            ("路径穿越", {"../escaped.py": GOOD}),
        ):
            ok, res = po.install(z("bad_%s.zip" % len(label), entries))
            check("装 %s 被拒" % label, not ok, res)
        check("已有插件还在（没被坏包波及）", os.path.isfile(keep))
        escaped = os.path.join(os.path.dirname(po._PLUG_DIR), "escaped.py")
        check("没有文件逃到 plugins/ 外面", not os.path.exists(escaped),
              escaped)
    finally:
        if os.path.isfile(keep):
            os.remove(keep)

    group("8. 语法检查真的在落盘前跑（坏包不该出现在 plugins/ 里）")
    ok, res = po.install(z("syntax.zip", {"syntaxbad.py": "def f(:\n"}))
    check("语法错的包装不进去", not ok, res)
    check("  理由里点了是哪个文件", "syntaxbad" in str(res.get("err")),
          res.get("err"))
    check("  plugins/ 里没留下残骸",
          not os.path.isfile(os.path.join(po._PLUG_DIR, "syntaxbad.py")))

    group("9. 正常安装 → 卸载闭环")
    ok, res = po.install(z("roundtrip.zip", {"rt.py": GOOD}))
    check("装上了", ok, res)
    tgt = os.path.join(po._PLUG_DIR, "rt.py")
    check("文件真的落在 plugins/ 下", os.path.isfile(tgt))
    meta = po.read_registry().get("plugins") or {}
    check("元数据记下了安装时间", bool((meta.get("demo") or meta.get("rt") or {})
                                       .get("installed_at")),
          list(meta.keys()))
    name = res.get("name") or ""
    ok2, res2 = po.uninstall(name)
    check("卸掉了", ok2, res2)
    check("文件真被删了", not os.path.isfile(tgt))
    check("元数据也清了", name not in (po.read_registry().get("plugins") or {}))
    ok3, res3 = po.uninstall(name)
    check("再卸一次会明确报错（不是假成功）", not ok3, res3)

    group("9b. 附属文件（zxplugin.json）跟着装、跟着卸")
    ok, res = po.install(z("withex.zip", {
        "wx.py": GOOD,
        "zxplugin.json": '{"name":"wx","author":"me","version":"1.2"}'}))
    check("带 zxplugin.json 装上了", ok, res)
    check("元信息读到了（作者/版本）",
          (res.get("manifest") or {}).get("author") == "me", res.get("manifest"))
    aux = [f for f in os.listdir(po._PLUG_DIR) if f.startswith("_wx_")]
    check("附属文件落盘了", bool(aux), aux)
    check("  且以 _ 开头（加载器不会把它当插件）",
          all(f.startswith("_") for f in aux), aux)
    po.uninstall("wx")
    left = [f for f in os.listdir(po._PLUG_DIR) if f.startswith("_wx_")]
    check("卸载时附属文件一起清了", not left, left)

    group("9c. 🔴🔴 插件本体按后缀认，不按 zip 里的顺序")
    # 这个坑很隐蔽：zip 条目顺序由打包工具决定，install 一度用 names[0]
    # 当插件本体。碰上"json 在前"的包，会把 zxplugin.json 装成 zxplugin.py ——
    # 语法检查照样通过（JSON 是合法的 Python 字面量），用户看到"安装成功"，
    # 实际装进去一个不加载的空壳。
    p = os.path.join(TMP, "jsonfirst.zip")
    mkzip(p, {"zxplugin.json": '{"name":"ord","author":"a"}',
              "ord.py": 'NAME = "ord"\nDESC = "d"\n'})
    ok, res = po.install(p)
    check("json 排在前面也能装上", ok, res)
    check("装出来的是 ord.py（不是 zxplugin.py）",
          res.get("package") == "ord", res)
    check("ord.py 真的在 plugins/ 下",
          os.path.isfile(os.path.join(po._PLUG_DIR, "ord.py")))
    check("没有装出 zxplugin.py",
          not os.path.isfile(os.path.join(po._PLUG_DIR, "zxplugin.py")))
    check("manifest 仍然读到了",
          (res.get("manifest") or {}).get("author") == "a", res.get("manifest"))
    po.uninstall("ord")
    for f in ("ord.py", "zxplugin.py"):
        q = os.path.join(po._PLUG_DIR, f)
        if os.path.isfile(q):
            os.remove(q)

    group("9d. 顺序反过来也一样（不能只修一边）")
    p = os.path.join(TMP, "pyfirst.zip")
    mkzip(p, {"ord2.py": 'NAME = "ord2"\nDESC = "d"\n',
              "zxplugin.json": '{"name":"ord2"}'})
    ok, res = po.install(p)
    check("py 在前面也正常", ok and res.get("package") == "ord2", res)
    po.uninstall("ord2")
    for f in ("ord2.py", "_ord2_zxplugin.json"):
        q = os.path.join(po._PLUG_DIR, f)
        if os.path.isfile(q):
            os.remove(q)

    group("10. 卸载不认识的名字不能瞎删")
    ok, res = po.uninstall("__init__")
    check("拒绝非法名", not ok, res)
    ok, res = po.uninstall("../secrets")
    check("拒绝路径穿越的名字", not ok, res)
    check("plugins/ 目录还在", os.path.isdir(po._PLUG_DIR))

    group("11. registry 读不出来时能兜住（它可丢弃）")
    reg = po.REGISTRY
    old = open(reg, "rb").read() if os.path.isfile(reg) else None
    try:
        open(reg, "w", encoding="utf-8").write("{ broken json")
        d = po.read_registry()
        check("坏 JSON -> 空 registry", d.get("plugins") == {}, d)
        import plugin_host
        check("坏 registry 时 overview 不崩",
              isinstance(po.overview(["app", "wechat"]), list))
    finally:
        if old is not None:
            open(reg, "wb").write(old)
        elif os.path.isfile(reg):
            os.remove(reg)

    group("12. 渠道策略：复用 features，不新造机制")
    import features
    feat = "po_test_feat"
    features.register(feat, True)
    try:
        po.set_channels(feat, ["app", "wechat"], {"app": True, "wechat": False})
        ch = po.channels_of(feat, ["app", "wechat"])
        check("app 开 / wechat 关", ch == {"app": True, "wechat": False}, ch)
        # 关键：再点一次"两个都开"必须真的都开（只写 true 会关不掉）
        po.set_channels(feat, ["app", "wechat"], {"app": True, "wechat": True})
        ch2 = po.channels_of(feat, ["app", "wechat"])
        check("两个都开（取消勾选能真的关回去）",
              ch2 == {"app": True, "wechat": True}, ch2)
        # 面板显示的必须等于运行时问的
        check("面板视图 == features.on 的实际值",
              ch2["wechat"] == features.on(feat, "wechat"))
    finally:
        try:
            features.DEFAULTS.pop(feat, None)
            #  还得把**写进配置文件的那份**擦掉。set_channels 是真的
            # set_features 落盘了，只 pop 内存里的 DEFAULTS 是不够的 ——
            # 于是 po_test_feat 永远留在 features_by_src 里，
            # 面板上多出一个谁也说不清干什么的开关。
            # （这条是被第 15 组的残留检查抓出来的，不是猜的。）
            import json as _json
            import paths as _paths
            with open(os.path.join(str(_paths.CONFIG_DIR), "config.json"),
                      encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            for _s in list(_cfg.get("features_by_src") or {}):
                (_cfg["features_by_src"][_s] or {}).pop(feat, None)
            with open(os.path.join(str(_paths.CONFIG_DIR), "config.json"),
                      "w", encoding="utf-8") as _f:
                _json.dump(_cfg, _f, ensure_ascii=False, indent=2)
            features.invalidate()
        except Exception as e:
            print("        （清 %s 失败：%s）" % (feat, e))

    group("13. 不声明 FEATURE 的插件：渠道不可选，但要说明原因")
    ch = po.channels_of("", ["app", "wechat"])
    check("空 FEATURE -> 两渠道都算开", ch == {"app": True, "wechat": True}, ch)
    ov = po.overview(["app", "wechat"])
    nofeat = [p for p in ov if not p["feature"]]
    for p in nofeat:
        check("  %s 标了 channelable=False（界面该置灰）" % p["name"],
              p["channelable"] is False)
    if nofeat:
        print("        （本机有%d 个不声明 FEATURE 的插件）" % len(nofeat))

    group("14. 🔴🔴 附属文件必须落在 plugins/ 下，绝不许落 CWD")
    # 曾经的 bug：拼附属文件落盘路径时写成 dst = "_" + os.path.basename(dst)，
    # 那是"给绝对路径加前缀"，得到一个相对路径 → 文件掉进大脑根目录（也就是
    # 云端 C:\vigil\），而且被 except: pass 静默吞掉，安装照样报成功。
    # 两个后果叠加：文件位置不对 + 没人知道出过错。所以这里既查落点，也查 CWD。
    cwd_before = set(os.listdir(HERE))
    p = os.path.join(TMP, "wx.zip")
    mkzip(p, {"wxplug.py": 'NAME = "wxplug"\nDESC = "d"\nFEATURE = "wx_plug_t"\n',
              "zxplugin.json": '{"name":"wxplug","author":"a"}',
              "notes.txt": "附属说明"})
    ok, res = po.install(p)
    check("带附属文件的包装得上", ok, res)
    files = res.get("files") or []
    # files 里是**落盘后**的名字（notes.txt -> _wxplug_notes.txt），
    # 比对时要按前缀找，别直接拿包内原名去in。
    names_out = [os.path.basename(f) for f in files]
    check("插件本体被记账",
          "wxplug.py" in names_out, names_out)
    check("附属文件被记账（落盘名带前缀）",
          "_wxplug_notes.txt" in names_out, names_out)
    check("装出来一共 3 个文件（本体+json+txt）",
          len(files) == 3, names_out)
    for f in files:
        check("  %s 在 plugins/ 下" % os.path.basename(f),
              os.path.dirname(os.path.abspath(f)) ==
              os.path.abspath(po._PLUG_DIR), f)
    leaked = [n for n in set(os.listdir(HERE)) - cwd_before
              if n.startswith("_") and "zxplugin" in n]
    check("CWD 没有多出 _*zxplugin.json", not leaked, leaked)
    check("plugins/ 下的附属文件真的存在",
          os.path.isfile(os.path.join(po._PLUG_DIR, "_wxplug_notes.txt")))
    po.uninstall("wxplug")

    # 清掉本轮测试装的东西
    for f in ("rt.py", "syntaxbad.py", "demo.py"):
        p = os.path.join(po._PLUG_DIR, f)
        if os.path.isfile(p):
            os.remove(p)
    try:
        reg2 = po.read_registry()
        reg2["plugins"] = {}
        po.write_registry(reg2)
    except Exception:
        pass

    group("15. 测试不许在配置里留下自己的键")
    # 这条是被真坑出来的：第 12 组往 features_by_src 写过 po_test_feat，
    # 跑完只清了plugins/ 目录，配置里的键留了下来。后来打开面板，
    # 多出一个谁也说不清干什么的开关，还以为是谁加的功能。
    #
    #  必须**直接读配置文件**，不能用 features.all_features()：
    # 它不传 src 时只返回基线那份，features_by_src 里的键一个都看不到 ——
    # 于是这条判据永远是 PASS，等于没写。（第一版就是这么写的，
    # 故意塞了个残留键进去它照样报"无"，当场露馅。）
    try:
        import paths
        import features
        features.invalidate()
        with open(os.path.join(str(paths.CONFIG_DIR), "config.json"),
                  encoding="utf-8") as f:
            cfg = json.load(f)
        seen = {}
        for k in (cfg.get("features") or {}):
            seen[k] = "基线"
        for s, d in (cfg.get("features_by_src") or {}).items():
            for k in (d or {}):
                seen[k] = s
        # 渠道专有键本来就带下划线分段（weather_alert / rand_img_chat），
        # 所以只认"以 _test_feat 结尾"和"以 httptest_ 开头"这两种形状
        leftovers = [("%s(%s)" % (k, v)) for k, v in seen.items()
                     if k.endswith("_test_feat") or k.startswith("httptest_")]
        check("配置里没有测试残留的键（%s）" % (leftovers or "无"), not leftovers,
              leftovers)
    except Exception as e:
        check("读配置核对测试残留", False, e)

    group("16. 🔴 卸载必须把渠道开关的键一起摘掉")
    # 不摘的话：插件没了，features_by_src.<src>.<FEATURE> 还在，
    # 而 features.on(key, src) 只看键不看插件装没装 —— 配置里挂着一个
    # 谁也关不掉的"功能"，功能开关页还会列出一个不存在的条目。
    # 每装卸一轮就多留一份，配置很快攒出一堆说不清是什么的键。
    feat2 = "wx_plug_t"                # 跟第 14 组那个插件同一个 FEATURE
    ok, res = po.install(os.path.join(TMP, "wx.zip"))
    check("重装上了（拿第 14 组那个包）", ok, res)
    import features as _fe
    _fe.invalidate()
    #  必须先 register 再 set_channels —— features.set_features 有
    # `k in DEFAULTS` 的白名单，而 DEFAULTS 里的插件键是**装载时**
    # （plugin_host 调 features.register）才加进来的，安装本身不加。
    # 跳过register 的话 set_channels 会被静默丢掉，然后这条判据会误报
    # "没写进配置"，看起来像卸载的锅。
    _fe.register(feat2, True)
    po.set_channels(feat2, ["app", "wechat"], {"app": True, "wechat": False})

    def _in_cfg(key):
        """直接读配置文件看这个键在不在（不能用 all_features，只看得到基线）。"""
        with open(os.path.join(str(paths.CONFIG_DIR), "config.json"),
                  encoding="utf-8") as f:
            c = json.load(f)
        if key in (c.get("features") or {}):
            return "基线"
        for s, d in (c.get("features_by_src") or {}).items():
            if key in (d or {}):
                return s
        return None

    where = _in_cfg(feat2)
    check("装完渠道开关写进配置了（%s）" % (where or "没写"), where is not None, where)
    #  两个渠道都要查。只查"在不在任意一个渠道里"的话，
    # 只写成功一个渠道也会 PASS —— 而"app 写了 wechat 没写"正是
    # 最典型的漏渠道现场（界面显示两个都开，微信那边其实没生效）。
    with open(os.path.join(str(paths.CONFIG_DIR), "config.json"),
              encoding="utf-8") as f:
        _c = json.load(f)
    _by = _c.get("features_by_src") or {}
    check("app 写了 true", (_by.get("app") or {}).get(feat2) is True,
          (_by.get("app") or {}).get(feat2))
    check("wechat 写了 false（取消勾选必须显式写 false）",
          (_by.get("wechat") or {}).get(feat2) is False,
          (_by.get("wechat") or {}).get(feat2))
    check("内存DEFAULTS 里也登记了",
          feat2 in _fe.all_features(), list(_fe.all_features())[:8])

    ok, res = po.uninstall("wxplug")
    check("卸载成功", ok, res)
    check("消息里说了清了渠道开关", "渠道开关" in (res.get("msg") or ""),
          res.get("msg"))
    _fe.invalidate()
    left = _in_cfg(feat2)
    check("🔴 配置里的键被摘干净了（还剩 %s）" % (left or "无"), left is None, left)
    check("🔴 内存 DEFAULTS 里也摘了", feat2 not in _fe.all_features(),
          list(_fe.all_features())[:8])

    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

    print("\n" + "=" * 46)
    print("通过 %d / %d" % (_OK, _OK + _BAD))
    print("=" * 46)
    return 0 if _BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
