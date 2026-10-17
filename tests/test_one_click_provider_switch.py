# -*- coding: utf-8 -*-
"""点服务商就是切换 —— 服务端那半。

上一版里"点一下硅基流动"只改了输入框里的文字，还得再点保存才生效；
而就算点了保存也只有一把 `api_key`，切到别家她照样调不动（401）。

这里钉四件：
1. 各家密钥分别存着，切回去自动换上那把
2. 没存过密钥的那家，界面上必须能认出来（不然显示"已切换"而她哑了）
3. 老配置要能迁移（`api_keys` 原来不存在）
4. `public_current` 不许带出明文
"""

import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import model_hub as M          # noqa: E402

SILICON = "sk-silicon-abcdef123456"
XIAOMI = "sk-xiaomi-abcdef1234567"


def _cfg(**kw):
    d = {"provider": "xiaomi",
         "api_base": "https://api.xiaomimimo.com/v1",
         "api_key": XIAOMI,
         "model": "mimo-v2.6-flash"}
    d.update(kw)
    return d


class TestProviderKeyStore(unittest.TestCase):

    def test_两家密钥各自保存(self):
        c1, _ = M.apply_cfg(_cfg(), {"provider": "siliconflow",
                                     "api_key": SILICON, "chat": "Qwen/Qwen3-8B"})
        c2, _ = M.apply_cfg(c1, {"provider": "xiaomi",
                                 "api_key": XIAOMI, "chat": "mimo-v2.6-flash"})
        bag = c2.get("api_keys") or {}
        self.assertEqual(M.provider_key(c2, "siliconflow", M.PROVIDERS["siliconflow"]["base"]), SILICON)
        self.assertEqual(M.provider_key(c2, "xiaomi", M.PROVIDERS["xiaomi"]["base"]), XIAOMI)

    def test_切回去自动换上那家的key(self):
        """这是"点一下就切换"能成立的前提：不换key 切过去她调不动。"""
        c1, _ = M.apply_cfg(_cfg(), {"provider": "siliconflow",
                                     "api_key": SILICON, "chat": "Qwen/Qwen3-8B"})
        c2, _ = M.apply_cfg(c1, {"provider": "xiaomi", "chat": "mimo-v2.6-flash"})
        self.assertEqual(c2.get("api_key"), XIAOMI,
                         "切回小米却还用别家的密钥")
        self.assertEqual(c2.get("provider"), "xiaomi")

    def test_切到没存过密钥的厂商不换key(self):
        """换过去之后那把 key 是别家的 —— 界面必须能认出这种情况，
        否则会显示"已切换"而她下一句就401。判据在 public_current。"""
        c, changed = M.apply_cfg(_cfg(), {"provider": "siliconflow",
                                          "chat": "Qwen/Qwen3-8B"})
        self.assertEqual(c.get("provider"), "siliconflow")
        self.assertFalse(M.has_provider_key(c, "siliconflow"))
        pc = M.public_current(c)
        self.assertEqual(pc.get("key_belongs_to"), "",
                         "把别家的密钥当成这家的了")

    def test_已存密钥的厂商归属正确(self):
        c, _ = M.apply_cfg(_cfg(), {"provider": "siliconflow",
                                    "api_key": SILICON, "chat": "Qwen/Qwen3-8B"})
        pc = M.public_current(c)
        self.assertEqual(pc.get("key_belongs_to"), "siliconflow")
        self.assertTrue(pc.get("keys_by_provider", {}).get("siliconflow"))
        self.assertFalse(pc.get("keys_by_provider", {}).get("custom"))

    def test_老配置会被迁移(self):
        """`api_keys` 是后来才有的。老配置里只有一把 api_key，
        不迁移的话界面把每一家都显示成"还没存密钥"。"""
        c = _cfg()                       # 没有 api_keys
        # Active legacy keys are usable at their original endpoint only.
        self.assertTrue(M.has_provider_key(c, "xiaomi", c['api_base']))
        self.assertEqual(M.provider_key(c, 'xiaomi', 'https://other.invalid/v1'), '')
        self.assertFalse(M.adopt_current_key(c))
        self.assertTrue(M.public_current(c).get("keys_by_provider", {}).get("xiaomi"))

    def test_迁移是幂等的(self):
        """run_server 每次启动都调它。重复调不能把已有那份覆盖掉。"""
        c = _cfg(api_keys={"xiaomi": XIAOMI, "siliconflow": SILICON})
        self.assertFalse(M.adopt_current_key(c))
        self.assertEqual(c["api_keys"]["siliconflow"], SILICON)

    def test_迁移在缺key时不动(self):
        c = _cfg(api_key="")
        self.assertFalse(M.adopt_current_key(c))
        self.assertNotIn("api_keys", c)

    def test_迁移不覆盖别家已登记的(self):
        """当前服务商是小米、但 api_keys 里登记的是硅基那把 ——
        这是老配置手动改过的情况，不能拿小米那把盖掉硅基。"""
        c = _cfg(api_keys={"siliconflow": SILICON})
        M.adopt_current_key(c)
        self.assertEqual(c["api_keys"]["siliconflow"], SILICON)

    def test_打码串不会写进api_keys(self):
        c, changed = M.apply_cfg(_cfg(), {"api_key": M.mask_key(XIAOMI)})
        self.assertNotIn("api_keys", c)
        self.assertTrue(any("密钥" in x for x in changed))

    def test_公开视图不带明文(self):
        c, _ = M.apply_cfg(_cfg(), {"provider": "siliconflow",
                                    "api_key": SILICON, "chat": "Qwen/Qwen3-8B"})
        pc = M.public_current(c)
        blob = json.dumps(pc, ensure_ascii=False)
        self.assertNotIn(SILICON, blob)
        self.assertNotIn(XIAOMI, blob)


class TestRuntimeWhitelist(unittest.TestCase):
    """API_CFG_KEYS 少一个键，内存那份就当場少一个键（update 是整体替换）。"""

    def _keys(self):
        import runtime
        return runtime.API_CFG_KEYS

    def test_名单含provider(self):
        self.assertIn("provider", self._keys(),
                      "换服务商后内存里还是旧家 —— 面板显示已切换而她连错地方")

    def test_名单含api_keys(self):
        self.assertIn("api_keys", self._keys(),
                      "各家密钥在内存里没了，下次切回那家要重填")

    def test_名单仍含既有键(self):
        for k in ("api_base", "api_key", "model", "vision", "voice",
                  "uapi", "persona_file"):
            self.assertIn(k, self._keys(), "别把既有键弄丢了")


PANEL = os.path.join(ROOT, "admin", "dashboard.html")


def _panel():
    with io.open(PANEL, encoding="utf-8") as f:
        return f.read()


class TestPanelOneClickSwitch(unittest.TestCase):
    """面板那半。"""

    def setUp(self):
        self.h = _panel()

    def test_点服务商会真的落盘(self):
        self.assertIn("await saveModel(", self.h,
                      "点服务商只改输入框，还得再点保存 —— 看着切了其实没切")
        self.assertIn("async function switchProvider(", self.h)

    def test_切服务商要重拉列表(self):
        i = self.h.find("async function switchProvider(")
        seg = self.h[i:i + 2000]
        self.assertIn("await pullModels(false, x.key)", seg,
                      "不重拉的话列表还是上一家的")

    def test_没存密钥的厂商不切(self):
        i = self.h.find("async function switchProvider(")
        seg = self.h[i:i + 2000]
        self.assertIn("if (!mKeys[x.key])", seg,
                      "没密钥还照切 —— 显示已切换而她401，比没切更难查")
        self.assertIn("apiKey.focus()", seg)

    def test_显示当前服务商(self):
        self.assertIn("mProvLabel", self.h,
                      "界面上唯一能确认切没切过去的只有按钮高亮，"
                      "而高亮只是输入框状态")

    def test_没重渲染整页(self):
        """renderView 会清空容器，toast 一起没 —— 表现为
        '切换成功了但屏幕上什么都没发生'。

        🔴 判据只看真实调用，**不能整段扫**：那段代码里我自己写了
        一句解释它为什么不能那么做的注释，字面上就含 `renderView('config')`
        —— 按子串判会对着正确写法报红（这个坑之前踩过一次）。
        """
        i = self.h.find("async function saveModel(")
        seg = self.h[i:i + 2600]
        # 真调用形如 `renderView('config');` 出现在行首/缩进后，
        # 而注释里的是 `renderView('config')：`（后面跟冒号）
        import re
        calls = re.findall(r"^\s*renderView\('config'\)", seg, re.M)
        self.assertEqual(calls, [], "不能重渲染整页，会把 toast 一起清掉")

    def test_保存失败回滚选中态(self):
        i = self.h.find("async function saveModel(")
        seg = self.h[i:i + 2600]
        self.assertIn("wasProv", seg,
                      "存失败却留着选中态 = 界面看着选中了但其实没生效")


if __name__ == "__main__":
    unittest.main()