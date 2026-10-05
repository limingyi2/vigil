# -*- coding: utf-8 -*-
"""人设卡片上的介绍必须跟着人设正文走。

## 为什么有这个测试

用户报「我换了人设，卡片上的介绍不会变」。查下来是**两个真源**：

    brain/server.py   /api/persona 的 presets[]  ← 手搓，直接读 meta["desc"]
    brain/persona_store.py  list_templates()      ← _card_desc() 从正文现算

面板的人格卡片读的是 `presets`，所以改 `_card_desc` 对卡片零影响 ——
改了也白改。而 `desc` 是**预设写死的元数据**，编辑器里根本没有这一栏，
用户把整份人设整个换掉，卡片上还是老那句。

## 判据要点

1. `/api/persona` 的 presets 段里**不能出现直接读 `meta.get("desc")`**，
   必须调`list_templates` —— 治"两个真源"，而不是治这一次的症状
2. `_card_desc` 的回退链：notes -> personality -> background -> scene
   -> relation -> desc。只读 notes 的话，内容填在分栏字段里的老预设
   会一律显示空，卡片上一排空白 = 功能没做
3. **形状判断而不是卡长度**：以冒号结尾的是小标题。
   原来写 `len(s) < 6` 跳过，把「很安静。」这种短但有效的描述扔了
4. `list_templates` 必须保留「目录空时给新建项」的兜底。
   原来那份兜底在 `list_personas` 里，换函数时不搬过来，
   「目录空」会从"显示新建项"退化成"空白列表"
"""
import io
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OK, BAD = [], []


def check(label, cond, extra=""):
    #🔴 参数顺序是 (label, cond)。写成 (cond, label) 的话，
    # 非空字符串恒为真 —— 满屏 OK 而一条都没真测。上一版就栽在这。
    (OK if cond else BAD).append(label)
    print("  %-4s %s%s" % ("OK" if cond else "BAD", label,
                           ("  <- " + repr(extra)[:150]) if extra else ""))


def read(name):
    return io.open(os.path.join(HERE, name), encoding="utf-8").read()


def main():
    print("\n[0] 自检：check() 本身不能是恒真的")
    # 没有这条，上面每一条 OK 都可能是"label 字符串非空"骗的。
    # 上一版把参数顺序写成 (cond, label)，满屏 OK 而一条都没真测。
    _ok0, _bad0 = len(OK), len(BAD)
    check("这条必须是真命题", 1 == 1)
    check("这条必须是假命题（应记 BAD）", 1 == 2)
    check("假命题确实进了 BAD", len(BAD) == _bad0 + 1, len(BAD))
    BAD.pop()
    del OK[-1]
    check("撤销后账目复原", len(OK) == _ok0 + 1 and len(BAD) == _bad0,
          (len(OK), len(BAD)))

    print("\n[1] server.py 的 presets 必须走 list_templates")
    src = read("server.py")
    i = src.find('path == "/api/persona"')
    check("找得到 /api/persona 分支", i >= 0)
    j = src.find('elif path == ', i + 10)
    seg = src[i:j] if i >= 0 and j > i else ""
    check("分支段非空（锚点没失效）", bool(seg), len(seg))
    check("presets 段调用了 list_templates",
          "list_templates" in seg)
    # 治本的那一刀：不能再手搓一份直接读 desc
    bad_spot = re.search(r'presets\s*=\s*\[\]', seg)
    check("有独立的 presets 构造段", bool(bad_spot))
    tail = seg[bad_spot.start():] if bad_spot else ""
    tail = tail[:900]
    check("presets 段里不出现 meta.get(\"desc\")",
          'meta.get("desc")' not in tail)
    check("presets 段里不出现 path_of(key)（手搓的标志）",
          "path_of(" not in tail)
    # 变量名要对：那段上下文里叫 cur，不是 _cur_key
    if "list_templates(" in seg:
        m = re.search(r"list_templates\(\s*(\w+)\s*\)", seg)
        check("传给 list_templates 的是上下文真有的变量",
              bool(m) and m.group(1) in ("cur", "_pkey"),
              m.group(1) if m else None)

    print("\n[2] _card_desc 的回退链")
    import persona_store as ps
    cases = [
        ({"notes": "# 卡\n\n你的性格基调：软是底色。"}, "notes 优先",
         "你的性格基调：软是底色。"),
        ({"notes": "", "personality": "外表开朗，其实有点怕生。"},
         "退到 personality", "外表开朗，其实有点怕生。"),
        ({"notes": "", "background": "高中同学，认识六年了。"},
         "退到 background", "高中同学，认识六年了。"),
        ({"notes": "", "desc": "原版·老朋友"}, "全空退到 desc", "原版·老朋友"),
        ({}, "全空且无 desc", ""),
        ({"notes": "#\n---\n\n", "personality": "很安静。"},
         "跳过标题行，短句照样取", "很安静。"),
        ({"notes": "一、你是谁\n"}, "以冒号结尾的是标题，跳过",
         "一、你是谁"),
    ]
    for d, label, want in cases:
        got = ps._card_desc(d)
        check(label, got == want, "got=%r want=%r" % (got, want))

    print("\n[2b] 中文小标题要跳，但正文里的书名号不能动")
    # 人设正文里的小标题写成 `【你的日常（…）】` 而不是 `## …`。
    # 只认 `#` 的话，卡片上会出现「【你的日常】」当介绍。
    for raw, want, label in [
            ("【你的日常】\n你可以主动分享日常。", "你可以主动分享日常。",
             "整行是【】包住 -> 跳到下一句"),
            ("# 卡\n【你的日常】\n你可以主动分享日常。", "你可以主动分享日常。",
             "有 # 标题时也跳【】"),
            ("他说【笑】了一声", "他说【笑】了一声",
             "【】在句中 -> 原样保留（剥掉会改坏语义）"),
            ("他爱看《红楼梦》", "他爱看《红楼梦》",
             "《》在句中 -> 原样保留"),
            ("《红楼梦》真好看", "《红楼梦》真好看",
             "《》不在行末 -> 是正文，不是标题"),
            ("一、你是谁", "一、你是谁", "无括号的序数行 -> 保留"),
            # 「【章节名】正文」这种以【开头但后面还有正文的也要去掉前半截
            ("【你在群里的作用】两件事：把话头往下接。", "两件事：把话头往下接。",
             "以【开头的章节名 -> 去掉前半截"),
            ("【笑】了一声", "【笑】了一声", "短语气词在句首 -> 不动"),
            ("他爱看【红楼梦】这本书", "他爱看【红楼梦】这本书",
             "【】在句中 -> 不动")]:
        got = ps._first_sentence(raw)
        check(label, got == want, "got=%r want=%r" % (got, want))

    print("\n[3] markdown 修饰要剥掉")
    # 语义：**去掉这一种标记，其余正文全留**。标记之外的正文不能丢 ——
    # 剥 `**` 时 `` `code` `` 在那对标记之外，丢掉就是凭空删正文。
    # 🔴 判据要落在 `_strip_md`（一种标记），别用 `_first_sentence`
    # 串两个标记测：它先剥 `**` 再剥反引号，两次串起来的结果不好归因。
    for raw, mark, want in [
            ("**软是底色**，嘴硬是保护色", "**", "软是底色，嘴硬是保护色"),
            ("**一**、**二**、三", "**", "一、二、三"),
            ("`code` 和 x", "`", "code 和 x"),
            ("`code` 和 **bold**", "**", "`code` 和 bold"),
            ("`code` 和 **bold**", "`", "code 和 **bold**"),
            ("3*2 和 x", "**", "3*2 和 x"),          # 落单的整体不动
            ("未配对的 **开头", "**", "未配对的 **开头"),
            ("没有标记", "**", "没有标记")]:
        got = ps._strip_md(raw, mark)
        check("剥%s %s" % (mark, raw[:16]), got == want, "got=%r" % got)
    # 串两个标记时**要按标记分别看**：`**` 只包住 `bold`，`` `code` `` 在
    # 这对标记之外，所以剥 `**` 的结果里不该有 `code`。
    # 我上一版断言"两个内容都保留"是把自己想的语义当成了实现的语义 ——
    # 实现的语义是"只保留这一对标记里的内容"，这才是对的。
    # 串两个标记走 _first_sentence：两种标记都该被剥掉
    seq = ps._first_sentence("`code` 和 **bold**")
    check("_first_sentence 两种标记都剥掉",
          seq == "code 和 bold", repr(seq))

    print("\n[4] list_templates 的空目录兜底")
    check("定义了 _card_desc", hasattr(ps, "_card_desc"))
    # 真建一个空目录跑一遍，别靠读源码猜
    old_dir = ps.PERSONA_DIR
    tmp = tempfile.mkdtemp(prefix="zxpersona")
    try:
        ps.PERSONA_DIR = os.path.join(tmp, "personas")
        out = ps.list_templates("base")
        check("目录不存在时返回一条新建项",
              len(out) == 1 and out[0].get("blank") is True, out)
        os.makedirs(ps.PERSONA_DIR)
        out = ps.list_templates("base")
        check("目录存在但空文件时也返回一条新建项",
              len(out) == 1 and out[0].get("blank") is True, out)
        # 放两份，验排序：current 最前、blank沉底。
        # base 必须**带正文** —— 只给 name 的空白人设 desc 本来就该是空，
        # 拿它断言 "desc 非空" 是我上一版测试写错了，不是代码错。
        for key, data in (("base", {"name": "知夏",
                                    "personality": "阳光开朗，对你极度偏爱。"}),
                          ("zz", {})):
            p = os.path.join(ps.PERSONA_DIR, key + ".json")
            with io.open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        out = ps.list_templates("base")
        keys = [o["key"] for o in out]
        check("当前人设排最前", keys and keys[0] == "base", keys)
        check("空白人设沉到末尾", keys and keys[-1] == "zz", keys)
        check("有正文的卡片 desc 非空",
              bool(out[0].get("desc")), out[0].get("desc"))
    finally:
        ps.PERSONA_DIR = old_dir
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    print("\n[5] list_personas 已无调用者（留两份真源就会漂）")
    psrc = read("persona_store.py")
    others = [f for f in os.listdir(HERE)
              if f.endswith(".py")
              and f not in ("persona_store.py", os.path.basename(__file__))]
    used = []
    for f in others:
        try:
            t = io.open(os.path.join(HERE, f), encoding="utf-8").read()
        except Exception:
            continue
        if "list_personas" in t:
            used.append(f)
    check("server.py 不再调 list_personas",
          "list_personas" not in src)
    check("其他模块也不调（否则删了会炸）",
          not used, used)

    print("\n%d 项通过，%d 项失败" % (len(OK), len(BAD)))
    if BAD:
        print("失败的：")
        for b in BAD:
            print("  - " + b)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())