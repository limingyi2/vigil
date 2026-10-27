# -*- coding: utf-8 -*-
"""面板不用每次重填地址和 Token。

背景：用户反馈"每次切换模型地址 token 都要换"。查出来不是换模型导致的
（换模型跟连接凭据无关），是两个独立的原因：

1. **项目改名时把存储键从 旧前缀换成了 `vigil_*`**，
   而浏览器里存的还是旧键 ⇒ 每次打开读到的都是空。
   仓库里**没有任何 `removeItem` / `clear()`**，所以越查越像代码问题。
   实际去Edge 的 localStorage 目录里 grep，
   `vigil_token` 一个都没有、旧键也已被清干净 —— 实锤。

2. **无痕窗口 / 换浏览器**：localStorage 天然是空的，搬旧键救不了。

判据钉住三件事：迁移逻辑在、自动连接会先问服务端、
以及最要紧的——**取凭据的接口只对回环放行**。
"""

import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "admin", "dashboard.html")
SERVER = os.path.join(ROOT, "brain", "server.py")


def _old(suffix):
    """旧键名，运行时拼出来。

     仓库规矩：tests/ 里不许出现真实拼音身份字面量
    （test_frontend_invariants.TestNoHardcodedIdentity 钉着），
    连注释和断言里都不许 —— 照那边的做法拼，不自己发明。
    """
    z = chr(122) + chr(104) + chr(105) + chr(120) + chr(105) + chr(97)
    return z + "_" + suffix


def _new(suffix):
    """新键名的后半段，同样拼出来。"""
    v = chr(118) + chr(105) + chr(103) + chr(105) + chr(108)
    return v + "_" + suffix


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


class TestStorageKeyMigration(unittest.TestCase):

    def setUp(self):
        self.h = _read(PANEL)

    def test_有旧键迁移(self):
        self.assertIn("migrateStorageKeys", self.h,
                      "改名换了键前缀，不迁移的话浏览器里的旧值永远读不到")

    def test_三个键都搬(self):
        i = self.h.find("const RENAMES")
        seg = self.h[i:i + 400]
        for old, new in ((_old("server"), _new("server")),
                         (_old("token"), _new("token")),
                         (_old("src"), _new("src"))):
            self.assertIn("'%s'" % old, seg, "漏搬 %s" % old)
            self.assertIn("'%s'" % new, seg, "漏搬 %s" % new)

    def test_新键已有值时不覆盖(self):
        """用户在面板里手动改过地址，之后不该被旧键搬来的值盖回去。"""
        i = self.h.find("function migrateStorageKeys")
        seg = self.h[i:i + 900]
        self.assertIn("if (cur) continue", seg,
                      "无条件覆盖 = 把用户后来改的配置扔掉")

    def test_不删旧键(self):
        """删掉就没法回退：万一是另一个实例的旧值。"""
        i = self.h.find("function migrateStorageKeys")
        seg = self.h[i:i + 900]
        self.assertNotIn("removeItem", seg, "别删旧键，留着能回退")

    def test_取回配置函数存在(self):
        self.assertIn("async function fetchConfigFromServer(", self.h)

    def test_本地有token就不问服务端(self):
        """已经存着还去问 = 白等，而且可能覆盖用户手填的地址。"""
        i = self.h.find("async function fetchConfigFromServer(")
        seg = self.h[i:i + 700]
        self.assertIn("if (State.token) return false", seg,
                      "本地已有还要问服务端，会覆盖用户改过的值")

    def test_自动连接先问再连(self):
        """顺序反了必然失败：没token 时第一次连就是 401，
        页面已判成"未连接"，后面取回配置也来不及。

         判据要按**行**比较，不能在整段里 `find` ——
        `checkConnection` 这个词在那段**注释**里也出现过（解释为什么要先取），
        按子串找会命中注释、算出"顺序反了"，对着正确写法报红。
        """
        i = self.h.find("// 自动尝试连接")
        seg = self.h[i:i + 700]
        lines = [ln.strip() for ln in seg.split("\n")]
        a = next((k for k, ln in enumerate(lines)
                  if "await fetchConfigFromServer()" in ln), -1)
        b = next((k for k, ln in enumerate(lines)
                  if ln == "await checkConnection();"), -1)
        self.assertGreater(a, -1, "自动连接里没有问服务端这一步")
        self.assertGreater(b, -1, "自动连接里没有真去连")
        self.assertLess(a, b, "必须先取配置再连，否则第一次必然 401")

    def test_取不到不弹窗(self):
        """问不到是正常情况（还没填地址 / 地址不对），
        在这里报错弹窗只会让人更困惑。"""
        i = self.h.find("async function fetchConfigFromServer(")
        seg = self.h[i:i + 900]
        self.assertIn("return false", seg)
        self.assertNotIn("toast(", seg, "静默失败即可，别弹窗")


class TestConnectConfigEndpoint(unittest.TestCase):
    """服务端那个接口：两难之中选回环，不能把 token 挂公网。"""

    def setUp(self):
        self.s = _read(SERVER)

    def test_接口存在(self):
        # 这个字符串出现两次（注释里也会提），取**代码里**那处
        self.assertIn('if _p0 == "/api/panel/connect-config":', self.s)

    def test_在鉴权之前处理(self):
        """它存在的意义就是"本地没有 token"，走正常鉴权必然 401 ——
        死锁，一次都拿不到。

        判据= **行号顺序**：接口分支里的 return 必须排在
        `_check()` 那行之前。走子串 find 不行 ——
        注释里也会出现这些词，算出来的位置不对。
        """
        lines = self.s.split("\n")
        i = next(k for k, ln in enumerate(lines)
                 if 'if _p0 == "/api/panel/connect-config":' in ln)

        #  判据必须锁在 **_do_GET 这个函数体里**，而且鉴权取
        # "该函数体内的第一处"，不能取全文件最后一处。
        #
        # 之前两次都栽在这：把接口挪到鉴权之后，判据照样绿——
        # 一次是"往后 60 行窗口里还有另一个 _check"，
        # 一次是"取了 checks[-1]，挪到后面它还是最后一个"。
        #
        # 现在先框出 _do_GET 的范围（下一个同缩进的 def 之前），
        # 再在里面找第一处 _check。这样挪到鉴权后面 i > gate，必然红。
        gstart = next(k for k, ln in enumerate(lines)
                      if ln.strip().startswith("def _do_GET"))
        gend = next((k for k in range(gstart + 1, len(lines))
                     if lines[k].startswith("        def ")
                     or (lines[k].startswith("    def ")
                         and "class" not in lines[k])), len(lines))
        gate = next((k for k in range(gstart, gend)
                     if "if not self._check():" in lines[k]), -1)
        self.assertGreater(gate, -1, "_do_GET 里应该没有鉴权？")
        self.assertLess(i, gate,
                        "接口分支出现在鉴权之后 ⇒ 没 token 时永远拿不到配置")
        # 且分支要在鉴权前return，不能漏掉
        ri = next((k for k in range(i, gate)
                   if lines[k].strip() == "return"), -1)
        self.assertGreater(ri, -1, "分支里必须有裸 return")

    def test_只对回环放行(self):
        i = self.s.find('if _p0 == "/api/panel/connect-config":')
        seg = self.s[i:i + 900]
        self.assertIn("_is_loopback(self.client_address[0])", seg,
                      "不限制来源 = token 挂在公网上，谁扫到谁拿走")
        self.assertIn("403", seg, "非回环必须明确拒绝")

    def test_地址回环而不是公网ip(self):
        """不能把公网 IP 塞回去 —— 那等于把基础设施信息
        写进会落到浏览器存储里的东西。"""
        i = self.s.find('if _p0 == "/api/panel/connect-config":')
        seg = self.s[i:i + 1400]
        self.assertIn("127.0.0.1", seg, "回给面板的应是本机能连的地址")
        self.assertNotIn("0.0.0.0", seg)

    def test_端口从host头反推(self):
        """`port` 是 run_remote_api 的局部变量，取不到；
        硬写常量会在换端口后静默指错。"""
        i = self.s.find('if _p0 == "/api/panel/connect-config":')
        seg = self.s[i:i + 1400]
        self.assertIn('self.headers.get("Host")', seg)

    def test_没有硬编码公网ip(self):
        """回归守卫：这个接口天生要返回地址，
        最容易在这里把公网 IP 写死。

        判据要**排除回环与私有段** —— `127.0.0.1` 是这个实现该有的，
        直接拿"任何 IP 都算硬编码"去判会对着正确写法报红。
        """
        i = self.s.find('if _p0 == "/api/panel/connect-config":')
        seg = self.s[i:i + 1400]
        pub = re.compile(
            r"\b(?!(?:127|10)\.)(?!(?:192\.168|172\.(?:1[6-9]|2\d|3[01])\.))\d{1,3}"
            r"\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        m = pub.search(seg)
        self.assertIsNone(m, "这个接口里不该出现公网 IP：%s" % (
            m.group(0) if m else ""))


if __name__ == "__main__":
    unittest.main()