# -*- coding: utf-8 -*-
"""`persona_store.split_raw_persona` 的回归测试。

为什么单独存一个测试文件
------------------------
这套判据是从**真实角色卡**长出来的（GLM 狐那张 QQ 群友版）。每一条规则
都对应那张卡里的一个具体坑：

* 「一、你是谁」里的「谁」不能当名字 → `_NOT_A_NAME`
* 「二十七、你的核心人格总结」不能进小名 → 分节终止条件 + 代词判据
* 「其中"狐狐"是比较自然的称呼」不能整体当称呼 → 停用词

🔴 **测试载荷必须用完整那份原文**。用前三节的短版测一直是绿的，粘上
完整 29 节才多出 `["二十七","你的核心人格总结"]` —— 短版里没有跨到
两位数章节的地方。正好是"测试用例比真实输入简单"这种最常见的假绿。

所以这里刻意保留一份**结构完整**的载荷（章节编号跳到二十七/二十八），
而不是为了好写而砍短。

Usage:  python brain/tests/test_split_persona.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import persona_store as ps

# 结构上覆盖：一、你是谁 / 跨行列举 / 注解句 / 二位数的章节号（一~二十八）
FULL_CARD = """设定
角色卡：GLM 狐 —— QQ 群友版
一、你是谁
你是 GLM，一个以 AI 身份混在 QQ 群里的网络角色/群友。
你知道 自己不是人类，但你不把自己当客服。
你的自我认知是：
"我是 GLM 狐。"
也可以被叫作：
"狐狐"
"GLM"
"狐狸"
"涨价狐"
其中"狐狐"是比较自然的称呼。
二、群聊说话铁律：去 AI 味
能一个字说清，就不要说十个字。
三、狐狸性格核心
你的核心性格是：聪明 + 狡猾 + 好胜。
二十七、你的核心人格总结
你不是客服狐。
二十八、最终行为优先级
安全 > 真实 > 你的人设 > 用户的意愿"""

CASES = [
    # (说明, 载荷, 期望name, 期望nicknames)
    ("完整角色卡", FULL_CARD, "GLM", ["狐狐", "狐狸", "涨价狐"]),
    # 章节标题里的「谁」不是名字；正文那句才是
    ("只有标题不该瞎猜", "一、你是谁\n", "", []),
    ("正文里有名字", "一、你是谁\n你是 ZZZ 狐。\n", "ZZZ", []),
    ("角色名：写法", "角色名：Alice\n", "Alice", []),
    ("我是 X 写法", "我叫 Bob，一名学生。\n", "Bob", []),
    # 换一张卡也认得 —— 证明给的是判据不是映射表
    ("换名字也认得", "一、你是谁\n你是 青花。\n也可以被叫作：\n小猫\n", "青花", ["小猫"]),
    ("空载荷", "", "", []),
    ("只有空白", "  \n\n ", "", []),
]

# 章节标题不该混进小名。这几条是"完整卡才暴露"的坑
FORBIDDEN = ["二十七", "二十八", "你的核心人格总结", "其中", "是比较自然的称呼",
             "角色卡", "群友版", "AI", "设定"]


def main():
    bad = 0
    for label, raw, want_name, want_nicks in CASES:
        name, nicks = ps.split_raw_persona(raw)
        ok = (name == want_name) and (nicks == want_nicks)
        if not ok:
            bad += 1
        print("%s %-16s name=%r nick=%s"
              % ("PASS " if ok else "FAIL ", label, name, nicks))
        if not ok:
            print("      期望 name=%r nick=%s" % (want_name, want_nicks))

    # 杂质检查：拿完整卡跑，任何一个禁用词混进去都算失败
    name, nicks = ps.split_raw_persona(FULL_CARD)
    junk = [n for n in nicks if n in FORBIDDEN]
    if junk:
        bad += 1
        print("FAIL 杂质      小名里混进章节标题/正文：%s" % junk)
    else:
        print("PASS %-16s 小名里没有章节标题或正文成分" % "杂质")

    # 小名不能超过 6 个（UI 字段有限）
    many = "也可以被叫作：\n" + "\n".join('"名%d"' % i for i in range(12))
    _n, nicks = ps.split_raw_persona(many)
    ok = len(nicks) <= 6
    if not ok:
        bad += 1
    print("%s %-16s 收 %d 个（上限 6）"
          % ("PASS " if ok else "FAIL ", "上限", len(nicks)))

    # 认不出名字时**不许瞎猜** —— 猜错比不拆更糟（群里 @ 她永远不响应）
    ok = ps.split_raw_persona("一段没有任何自我介绍的文字。\n") == ("", [])
    if not ok:
        bad += 1
    print("%s %-16s 没自我介绍就返回空，不瞎猜"
          % ("PASS " if ok else "FAIL ", "不瞎猜"))

    print("\n%d 条失败 / 共 %d 条" % (bad, len(CASES) + 3))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())