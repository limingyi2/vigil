# -*- coding: utf-8 -*-
"""技能宿主的入口契约：setup 和 register 都得认。

## 为什么

宿主原来只认 `export function setup(api)`。而 **QQ Agent 的 skill 一律是
`export function register(api)`** —— 这层 api 本来就是照着 QQ Agent 垫出来的
（见 `brain/skill_host/server.js` 文件头），所以这类包除了入口函数叫什么以外
全都能跑。只认 setup 的后果很难查：

    agent-reach 的文件**都在磁盘上**（安装接口报 ok、"3 个文件"），
    但宿主日志写着「装载 agent-reach 失败：这个包没有 setup(api)，不是 skill」，
    面板的技能列表只列**装载成功**的 → 用户看到的是
    "提示安装成功，但下面列表里没有"。

实测(`2026-10-06`)：把入口放宽成"setup 或 register"之后，同一个包立刻装载成功，
`/skills` 从 4 个变成 5 个、多出 4 个工具。

## 这条判据的性质（说清楚，别当成功能测试）

它是**源码级**的契约检查：钉住 server.js 里那个二选一还在，并且用的确实是
`boot` 而不是又写回 `mod.setup`。真正的功能验证在云端做过（真包、真宿主），
本地再起一个 node 宿主成本太高且要抢 8790 端口，所以不在这儿重复。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "brain", "skill_host", "server.js")


class TestSkillEntryContract(unittest.TestCase):
    def setUp(self):
        self.src = io.open(SERVER, encoding="utf-8", errors="replace").read()

    def _boot_block(self):
        """把 `const boot = …` 到 `if (!boot)` 之间那段抠出来。

         **必须限定在这段里面查**，不能查整个文件。第一版就是查全文件里有没有
        `mod.register` 这个字符串 —— 把选择那行改成 `const boot = mod.setup`、
        留下下面半截 `: … mod.register` 的残骸时，字符串还在、判据照样绿。
        （我拿这个错误改法验过一次，它确实没红。）所以范围要卡死在赋值语句里。
        """
        m = re.search(r"const boot = (.*?)\n\s*if \(!boot\)", self.src, re.S)
        self.assertIsNotNone(m, "找不到 const boot = … 那段选择逻辑")
        return m.group(1)

    def test_两种入口都认(self):
        block = self._boot_block()
        self.assertIn("mod.register", block,
                      "register 那条回落没了 —— QQ Agent 的 skill 会又变成"
                      "『装上了但列表里没有』")
        self.assertIn("mod.setup", block)
        # 而且得是**同一个表达式里的二选一**，不是两行各写各的
        self.assertRegex(block, r"typeof\s+mod\.setup",
                         "setup 那半边不是 typeof 判定，形状不对")
        self.assertRegex(block, r"typeof\s+mod\.register",
                         "register 那半边不是 typeof 判定，形状不对")

    def test_真的调用了选出来的那个入口(self):
        # 只加了判断但调用处还写死 mod.setup 的话，回落等于没做
        self.assertRegex(
            self.src, r"await\s+boot\s*\(",
            "调用处没用 boot(...)，还是写死的 mod.setup —— 回落形同虚设")

    def test_报错要说清两种名字(self):
        # 报错只说 setup 的话，用户拿着一个 register 包会以为要改自己的包
        m = re.search(r"throw new Error\('这个包没有[^']*'\)", self.src)
        self.assertIsNotNone(m, "找不到那句报错")
        msg = m.group(0)
        self.assertIn("setup", msg)
        self.assertIn("register", msg)


if __name__ == "__main__":
    unittest.main()
