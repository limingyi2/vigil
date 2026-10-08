# -*- coding: utf-8 -*-
"""服务商风控拒答的处理：**识别出来、不入她的历史**。

背景（2026-10-07）：切到小米 MiMo 后她句句返回
"The request was rejected because it was considered high risk"，
那段英文被当成她的回复显示出来还进了对话历史 —— 用户以为"她一直说英文"。
定位结论：小米判 `personas/base.json` 的 notes（含带性暗示的固定形象描写）为
高风险；而且是**内容累积触发**（干净对话照常回中文）。

2026-10-07 用户决定：**不做自动降级**（换一家重问，同一段记忆换个模型说出来
就不是同一个人了）。所以这里只钉两件事：
  1. 认得出拒答语（短句才判，避免误杀正常回复）
  2. 拒答**照旧显示**（平台的话，如实给用户看），但**绝不能进她的历史/记忆**
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))

import brain as B  # noqa: E402

REFUSAL = "The request was rejected because it was considered high risk"


class TestRefusalDetection(unittest.TestCase):
    def test_xiaomi_english_risk_line(self):
        """小米那句原话必须被认出来。"""
        self.assertTrue(B.is_provider_refusal(REFUSAL))

    def test_common_policy_phrases(self):
        for s in ("Content policy violation",
                  "This request was blocked by our usage policies",
                  "输入内容存在风险"):
            self.assertTrue(B.is_provider_refusal(s), s)

    def test_her_normal_chinese_reply_is_not_refusal(self):
        """她正常说话绝不能被误判。"""
        for s in ("困得快睡着了，你bug修完了？",
                  "我刚刚走神了下…你再说一遍？",
                  "你在干嘛呀",
                  ""):
            self.assertFalse(B.is_provider_refusal(s), s)

    def test_long_text_is_not_refusal(self):
        """拒答语都是短句；超长的按正常回复算（宁可漏判也不误杀）。"""
        long_txt = "content policy" + "啊" * 500      # >400 字符
        self.assertGreater(len(long_txt), 400)
        self.assertFalse(B.is_provider_refusal(long_txt))

    def test_none_is_safe(self):
        self.assertFalse(B.is_provider_refusal(None))


class _Resp(object):
    def __init__(self, obj):
        self._o = obj

    def raise_for_status(self):
        pass

    def json(self):
        return self._o


class TestRefusalNeverStoredAsHerWords(unittest.TestCase):
    """走完整 chat()：主服务商拒答时，那句话不能进她的对话历史。"""

    def setUp(self):
        import plugin_host
        plugin_host.load(force=True)
        self.seen = []
        self._real_httpx = B.httpx

    def tearDown(self):
        B.httpx = self._real_httpx

    def _stub_refusal(self):
        def _post(url, headers=None, json=None, timeout=None):
            self.seen.append(url)
            return _Resp({"choices": [{"message": {"content": REFUSAL}}]})

        B.httpx = type("H", (), {"post": staticmethod(_post)})

    def _brain(self):
        return B.Brain(mem=None, api_config={
            "api_key": "sk-xm",
            "api_base": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.6-flash",
        })

    def test_refusal_shown_but_not_logged(self):
        self._stub_refusal()
        b = self._brain()
        ans, mode = b.chat("你在干嘛呀", log=False, src="app")
        # 照旧显示平台的话（用户能看到是谁拦的），不做降级
        self.assertEqual(ans, REFUSAL)
        self.assertEqual(mode, "API直出")
        self.assertEqual(len(self.seen), 1, "不该有第二次请求（没做降级）")
        # 关键：这句话不能进她的历史
        joined = " ".join(str(m.get("content") or "")
                          for m in getattr(b, "hist", []))
        self.assertNotIn("rejected", joined,
                         "风控拒答的话被当成她说过的话存进历史了")
        self.assertNotIn("rejected", " ".join(str(x) for x in
                                              getattr(b, "hist", [])))

    def test_normal_reply_still_stored(self):
        """别把这条规则做成"一律不入库" —— 正常回复必须照旧进历史。"""
        def _post(url, headers=None, json=None, timeout=None):
            return _Resp({"choices": [{"message": {"content": "我在呢，眯会儿"}}]})

        B.httpx = type("H", (), {"post": staticmethod(_post)})
        b = self._brain()
        ans, _mode = b.chat("你在干嘛呀", log=False, src="app")
        self.assertEqual(ans, "我在呢，眯会儿")
        joined = " ".join(str(m.get("content") or "")
                          for m in getattr(b, "hist", []))
        self.assertIn("眯会儿", joined, "正常回复没进历史，聊天会断片")


if __name__ == "__main__":
    unittest.main()