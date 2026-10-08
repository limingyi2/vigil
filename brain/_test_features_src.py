# -*- coding: utf-8 -*-
"""功能开关：写入的渠道规则必须和读取的渠道规则**同一套**。

## 症状

面板上「功能开关」页，把顶栏切到 App，点任意一个开关 —— 提示"已关闭"，
刷新一下又开着。**App 渠道的功能一个都改不动**，微信渠道却正常。
同一页的「说话多少」滑杆倒是好使，所以一开始没人怀疑是开关系统本身。

## 根因

读写两侧对 `app` 的处理不一致：

    读：all_features(src)
          if src and src != "app":          # ← 单独跳过 app，注释写着"app 是基线"
              d.update(_cache["by"].get(src))

    写：set_features(patch, src)
          if src:                           # ← src="app" 也是真，照写覆盖
              by[src] = {...}               #   落进 features_by_src.app

面板在 App 渠道下会把 `src="app"` 放进请求（`withSrc` 对 app 不拼 query，
src 是 body 里另加的），服务端 `_norm_src("app")` 返回 `"app"`（不是空，
空值也会被它归成 "app"），于是这一路直通写入侧：

    POST /api/features {"tools": false, "src": "app"}
      → set_features(patch, "app") → features_by_src.app = {"tools": false}
      → all_features("app") 那时跳过 by["app"] → 读到 True
      → 点了跟没点一样

副作用比"没生效"更麻烦：配置里攒下了一堆**读不出来的覆盖**
（云端真有 `features_by_src.app = {calc,gate,meme,react: false}`）——
界面上显示"已关"，运行时全是开的，而且谁也不知道它们从哪来。

## 修法：app 就是一份普通覆盖

不搞"app 特例"。跟 `chatty`（说话多少）那套"**每个渠道各存一份覆盖，
全局那份是没人覆盖时的初值**"统一：`all_features(src)` 一律参与合并，
`set_features(patch, src)` 一律按渠道写。

连带好处：服务端 `_norm_src("")` 本来就把空 src 归成 `"app"`，所以
**App 客户端不带 src 的读写天然落在 app 渠道上**，前后端都不用改；
而且以前那些"点了没用"的历史覆盖会**即刻生效**，用户不用再点一遍。

## 判据怎么钉

① 写进去必须读得出来（根因判据 —— 读不出来的写入就是坏的）
② 每个渠道各写各的，互不影响；没写过覆盖的渠道跟全局走
③ 覆盖优先于全局：改全局不得掀掉某渠道已有的显式值
④ 渠道名大小写/空白归一；**空 src 才是"改全局初值"**
⑤ 历史覆盖确实生效（不是"攒着不读"）
⑥ 只写差异：写一个键不许把整份抄过来
⑦ `drop()` 要能把全局和所有渠道的键一起清掉
⑧ 没登记进 DEFAULTS 的键不许写进配置（写了就是谁也关不掉的幽灵键）

用法：F:/Vigil/venv/Scripts/python.exe _test_features_src.py
"""
import io
import json
import os
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
        print("  FAIL  " + name + "   -> "
              + ("<没传 extra>" if extra is None else repr(extra)))


import features as F                                     # noqa: E402

# 🔴 整个测试文件都跑在临时配置上，绝不碰 C:\Vigil 或本地真配置。
TMP = os.path.join(tempfile.gettempdir(), "_feat_src_test.json")


def fresh(cfg=None):
    """重置成一个干净配置，并把 features 指向它。"""
    d = cfg if cfg is not None else {"features": {}, "features_by_src": {}}
    with io.open(TMP, "w", encoding="utf-8", newline="\n") as fp:
        json.dump(d, fp, ensure_ascii=False)
    F._CFG = TMP
    F.invalidate()


def disk():
    return json.load(io.open(TMP, encoding="utf-8"))


def on(src, key):
    """跟运行时 features.on 对齐：**不认识的键一律当开**。

    这里少一个 `True` 就会把"没有这个键"读成 None，于是"没声明过的键当开"
    这条判据会假失败 —— 判据工具自己写错比没有判据更坏（会把人引去改产品代码）。
    """
    return F.all_features(src).get(key, True)


def cover(src, key=None):
    """盘上这个渠道的覆盖表。"""
    d = (disk().get("features_by_src") or {}).get(src)
    return d if key is None else (d or {}).get(key)


print("=" * 68)
print("[0] 判据工具自检 —— check(False) 必须记成失败，不能满屏 OK 却没测")
_bp, _bf = PASS[0], len(FAIL)
check("自检：抛一个假判据", False)
_recorded = len(FAIL) == _bf + 1        # 它有没有真被记进 FAIL
FAIL.pop()                              # 收回这条假的
PASS[0] = _bp
check("自检：假判据确实被记成失败", _recorded)
print()

print("[1] 起点：谁都没写过覆盖时，各渠道读到的就是全局那份")
fresh()
check("初始 tools 为 True（DEFAULTS）", on("app", "tools") is True)
check("初始 weather_alert 为 True", on("app", "weather_alert") is True)
check("初始两个渠道一致", on("app", "tools") is True and on("wechat", "tools") is True)
check("初始 features_by_src 是空的", disk().get("features_by_src") == {})
print()

print("[2] 根因判据：App 渠道改开关 —— 写进 features_by_src.app，且立刻读得出来")
fresh()
out = F.set_features({"tools": False}, "app")
check("1. 返回的就是改后的值（tools=False）", out.get("tools") is False, out.get("tools"))
D = disk()
check("2. 落到 features_by_src.app（每渠道一份，只写差异）",
      cover("app") == {"tools": False}, D.get("features_by_src"))
check("3. 全局 features 段没被动（那是别的渠道的初值）",
      not (D.get("features") or {}), D.get("features"))
check("4. all_features('app') 立刻读到 False —— 改了就是生效了",
      on("app", "tools") is False, on("app", "tools"))
check("5. 微信不受影响（没写过覆盖，仍跟全局 True)",
      on("wechat", "tools") is True, on("wechat", "tools"))
check("6. 没动的键不受影响（weather 仍 True）", on("app", "weather") is True)
print()

print("[3] 再点回来（开着）也要生效 —— 只挡一个方向的修法是假修")
F.set_features({"tools": True}, "app")
check("tools 回到 True", on("app", "tools") is True)
check("盘上 by_src.app.tools 也是 True", cover("app", "tools") is True, cover("app"))
check("只写了 tools 一个键（没把整份抄过来）", cover("app") == {"tools": True}, cover("app"))
print()

print("[4] 对照：微信渠道照旧走自己的覆盖，且不许动全局")
fresh()
F.set_features({"tools": False}, "wechat")
D = disk()
check("1. 落到 features_by_src.wechat", cover("wechat") == {"tools": False}, D.get("features_by_src"))
check("2. 全局 features 没被写", not (D.get("features") or {}), D.get("features"))
check("3. 微信读到 False（写得出就引得响）", on("wechat", "tools") is False, on("wechat", "tools"))
check("4. App 仍是 True（各写各的）", on("app", "tools") is True, on("app", "tools"))
print()

print("[5] 覆盖优先于全局：改全局初值不得掀掉某个渠道的显式值")
fresh()
F.set_features({"tools": False}, "wechat")             # 微信显式关
F.set_features({"tools": True})                        # 不带 src = 改全局初值
D = disk()
check("1. 全局 features.tools 变 True", (D.get("features") or {}).get("tools") is True,
      D.get("features"))
check("2. 微信仍是 False（覆盖压过全局）", on("wechat", "tools") is False, on("wechat", "tools"))
check("3. App 跟全局，是 True", on("app", "tools") is True)
F.set_features({"tools": False}, "wechat")             # 反向再来一次
F.set_features({"tools": False})
check("4. 反向：全局 False 后微信仍 False", on("wechat", "tools") is False)
check("5. 反向：App 跟全局 False", on("app", "tools") is False)
check("6. 微信那份只有 tools 一个键", cover("wechat") == {"tools": False}, cover("wechat"))
print()

print("[6] 没写过的键跟全局走（覆盖的语义是只写差异）")
fresh()
F.set_features({"weather": False}, "wechat")
check("微信 weather=False（自己有覆盖）", on("wechat", "weather") is False)
check("微信 tools 仍跟全局=True", on("wechat", "tools") is True)
check("App 完全不受影响", on("app", "weather") is True)
print()

print("[7] 渠道名归一：app 的几种拼法等价；只有空 src 才是改全局初值")
for shape, label, kind in [("app", "'app'", "cover"), ("APP", "'APP'", "cover"),
                           (" app ", "' app '", "cover"), ("App", "'App'", "cover"),
                           (None, "None", "global"), ("", "''", "global")]:
    fresh()
    F.set_features({"tools": False}, shape)
    D = disk()
    if kind == "cover":
        ok = cover("app") == {"tools": False} and not (D.get("features") or {})
        what = "写 app 覆盖"
    else:
        ok = (D.get("features") or {}).get("tools") is False and not D.get("features_by_src")
        what = "写全局初值"
    ok = ok and on("app", "tools") is False            # 两种写法读出来都必须是 False
    check("%-7s -> %-10s，且读得到" % (label, what), ok,
          {"features": D.get("features"), "by_src": D.get("features_by_src"),
           "read": on("app", "tools")})
print()

print("[8] 历史覆盖：以前点了没用、一直躺着不生效的那些值，现在真的生效了")
# 云端现状就是这形状：features_by_src.app 里躺着 4 个键，是当初在面板上
# 点出来的（那时读侧跳过 app，所以一直没生效）。统一规则后即刻兑现。
fresh({"features": {},
       "features_by_src": {"app": {"tools": False, "meme": False}}})
check("1. tools 读到 False（覆盖生效，不用再点一遍）",
      on("app", "tools") is False, on("app", "tools"))
check("2. 微信不受影响（仍跟全局 True）", on("wechat", "tools") is True)
check("3. 没登记进 DEFAULTS 的键会被丢掉（meme 当前没装插件）",
      on("app", "meme") is True, on("app", "meme"))
check("4. 盘上的值原样保留（读取不改写配置）",
      cover("app") == {"tools": False, "meme": False}, cover("app"))
print()

print("[9] drop() 清掉全局 + 所有渠道的键")
fresh()
F.set_features({"tools": False}, "app")
F.set_features({"tools": False, "weather": False}, "wechat")
n = F.drop("tools")
D = disk()
check("1. 返回值数得出清了几处（>=1）", n >= 1, n)
check("2. app 覆盖里的 tools 没了", "tools" not in (cover("app") or {}), cover("app"))
check("3. 微信覆盖里的 tools 没了", "tools" not in (cover("wechat") or {}), cover("wechat"))
check("4. 微信里别的键还在（weather=False）", cover("wechat", "weather") is False)
check("5. 清完读出来回到 DEFAULTS（tools=True）", on("app", "tools") is True)
F.DEFAULTS["tools"] = True              # drop 会把它从 DEFAULTS 摘掉，跑完复原
F.invalidate()

# 只有全局那一份时也要数得到。少报会让人以为"没清干净"，
# 于是去配置里找一个其实已经没了的键。
fresh()
F.set_features({"tools": False})                       # 只写全局初值
_n6 = F.drop("tools")
# 两处：DEFAULTS 里的登记 + 配置全局段里的键
check("6. 只在全局时，返回值得数出 2 处（DEFAULTS + 全局）", _n6 == 2, _n6)
F.DEFAULTS["tools"] = True
F.invalidate()
print()

print("[10] 空 patch / 无效键不许写坏配置")
fresh()
F.set_features({}, "app")
check("1. 空 patch：配置没多出东西",
      disk().get("features") == {} and not disk().get("features_by_src"))
F.set_features({"不存在的键": True, "tools": False}, "app")
D = disk()
check("2. 不在 DEFAULTS 里的键被丢掉，合法的照收", cover("app") == {"tools": False}, cover("app"))
check("3. 未声明的键读出来仍是当开", on("app", "不存在的键") is True)
print()

print("=" * 68)
print("通过 %d 项" % PASS[0])
if FAIL:
    print("失败 %d 项：" % len(FAIL))
    for f in FAIL:
        print("   - " + f)
    sys.exit(1)
print("全部通过")
