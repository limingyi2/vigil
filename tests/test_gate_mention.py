# -*- coding: utf-8 -*-
"""闸门:别接别人被 @ 的话题。

## 为什么单独立一条

群里绝大多数消息不是在跟她说话。原来的判定全靠模型,而提示词第一条写的是
"两个人在对话(一个在 @ 另一个、**另一个在回他**)→ PASS" —— 要求对方**已经回了**。
群里更常见的却是:

    @Echo 你玩不玩          ← @ 了别人,但对方还没回

这句不满足"另一个在回他",于是掉进第四条「剩下的都是 OPEN」,而第四条里正好
写着「提了个问题 → OPEN」。判成 OPEN,她就把**问别人的问题**当成问自己的答了。
实测(2026-10-06 群日志):

    [收到] 懒得起:@Echo 你玩不玩  -> 她:"那是问Echo的,不是我。"
    [收到] 懒得起:@。 你玩不玩     -> 她:"还是问别人的,我可没接这个茬。"
    [收到] 懒得起:@Echo           -> 她:"他们聊他们的,我玩我的。"

她自己清楚不是在问她,但闸门放行了就总得说点什么 —— 群里看着就是牛头不对马嘴。

## 判据分三层

1. `_called_someone_else` 认得对(含 @所有人 / 邮箱 / 同时@她 这些边角)
2. `_max_pass` 读配置、非法值回落
3. **`on_turn_in` 真的拦住了,而且不把这条计进强制放行的连击** ——
   这条最重要:"确定不是在跟她说话"和"拿不准先拦一下"是两回事,
   后者连几次就该强制放行(怕她被关起来),前者是事实,不能被子
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
sys.path.insert(0, os.path.join(ROOT, "brain", "plugins"))

_spec = importlib.util.spec_from_file_location(
    "gate_under_test", os.path.join(ROOT, "brain", "plugins", "gate.py"))
gate = importlib.util.module_from_spec(_spec)
sys.modules["gate_under_test"] = gate
_spec.loader.exec_module(gate)


class _Ctx(object):
    """最小 ctx:闸门只用到 text / src / brain / api_config + veto。"""

    def __init__(self, text, src="wechat"):
        self.text = text
        self.src = src
        self.brain = None
        self.api_config = {}
        self.vetoed = False
        self.why = ""

    def veto(self, why=""):
        self.vetoed = True
        self.why = why


class TestCalledSomeoneElse(unittest.TestCase):
    def setUp(self):
        # 她的名字直接打桩:不依赖 config.json / personas,换机器也一样
        self._real = gate._nicknames
        gate._nicknames = lambda ctx: ["麦麦"]

    def tearDown(self):
        gate._nicknames = self._real

    def _check(self, text, want, why, src="wechat"):
        got = gate._called_someone_else(_Ctx(text, src), text)
        self.assertEqual(got, want, "%r -> %s(%s),期望 %s"
                         % (text, got, why, want))

    def test_在at别人(self):
        for t in ("懒得起:@Echo 你玩不玩",
                  "懒得起:@。 你玩不玩",
                  "懒得起:@Echo",
                  "@阿明 在吗"):
            self._check(t, True, "在 @ 别人")

    def test_at的是她自己(self):
        for t in ("@麦麦 康神开播了", "懒得起:@麦麦", "@麦麦 @Echo 一起玩"):
            self._check(t, False, "@ 的人里有她")

    def test_所有人不算别人(self):
        self._check("@所有人 今天下午开会", False, "@所有人 也包括她")

    def test_没有at就不归它管(self):
        # 既没人 @、也没提她名字 —— 该不该接交给模型判,那才是"像不像真人"
        for t in ("谁在吗", "今天好累啊", "麦麦你玩不玩"):
            self._check(t, False, "没有 @ 别人的动作")

    def test_邮箱里的at不算点名(self):
        # 不能把 a@b.com 当成在叫 b.com
        self._check("我的邮箱 a@b.com 发你了", False, "邮箱不是点名")

    def test_app_渠道也照样判(self):
        # 这里只管"文字里是不是在叫别人",渠道过滤在 on_turn_in 里做。
        # 所以即便 src=app,这个函数本身也该给出同样的答案。
        self._check("@Echo 你看这个", True, "跟渠道无关", src="app")


class TestMaxPass(unittest.TestCase):
    def test_默认是8(self):
        self.assertEqual(gate._max_pass({}), 8)
        self.assertEqual(gate._MAX_CONSECUTIVE_PASS, 8)

    def test_能读配置(self):
        self.assertEqual(gate._max_pass({"max_consecutive_pass": 15}), 15)
        self.assertEqual(gate._max_pass({"max_consecutive_pass": "12"}), 12)

    def test_非法值回落(self):
        # 1 等于"一次不接话就必须开口",比默认还吵 —— 不允许
        for bad in (1, 0, -3, "abc", None, [], {}):
            self.assertEqual(gate._max_pass({"max_consecutive_pass": bad}), 8,
                             "非法值 %r 没回落" % (bad,))


class TestOnTurnInVetoes(unittest.TestCase):
    """真正跑 on_turn_in —— 这是"她到底会不会接别人话题"的那一环。"""

    def setUp(self):
        self._real_nick = gate._nicknames
        self._real_cfg = gate._cfg
        gate._nicknames = lambda ctx: ["麦麦"]
        gate._cfg = lambda: {"enabled": True, "sources": ["wechat"]}
        gate._pass_streak.clear()

    def tearDown(self):
        gate._nicknames = self._real_nick
        gate._cfg = self._real_cfg
        gate._pass_streak.clear()

    def test_at别人会被拦(self):
        c = _Ctx("懒得起:@Echo 你玩不玩")
        gate.on_turn_in(c)
        self.assertTrue(c.vetoed, "@ 的是别人,却没拦下来")
        self.assertIn("别人", c.why)

    def test_拦了也不攒连击(self):
        """🔴 最关键的一条。

        如果这条计进 `_pass_streak`,连来 8 次"@别人"就会触发强制放行,
        她又会去接别人的话题 —— 那正是这次要修的病。
        所以:拦住 + streak 保持 0。

        ⚠️ 连击的键是 `_scope(ctx)` = `"<渠道>.<人设key>"`,**不是** `"wechat"`。
        第一版这里写的是 `_pass_streak.get("wechat", 0)`,而真实键是 `"wechat."`
        (persona 取不到就是空串) —— 于是拿到的永远是默认值 0,
        **这条判据变成了恒真**:故意把 bug 放回去它照样绿。
        所以键必须问 `_scope` 要,不能自己拼。
        """
        key = gate._scope(_Ctx("懒得起:@Echo 你玩不玩"))
        self.assertTrue(key.startswith("wechat"),
                        "作用域键变了,这条判据得跟着改:%r" % key)
        for i in range(12):
            c = _Ctx("懒得起:@Echo 你玩不玩")
            gate.on_turn_in(c)
            self.assertTrue(c.vetoed, "第 %d 次没拦住" % (i + 1))
        self.assertEqual(gate._pass_streak.get(key, 0), 0,
                         "被计进了强制放行的连击 —— 攒够次数她又会来插嘴")

    def test_at她自己时不被这条拦(self):
        c = _Ctx("@麦麦 康神开播了")
        gate.on_turn_in(c)
        self.assertFalse(
            c.vetoed and "别人" in (c.why or ""),
            "被 @ 了反而被「在@别人」拦掉 —— 那她就永远不回了")

    def test_普通消息不受影响(self):
        """没有 @ 的消息不该被这条新判据碰到（交回模型判）。"""
        c = _Ctx("今天好累啊", src="wechat")
        # 模型部分不打桩会真调 API:直接禁掉判定,看是否走到它
        n = {}
        gate._judge = lambda ctx, cfg: (n.setdefault("called", True), (True, "打桩"))[1]
        gate.on_turn_in(c)
        self.assertTrue(n.get("called"), "普通消息没走到模型判定 —— 被误拦了")


if __name__ == "__main__":
    unittest.main()
