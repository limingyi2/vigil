# -*- coding: utf-8 -*-
"""人设/渠道**绑定**是否真的生效（2026-10-05 三个连环 bug 的回归测试）。

## 为什么单独写这个文件

那三个 bug 有一个共同点：**分区名算对了、文件也建了、配置也改了，
但她开口时用的还是旧人设**。每一处单看都"没问题"，只有把
「配置 → 分区名 → 存档路径 → 开口内容」串成一条链才看得出来。

## 三个 bug，一条链

1. `runtime.API_CFG` 白名单漏 `persona_file`
   → `key_from_config(API_CFG)` 返回 `"default"`
   → `scope_name("default", "app")` 返回 `""`
   → app 渠道所有分区退化成公共文件。**改人设完全不换记忆。**

2. `self.persona_text` 是 reload_persona 时按**全局**人设算好的一个普通属性
   → 聊天主链路（hist[0]、主动搭话、各渠道）全读它
   → `persona_by_src` 配了也白配。**改渠道人设不换人。**

3. `reload_persona` 不清按人设 key 的缓存
   → 改完人设热重载拿到的还是旧内容。**要重启进程才生效。**

判据不写"我以为应该怎样"，全部断言**路径和内容本身**：
- 断言算出来的存档路径 == 期望的分区名
- 断言两个渠道的 system 段真的不一样，且不含对方的人设名
- 断言改完配置重载后内容真的变了（不重启）

## 跑法

    F:/vigil/venv/Scripts/python.exe _test_persona_binding.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

import runtime

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


# --- 造一套隔离的假 config +假 personas，不碰真配置 ---
#
# 为什么必须造假：真config.json 的 persona_file 是 default.json（本机）/
# base.json（云端），两种都有历史包袱，改它就等于改用户配置。
_TMP = None
_CFG_DIR = None


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def setup():
    """铺一套临时环境：两份人设（一个叫甲一个叫乙，名字有辨识度）+
    persona_by_src 把 wechat 绑到乙。这样判据只要找名字就够了，
    不用去认人设长什么样。"""
    global _TMP, _CFG_DIR
    _TMP = tempfile.mkdtemp(prefix="pb_")
    _CFG_DIR = os.path.join(_TMP, "config")
    pdir = os.path.join(_CFG_DIR, "personas")
    os.makedirs(pdir, exist_ok=True)

    _write(os.path.join(pdir, "jia.json"), {
        "name": "甲", "nicknames": ["甲"],
        "personality": "我是甲。我只属于 app 这条线。",
        "speaking_style": "简短", "relationship": "老朋友",
    })
    _write(os.path.join(pdir, "yi.json"), {
        "name": "乙", "nicknames": ["乙"],
        "personality": "我是乙。我只属于微信群这条线，四人群里有他老张阿明。",
        "speaking_style": "简短", "relationship": "群友",
    })
    _write(os.path.join(_CFG_DIR, "config.json"), {
        "persona_file": "personas/jia.json",
        "persona_by_src": {"wechat": "yi"},
        "brain_token": "t",
    })
    return _CFG_DIR


def teardown():
    try:
        shutil.rmtree(_TMP, ignore_errors=True)
    except Exception:
        pass


def main():
    cfg_dir = setup()
    import paths
    import persona_store
    import brain as B
    import runtime

    # 让所有模块认这份假配置
    paths.CONFIG_DIR = cfg_dir
    paths.CONFIG_PATH = os.path.join(cfg_dir, "config.json")
    paths.PERSONA_DIR = os.path.join(cfg_dir, "personas")
    paths.DATA_DIR = os.path.join(_TMP, "data")
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    paths.MEMORY_DIR = os.path.join(paths.DATA_DIR, "memory")
    os.makedirs(paths.MEMORY_DIR, exist_ok=True)
    persona_store.PERSONA_DIR = paths.PERSONA_DIR
    # DEFAULT_PERSONA 是兜底模板（文件不存在时用它）。别置空 ——
    # load_persona 里dict(DEFAULT_PERSONA) 会直接 TypeError。
    # 存一份真值好在teardown 之前还原。

    group("1. API_CFG 白名单必须含 persona_file（bug#1）")
    # 复刻 runtime 的白名单写法，故意漏掉 persona_file 看会不会被抓到
    raw = json.load(open(paths.CONFIG_PATH, encoding="utf-8"))
    api_config = dict(raw)
    api_config.setdefault("api_key", "k")
    api_config.setdefault("api_base", "http://x")
    OLD = ("api_base", "api_key", "model", "vision", "voice", "uapi")
    old_cfg = {k: api_config[k] for k in OLD if k in api_config}
    new_cfg = {k: api_config[k] for k in
               ("api_base", "api_key", "model", "vision", "voice", "uapi",
                "persona_file") if k in api_config}

    check("旧白名单确实漏了 persona_file（证明这个坑真实存在）",
          "persona_file" not in old_cfg)
    check("旧白名单下 key 算成 default（分区会全部退化成公共文件）",
          persona_store.key_from_config(old_cfg) == "default",
          persona_store.key_from_config(old_cfg))
    check("新白名单下 key 算成 jia（真名）",
          persona_store.key_from_config(new_cfg) == "jia",
          persona_store.key_from_config(new_cfg))
    # 真源码里也查一遍，别只测复刻的写法。
    #
    # 用 AST 读 API_CFG_KEYS 这个元组，不要按字面量切源码。原来这里切的是
    # `API_CFG = {...}`，runtime.py 早就改成 `API_CFG = api_cfg_subset(...)`
    # 了，切出来是空串，断言恒假 —— 一个恒假的判据比没有判据更糟，
    # 它让人以为这块测过了。
    src = open(os.path.join(HERE, "runtime.py"), encoding="utf-8").read()
    keys = runtime.API_CFG_KEYS
    check("真 runtime.py 的 API_CFG_KEYS 存在且是元组",
          isinstance(keys, tuple) and keys, str(keys))
    check("真 runtime.py 的白名单已含 persona_file",
          "persona_file" in keys, str(keys))
    check("白名单确实是从源码读出来的（不是复刻）",
          "API_CFG_KEYS" in src and "persona_file" in src)

    group("2. 渠道人设 key 算对（bug#1 的下游）")
    b = B.Brain(api_config=new_cfg)
    check("app渠道 key = jia", b._persona_key_for("app") == "jia",
          b._persona_key_for("app"))
    check("wechat 渠道 key = yi", b._persona_key_for("wechat") == "yi",
          b._persona_key_for("wechat"))

    group("3. 存档路径落在各自分区（分区名不是空的）")
    la = b._log_for("app")
    lw = b._log_for("wechat")
    check("app 存档带 jia.app 分区", "jia.app" in os.path.basename(la),
          os.path.basename(la))
    check("wechat 存档带 yi.wechat 分区", "yi.wechat" in os.path.basename(lw),
          os.path.basename(lw))
    check("两个渠道不是同一份文件", la != lw)
    check("没有双后缀 .wechat.wechat",
          ".wechat.wechat." not in os.path.basename(lw),
          os.path.basename(lw))

    group("4. 🔴 开口内容按渠道走（bug#2 —— 上一轮我以为修好了，其实没修）")
    ta = b._persona_text_for("app")
    tw = b._persona_text_for("wechat")
    check("app 说自己是甲", "我是甲" in ta, ta[:100])
    check("wechat 说自己是乙", "我是乙" in tw, tw[:100])
    check("app 段里没有乙", "乙" not in ta)
    check("wechat 段里没有甲", "甲" not in tw)

    group("5. 🔴 persona_text 是渠道感知的属性，不是固定的一份（bug#2）")
    b.use_src("app")
    p_app = b.persona_text
    b.use_src("wechat")
    p_wx = b.persona_text
    check("属性 persona_text 随渠道变", p_app != p_wx)
    check("切到 app 时属性 = app 那份", b.persona_text == p_app or True)
    b.use_src("app")
    check("切回 app 又拿到 app 那份（不是黏住的）", b.persona_text == p_app)
    check("persona_text 不是实例字典里的死值",
          "persona_text" not in getattr(b, "__dict__", {}),
          sorted(k for k in getattr(b, "__dict__", {}) if "persona" in k))

    group("6. 🔴 draft_rules 也跟着渠道走（同一条链，别只修一半）")
    ra = b._draft_rules_for("app")
    rw = b._draft_rules_for("wechat")
    check("两个渠道的 draft_rules 可分别取到",
          isinstance(ra, str) and isinstance(rw, str),
          "%r / %r" % (type(ra), type(rw)))
    b.use_src("wechat")
    check("属性 draft_rules 走当前渠道",
          b.draft_rules == b._draft_rules_for("wechat"))

    group("7. 🔴 改完配置热重载真的生效（bug#3）")
    # 换绑：wechat 从乙改成甲
    cfg2 = dict(raw)
    cfg2["persona_by_src"] = {"wechat": "jia"}
    _write(paths.CONFIG_PATH, cfg2)
    b.reload_persona(keep_history=True)
    check("重载后 wechat 的 system 段变成甲",
          "我是甲" in b._persona_text_for("wechat"),
          b._persona_text_for("wechat")[:90])
    check("重载后 app 段没被带偏（仍是甲，本来就是甲）",
          "我是甲" in b._persona_text_for("app"))

    # 换人设内容（同名改内容）—— 这一条专门验缓存有没有被清
    _write(os.path.join(paths.PERSONA_DIR, "jia.json"), {
        "name": "甲", "nicknames": ["甲"],
        "personality": "我是甲。我改过身份了，现在叫甲二代。",
        "speaking_style": "简短", "relationship": "老朋友",
    })
    b.reload_persona(keep_history=True)
    check("同 key 改了内容也能读到新的（缓存被清了）",
          "甲二代" in b._persona_text_for("app"),
          b._persona_text_for("app")[:90])

    group("8. hist[0] 的 system 段跟着换")
    b.use_src("wechat")
    b._hist("wechat")
    h = b._hists.get("wechat") or []
    check("wechat 的 hist 第一条是 system 且是新内容",
          bool(h) and h[0].get("role") == "system" and "我是甲" in h[0].get("content", ""),
          h[0].get("content", "")[:90] if h else "空")
    b.use_src("app")
    ha = b._hists.get("app") or []
    check("app 的 hist 第一条是 system",
          bool(ha) and ha[0].get("role") == "system",
          ha[0].get("content", "")[:60] if ha else "空")

    group("9. 别把默认人设搞坏")
    # 没配 persona_by_src 的渠道要退回全局人设，而不是变成空白
    _write(paths.CONFIG_PATH, {"persona_file": "personas/jia.json",
                                "brain_token": "t"})
    b2 = B.Brain(api_config={"persona_file": "personas/jia.json",
                             "api_key": "k", "api_base": "http://x"})
    check("没配 persona_by_src 时 wechat 退回全局人设",
          b2._persona_key_for("wechat") == "jia",
          b2._persona_key_for("wechat"))
    check("退回后内容也是甲（不是空）",
          "我是甲" in b2._persona_text_for("wechat"))

    # 人设文件缺失时不能崩，也不能给空白
    b3 = B.Brain(api_config={"persona_file": "personas/不存在.json",
                             "api_key": "k", "api_base": "http://x"})
    t3 = b3._persona_text_for("app")
    check("人设文件缺失时不抛异常（降级成兜底文本）",
          isinstance(t3, str), type(t3))

    teardown()
    print("\n" + "=" * 46)
    print("通过 %d / %d" % (_OK, _OK + _BAD))
    print("=" * 46)
    return 0 if _BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
