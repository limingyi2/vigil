# -*- coding: utf-8 -*-
"""/api/persona/save 的热重载判据必须按渠道算。

这里防的是一个已经上线过、且**症状极像成功**的 bug：

    if key == persona_store.key_from_config(api_config):
        brain.reload_persona()

`key_from_config` 读的是**全局**那份（= App 的 key），不看 persona_by_src。
于是编辑微信人设时 key 对不上，被判成"你在编辑别的模板"而**故意**不重载
（那段守卫的本意是对的）—— 但接口回 ok:true、界面弹"她马上就是这个设定"，
**只有她不换**。实测：App 存完 prompt 人设段 4549→4672，微信 2113→2113。

判据不启动 HTTP、不碰云端：直接把那段判据的算法抽出来喂各种组合。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

import server  # noqa: E402


class _Cfg(object):
    """模拟 config.json：全局 persona_file + persona_by_src。"""

    def __init__(self, glob, by_src):
        self.glob = glob
        self.by_src = by_src


def _should_reload(cfg, saved_key, src):
    """复刻 server.py 里 /api/persona/save 的判据。

    改这份测试时**必须**同步看 server.py 那段；两处不一样的话，
    这份测试保护的就不是线上跑的那份逻辑了。

    _persona_for_src 会去读真的 config.json，所以这里把它打桩 ——
    不打桩的话测试结果取决于开发机上谁配过什么（本人就踩过：
    本机 config 里有 persona_by_src.wechat，于是一条判据莫名失败）。
    """
    src = server._norm_src(src)
    real = server._persona_for_src
    server._persona_for_src = lambda s, default_key="": (
        str(cfg.by_src.get(s) or "").strip() or str(default_key or ""))
    try:
        cur = server._persona_for_src(src, cfg.glob)
    finally:
        server._persona_for_src = real
    return saved_key == cur


class TestPersonaSaveReloadJudgement(unittest.TestCase):
    # 全局（= pc 桌面端/无覆盖渠道）用 preset_group；微信渠道覆盖成 maibot。
    CFG = _Cfg("preset_group", {"wechat": "preset_group_maibot"})

    def test_editing_the_channels_own_persona_reloads(self):
        # pc 吃全局那份；wechat 吃 persona_by_src.wechat 那份。
        self.assertTrue(_should_reload(self.CFG, "preset_group", "pc"))
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", "wechat"))

    def test_editing_another_templates_does_not_reload(self):
        """守卫的本意要保住：在面板里改一个**没在用**的模板，不该动她。"""
        self.assertFalse(_should_reload(self.CFG, "preset_group_maibot", "pc"))
        self.assertFalse(_should_reload(self.CFG, "preset_group", "wechat"))

    def test_missing_src_defaults_to_wechat(self):
        """不带 src / 空白 → 归一到主渠道 wechat（app 已退役）。"""
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", None))
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", ""))
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", "  "))

    def test_unknown_channel_falls_back_to_wechat(self):
        """传个认不出来的渠道不能变成"哪个都不匹配"（那等于静默不重载）。"""
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot",
                                       "../../evil"))
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", "APP"))

    def test_case_and_padding_are_normalised(self):
        self.assertTrue(_should_reload(self.CFG, "preset_group_maibot", " WeChat "))

    def test_channel_without_its_own_persona_uses_the_global_one(self):
        cfg = _Cfg("base", {})
        self.assertTrue(_should_reload(cfg, "base", "wechat"))
        self.assertFalse(_should_reload(cfg, "base_other", "wechat"))


class TestSaveHandlerReadsSrc(unittest.TestCase):
    """源码级判据：确认 handler 真的用了 _norm_src / _persona_for_src。

    纯逻辑测试保护不了"handler 忘了把 src 传进来"这种改法 ——
    那会让上面整组判据全部通过，而线上依然是坏的。
    """
    @classmethod
    def setUpClass(cls):
        import inspect
        src = inspect.getsource(server)
        i = src.find('elif path == "/api/persona/save":')
        cls.body = src[i:i + 2600] if i >= 0 else ""

    def setUp(self):
        self.assertNotEqual(self.body, "", "没找到 /api/persona/save 这段")

    def test_handler_normalises_src(self):
        self.assertIn("_norm_src", self.body)

    def test_handler_computes_the_channels_own_key(self):
        self.assertIn("_persona_for_src", self.body)

    def test_reload_call_is_conditional_on_that_key(self):
        """判据必须用渠道那套 key，不能退回 key_from_config。"""
        self.assertNotIn("key_from_config(api_config):",
                         self.body.split("# 只有改的是")[0].split("key =")[1]
                         if "key =" in self.body else "")

    def test_response_tells_the_caller_whether_it_reloaded(self):
        """回包里带 reloaded —— 不然调用方只能靠猜。"""
        self.assertIn('"reloaded"', self.body)

    def test_apply_handler_also_takes_src(self):
        """切模板那条早就修了，别哪天又退回去。"""
        import inspect
        src = inspect.getsource(server)
        i = src.find('elif path == "/api/persona/apply":')
        self.assertNotEqual(i, -1)
        self.assertIn("_norm_src", src[i:i + 2000])


if __name__ == "__main__":
    unittest.main(verbosity=2)