"""判据：换服务商这条链路（model_hub + App 前端）。

三件事必须成立：
1. PROVIDERS 加一家就够，不需要改别的地方（前端按 providers 渲染，不写死）
2. 老配置没有 provider 字段时，按 base 能反推出正确的那家
3. 分类规则认得小米的模型 id（尤其 -omni / -tts 别被归错）

跑法：F:/Vigil/venv/Scripts/python.exe -m pytest tests/test_model_hub.py -q
"""
import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
for p in (os.path.join(ROOT, "brain"),):
    if p not in sys.path:
        sys.path.insert(0, p)

import model_hub as M                                  # noqa: E402

# 2026-10-08：模型面板已从 App 搬到 Electron 客户端，界面断言改读客户端源码。
# App 现在只是框架页（连接 + 更新），模型/人设界面都在客户端里。
CHAT_HTML = os.path.join(ROOT, "client", "src", "js", "pages.js")


class TestProviders(unittest.TestCase):
    def test_xiaomi_is_registered_with_a_working_base(self):
        self.assertIn("xiaomi", M.PROVIDERS)
        meta = M.PROVIDERS["xiaomi"]
        self.assertTrue(meta["name"], "没有显示名")
        # 结尾要带 /v1 —— 前端靠它拼 /models，少了拉不到列表
        self.assertTrue(meta["base"].rstrip("/").endswith("/v1"), meta["base"])
        self.assertIn("xiaomimimo", meta["base"])

    def test_every_provider_has_the_three_fields(self):
        """前端按 name / base 两个字段渲染，少一个就出空按钮或空地址。"""
        for k, v in M.PROVIDERS.items():
            for field in ("name", "base", "models_doc"):
                self.assertIn(field, v, "%s 缺 %s" % (k, field))
            self.assertIsInstance(v["name"], str)
            self.assertIsInstance(v["base"], str)

    def test_custom_base_stays_empty(self):
        """custom 的 base 必须留空 —— 它代表"用户自己填"，
        有默认地址就会覆盖掉用户填的那个。"""
        self.assertEqual(M.PROVIDERS["custom"]["base"], "")


class TestProviderGuessing(unittest.TestCase):
    """老配置没有 provider 字段，靠 api_base 反推。"""

    def guess(self, base):
        return M.current({"api_base": base})["provider"]

    def test_known_bases_map_to_their_own_provider(self):
        for pid, meta in M.PROVIDERS.items():
            if pid == "custom":
                continue
            self.assertEqual(self.guess(meta["base"]), pid,
                             "%s 的地址被判成了别家" % pid)

    def test_trailing_slash_does_not_break_guessing(self):
        self.assertEqual(self.guess("https://api.xiaomimimo.com/v1/"),
                         M.current({"api_base": "https://api.xiaomimimo.com/v1"})["provider"])

    def test_unknown_base_is_custom(self):
        self.assertEqual(self.guess("https://api.deepseek.com/v1"), "custom")
        self.assertEqual(self.guess(""), "custom")

    def test_explicit_provider_field_wins(self):
        """写了 provider 就认它，不看 base —— 否则改地址时会被反推覆盖。"""
        got = M.current({"provider": "xiaomi",
                         "api_base": "https://api.siliconflow.cn/v1"})
        self.assertEqual(got["provider"], "xiaomi")

    def test_guessing_never_returns_custom_when_base_is_known(self):
        """防退化：将来 PROVIDERS 加到第三家，这段循环不能只认第一家。"""
        base = M.PROVIDERS["xiaomi"]["base"]
        self.assertEqual(self.guess(base), "xiaomi")
        self.assertNotEqual(self.guess(base), "custom")


class TestClassify(unittest.TestCase):
    # 这 9 个是2026-10-06 用真 key 打 /v1/models 拉下来的真实列表，
    # 不是照文档抄的。文档里有几个 id（mimo-v2-flash / mimo-7b-instruct /
    # mimo-v2.5-omni）在实际列表里根本不存在，照文档写就会给出选不了的项。
    XIAOMI_REAL = [
        "mimo-v2.5", "mimo-v2.5-asr", "mimo-v2.5-pro",
        "mimo-v2.5-tts", "mimo-v2.5-tts-voiceclone",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed",
    ]

    def test_every_real_xiaomi_id_is_classified_something(self):
        for m in self.XIAOMI_REAL:
            self.assertIn(M.classify(m),
                          ("chat", "vision", "image", "audio"), m)

    def test_xiaomi_chat_models(self):
        for m in ("mimo-v2.5", "mimo-v2.5-pro", "mimo-v2.6-pro",
                  "mimo-v2.6-pro-ultraspeed", "mimo-v2.6-flash",
                  "MiMo-V2.5-Pro"):
            self.assertEqual(M.classify(m), "chat", m)

    def test_xiaomi_speech_models_are_audio(self):
        """asr 是语音识别、tts 是合成，都归audio。
        归错的话它们会出现在对话模型下拉里，选了就是 400。"""
        for m in ("mimo-v2.5-asr", "mimo-v2.5-tts",
                  "mimo-v2.5-tts-voiceclone", "mimo-v2.5-tts-voicedesign"):
            self.assertEqual(M.classify(m), "audio", m)

    def test_ultraspeed_is_still_chat(self):
        """-ultraspeed 看着像别的什么，其实是纯语言模型。"""
        self.assertEqual(M.classify("mimo-v2.6-pro-ultraspeed"), "chat")

    def test_turbo_does_not_imply_image(self):
        """Qwen 一堆 -Turbo 是语言模型。这条钉住 img 的关键字表。"""
        self.assertEqual(M.classify("Qwen/Qwen2.5-72B-Instruct-Turbo"), "chat")

    def test_unknown_model_defaults_to_chat(self):
        self.assertEqual(M.classify(""), "chat")
        self.assertEqual(M.classify("some/new-model"), "chat")


class TestFallbackList(unittest.TestCase):
    def test_fallback_mentions_xiaomi(self):
        """拉不到在线列表时的兜底清单里要有小米那几个，
        否则新用户换过去只能手打模型名。"""
        allids = [m for v in M.FALLBACK.values() for m in v]
        self.assertTrue(any("mimo" in m for m in allids), M.FALLBACK["chat"])

    def test_xiaomi_fallback_entries_are_real_ids(self):
        """🔴 兜底清单里的小米 id 必须真实存在。

        2026-10-06 实测：文档里写的 mimo-v2-flash / mimo-7b-instruct /
        mimo-v2.5-omni 都不在真实列表里，照文档抄进兜底清单 =
        拉不到列表时给用户三个选不上的选项。
        """
        real = set(TestClassify.XIAOMI_REAL)
        for group in ("chat", "audio"):
            for mid in M.FALLBACK.get(group, []):
                if mid.startswith("mimo"):
                    self.assertIn(mid, real,
                                  "兜底清单里的 %s 不在真实 /v1/models 列表里"
                                  % mid)

    def test_fallback_ids_are_grouped_consistently(self):
        """清单里的 id 必须和 classify 的结果一致 ——
        不一致就是"下拉里列在语音栏、点进去却是对话模型"。"""
        for group, ids in M.FALLBACK.items():
            if group == "audio":
                continue          # 语音模块还没做，分类宽松一点
            for mid in ids:
                self.assertEqual(M.classify(mid), group,
                                 "%s 在 %s 栏但 classify 说它是 %s"
                                 % (mid, group, M.classify(mid)))


class TestAppHtml(unittest.TestCase):
    """界面侧：服务商那一栏必须由服务端返回的 providers 驱动。

    2026-10-08：这段界面从 App 搬到了 Electron 客户端，App 只剩框架页。
    实现方式随之变了 —— App 当年是 markProvider() + provSeg 容器，客户端是
    `<select id="mProv">` + change 事件。这里跟着改成客户端的形状，但**钉的还是
    同一件事**：服务端加一家，前端不用改代码就多一个选项；换下拉要把地址带出来。
    """

    def setUp(self):
        self.src = io.open(CHAT_HTML, encoding="utf-8").read()

    def _model_page(self):
        """抠出模型页那段源码（含下拉与 change 处理），别整份文件里瞎找。
        2026-10-09 起模型页加了云端/本机切换和自动拉清单，函数变长了，
        窗口跟着放宽（别掐得太紧把 change 处理截在外面）。"""
        i = self.src.index("async function model(")
        return self.src[i:i + 40000]

    def test_provider_select_is_rendered_from_server_list(self):
        """下拉必须由 provs（服务端返回）渲染，不是写死的 option。"""
        page = self._model_page()
        self.assertIn("provs.map", page,
                      "模型页没用 provs 渲染服务商下拉 —— 服务端加一家前端不会跟着多")
        self.assertIn("'<option value=", page,
                      "服务商下拉没有渲染 option")

    def test_no_hardcoded_provider_ids_in_model_page(self):
        """模型页里不许出现写死的 provider id 数组。

        写死的话，服务端 PROVIDERS 加第三家时前端不会跟着多一个选项 ——
        接口返回了 providers 却不用，等于没返回。
        """
        page = self._model_page()
        for pid in ("'siliconflow'", '"siliconflow"', "'xiaomi'", '"xiaomi"'):
            self.assertNotIn(pid, page,
                             "模型页里写死了 provider id（%s），应改成读 provs" % pid)

    def test_provider_switch_fills_base_from_the_list(self):
        """换下拉要把这一家的默认地址带出来。

        不做这一步：选完只改了服务商名字，地址还停在上一家 —— 服务端拿旧地址 +
        新服务商名去调，看着切了其实没切。
        （2026-10-09 起处理抽进 provPull：按家查 provs 允许先用局部变量 pk
        接住 provSel.value —— 钉的是"查了 provs"这件事本身。）
        """
        page = self._model_page()
        self.assertIn("provSel.addEventListener('change'", page,
                      "服务商下拉没有 change 处理")
        self.assertRegex(page, r"provs\.find\(\(x\) => x\.key === (provSel\.value|pk)\)",
                         "换服务商时没按 key 去 provs 里查这一家")
        self.assertIn("baseInp.value = p.base", page,
                      "换服务商时没把这一家的默认地址带出来")

    def test_custom_provider_keeps_manual_base(self):
        """自定义那家不能强行覆盖地址（用户手填的 localhost / vllm 得留着）。"""
        page = self._model_page()
        self.assertIn("p.key !== 'custom'", page,
                      "换到自定义服务商时不该覆盖用户手填的地址")

    def test_no_leftover_static_provider_divs(self):
        """旧的写死 div 要删干净，否则会和新渲染的那份并存（两个服务商栏）。"""
        self.assertNotIn("id=\"prov-siliconflow\" onclick", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
