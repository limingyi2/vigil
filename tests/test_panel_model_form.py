# -*- coding: utf-8 -*-
"""面板的「模型与接口」页 —— 字段名和脱敏这两件事不能改坏。

## 为什么要单独立一条

面板原来那一页是**从服务端回来的列表里点一下切换**：只发 `{chat: id}`。
看着能用，但换不了 key、换不了地址 —— 而"换服务商"必然要同时改
provider / api_base / api_key 三样。于是面板上永远换不到别家模型，
而症状是"点了没反应"，不报错。

App 的 chat.html 早就是能填的那套了，两边共用 `/api/models` 和
`/api/models/apply`。这里钉住面板跟它一致。

## 钉的是哪几件会静默失效的事

1. **字段名**。`model_hub.apply_cfg` 只认 provider/api_base/api_key/
   chat/vision/image/audio。发 `{model: id}` 会走成"没有改动"，
   而界面弹"已切换" —— 看着成功了其实没换。
2. **密钥不许回填**。服务端 `public_current` 只回 `api_key_set`，
   回的是整个配置对象。把它塞进 input 就是把打码串摆在那儿，
   手一抖复制重贴回去，真 key 就被覆盖了。
3. **`changed` 为空 ≠ 成功**。apply 在没改动时回 `{ok:true, changed:[]}`，
   当成成功报"换好了"就是假成功。
4. **demo 数据形状要对**。demo 模式的 /api/models 原先是老结构
   （current 是字符串、没有 providers/groups），跟真实服务端不一致，
   于是 demo 模式下这一页渲染成空白 —— 而空白看不出是数据形状不对。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "admin", "dashboard.html")
APP = os.path.join(ROOT, "client", "src", "js", "pages.js")


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _panel():
    return _read(PANEL)


class TestPanelModelFormExists(unittest.TestCase):
    """这一页得真的能填 —— 三样都得有，只给模型名下拉不算。"""

    def setUp(self):
        self.h = _panel()

    def test_有地址和密钥两个输入框(self):
        # 光有模型下拉是原来那版的状态，换不了 key 和地址
        self.assertIn("接口地址", self.h, "面板上没有接口地址输入框")
        self.assertIn("密钥", self.h, "面板上没有密钥输入框")
        self.assertIn("api_base", self.h)
        self.assertIn("api_key", self.h)

    def test_密钥框是密码类型且不回填(self):
        # type=password：密钥明文摆在一个能截图的输入框里是最低成本的泄露
        m = re.search(r"apiKey\s*=\s*el\('input',\s*\{([^}]*)\}", self.h)
        self.assertIsNotNone(m, "找不到密钥输入框的定义")
        self.assertIn("password", m.group(1),
                      "密钥框不是 type=password —— 输入框里的东西肉眼可见、随手可截图")
        # 上面那行只是初始状态。回填路径必须显式清空。
        self.assertIn("apiKey.value = ''", self.h,
                      "保存后没清空密钥框 —— 打码串或明文会多留在页面上")

    def test_按_api_key_set_显示状态而不是打码串(self):
        """placeholder 里出现打码串是有害的：它在输入框里，复制重贴回去
        就会把真 key 覆盖掉（apply_cfg 的 is_masked 挡的是另一回事）。

         判据必须用正则加词边界，不能用 `assertNotIn("placeholder: mCur.api_key")`
        —— `mCur.api_key_set` 里含 `mCur.api_key` 这个子串，那样写会对着
        正确写法报红。第一版就是这么写的，结果测试变红而代码没问题。
        """
        # `\b` 在 key 后面不成立（`_` 是词字符）⇒ 不会误伤 api_key_set
        self.assertNotRegex(self.h, r"placeholder:\s*mCur\.api_key\b",
                            "把打码串摆进了 placeholder")
        self.assertRegex(self.h, r"placeholder:\s*mCur\.api_key_set\b",
                         "应该用 api_key_set 判断有没有那把")


class TestPanelSendsRightFieldNames(unittest.TestCase):
    """字段名错了不报错，只是"没有改动"。"""

    def setUp(self):
        self.h = _panel()

    def test_发的是_chat_不是_model(self):
        m = re.search(r"body:\s*payload", self.h)
        self.assertIsNotNone(m, "找不到保存请求")
        # payload 那几行
        blk = re.search(r"const payload = \{(.*?)\};", self.h, re.S)
        self.assertIsNotNone(blk, "找不到 payload 定义")
        for k in ("provider", "api_base", "api_key", "chat"):
            self.assertRegex(blk.group(1), r"\b%s\b" % k,
                             "payload 里没有 %s —— 服务端 apply_cfg 只认这个" % k)
        self.assertIn("chat: selChat.value", self.h,
                      "对话模型字段名/取值不对")
        self.assertNotIn("body:{chat:m.id}", self.h,
                         "还在用旧写法发 {chat: id}，那只切模型名")

    def test_空key_被当作沿用而不是清空(self):
        """留空 = 沿用现在那把。判据在服务端（`if key and not is_masked`），
        面板这边只要别把空串当"用户想清空"发出去就够。"""
        self.assertIn("(apiKey.value || '').trim()", self.h)
        self.assertNotIn("api_key: apiKey.value,", self.h,
                         "直接把原值发过去了，没 trim 也没走空串沿用")


class TestPanelApplyResultHonest(unittest.TestCase):
    """apply 在没改动时回 {ok:true, changed:[]} —— 那是"没改动"，不是成功。"""

    def setUp(self):
        self.h = _panel()

    def test_空changed_不报成功(self):
        self.assertRegex(self.h, r"changed\s*&&\s*!r\.changed\.length",
                         "没判空 changed —— 会把「没有改动」显示成「换好了」")
        self.assertIn("没有改动", self.h)

    def test_失败时报的是服务端给的_err(self):
        self.assertIn("r.err", self.h, "失败提示里没有 err —— 只会显示'未知原因'")

    def test_地址为空和模型为空都拦一下(self):
        """不拦也能发出去，但服务端会拿一个空 api_base 去调模型，
        她直接哑掉，而界面显示「保存成功」。"""
        self.assertIn("地址得填", self.h)
        self.assertIn("至少选一个对话模型", self.h)


class TestPanelProviderNotHardcoded(unittest.TestCase):
    """服务商那一栏必须从服务端回传的 providers 渲染。

    写死两个 div 加一个 `['siliconflow','custom']` 数组的话，
    服务端 model_hub.PROVIDERS 加第三家（小米 MiMo 就是这么加的）时必漏 ——
    而漏的表现不是报错，是面板上没有那个按钮、用户以为不支持。
    """

    def setUp(self):
        self.h = _panel()

    def test_从_providers_渲染(self):
        self.assertIn("mProvs", self.h, "没有读 providers")
        self.assertIn("mProvs.forEach", self.h, "服务商按钮不是按列表渲染的")

    def test_切换服务商会自动带上它的地址(self):
        """key 仍然要自己填（不存明文在 html 里），但地址可以自动带 ——
        否则每换一家都要手打一遍域名，输错了报的是 401，很难查。"""
        self.assertIn("if (x.base) apiBase.value = x.base", self.h)

    def test_服务商列表来自服务端而不是写死(self):
        """面板这一栏必须由服务端返回的 providers 驱动，且三家的说明文案都在。

        2026-10-08：原来这条是拿"面板文案 vs App 的 PROV_HINT"互相比对；
        App 改成框架页后那边没有这份文案了，改为**只钉面板自己**：
        三家的说明都在，且下拉是按 provs 渲染的。
        """
        self.assertIn("mProvs", self.h,
                      "面板没读服务端返回的 providers")
        for key in ("siliconflow", "xiaomi", "custom"):
            pat = r"(?<![\w'\"])%s\s*:" % key
            self.assertRegex(self.h, pat, "面板缺 %s 的说明文案" % key)


class TestPanelDemoShapeMatchesServer(unittest.TestCase):
    def setUp(self):
        h = _panel()
        m = re.search(r"if \(p\.indexOf\('/api/models'\) === 0\) \{(.*?)\n  \}", h, re.S)
        self.assertIsNotNone(m, "demo 里没有 /api/models 分支")
        self.demo = m.group(1)

    def test_current是对象不是字符串(self):
        self.assertIn("api_base", self.demo, "demo 的 current 不含 api_base")
        self.assertIn("chat:", self.demo, "demo 的 current 不含 chat")
        self.assertIn("api_key_set", self.demo,
                      "demo 的 current 不含 api_key_set —— 面板靠它判'已有一把'")

    def test_有_providers_and_groups(self):
        self.assertIn("providers:", self.demo)
        self.assertIn("groups:", self.demo)
        self.assertIn("source:", self.demo)


class TestPanelJsSyntax(unittest.TestCase):
    def test_内联脚本能编译(self):
        h = _panel()
        m = re.search(r"<script>(.*?)</script>", h, re.S)
        self.assertIsNotNone(m, "找不到内联脚本")
        import subprocess
        import shutil
        node = shutil.which('node')
        if not node:
            self.skipTest("找不到 node，跳过语法检查")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, 'panel.js')
            with open(script, 'w', encoding='utf-8') as output:
                output.write(m.group(1))
            r = subprocess.run([node, '--check', script], text=True,
                               capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, "面板脚本编译失败：%s" % r.stderr[:300])


if __name__ == "__main__":
    unittest.main()
