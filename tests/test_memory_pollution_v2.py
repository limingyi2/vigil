# -*- coding: utf-8 -*-
"""记忆污染的三类新残渣 + 反向不误伤。

## 为什么要补

云端 `base.app` 那个分区里 62 条，**全部是真实进过库的样本**（不是编的用例）。
拿现有 `_memory_should_skip` 跑一遍，只拦下 13 条，剩下 50 条里混着：

    使用 daily-fortune__today 技能工具抽取今日运势← 技能调用执行说明
    20分钟后提醒用户                ← 提醒工具的祈使句
    从网上随便存一张新的趣图                    ← 图片工具的执行说明
    使用 daily-fortune__toda            ← 同上（还被截断了）

所以判据照着真实样本写，不照着想象写。

## 三条判据各自的"看起来像但要放过"

写这类规则最容易出的错是**杀过头** —— 「提醒」「使用」「一张图」都是
正常句子里的高频词。所以每条规则都配了必须放行的反例：

- 提醒祈使 vs 「明天上午10点提醒起床学习」（真安排）
- 提醒祈使 vs 「去图书馆前提醒用户带充电宝」（真约定）
- 图片工具 vs 「用户发送了一张手画卡片…」（真事）
- 技能调用 vs 「使用技能表里那个运势」（只按「技能」二字判就会误杀这条）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

import memory_store_v2 as M   # noqa: E402


class TestRealPollutionSamplesMustBeBlocked(unittest.TestCase):
    """真实进过库的污染样本，一条都不许再放行。"""

    CASES = [
        ("使用 daily-fortune__today 技能工具抽取今日运势", "技能调用"),
        ("使用 daily-fortune__toda", "技能调用"),
        ("调用 skill__today", "技能调用"),
        ("20分钟后提醒用户", "提醒指令"),
        ("30分钟后通知他", "提醒指令"),
        ("2小时后告诉用户", "提醒指令"),
        ("从网上随便存一张新的趣图", "图片工具"),
        ("在网上找一张新的图片", "图片工具"),
    ]

    def test_each_sample_is_blocked_with_the_right_reason(self):
        for text, why in self.CASES:
            got = M._memory_should_skip(text)
            self.assertEqual(got, why, "%r -> %r（期望 %r）" % (text, got, why))

    def test_lead_noise_does_not_hide_them(self):
        """「帮我使用 xxx__yyy」也要拦住 —— 抽取出来的话常带这类前缀。"""
        for text in ["帮我使用 skill__today",
                     "麻烦调用 skill__today",
                     "顺便使用 skill__today 抽取"]:
            self.assertEqual(M._memory_should_skip(text), "技能调用", text)


class TestRealFactsMustNotBeBlocked(unittest.TestCase):
    """你的真实事实，一条都不许被误伤。全部取自那 62 条里的真条目。"""

    FACTS = [
        "明天下午三点去图书馆复习高数",
        "去图书馆时记得带充电宝",
        "去图书馆前提醒用户带充电宝",      # 含「提醒用户」
        "明天上午10点提醒起床学习",        # 含「提醒」
        "晚上一起吃饭（待确认）",
        "舍友习惯：舍友在天气较冷时仍开空调",
        "失眠睡不着",
        "过两天考试周开始",
        "体重状态：用户认为自己胖了",
        "近期需要上早八课",
        "计划去吃烤肉自助",
        "中秋与国庆假期连放，总共13天",
        "25号放假",
        "用户已订好酒店",
        "一个多月后要去实习，需尽快投递简历并准备面试",
        "今天学习了Python一天",
        "正在学习编程中的if语句",
        "用户发送了一张手画卡片，上面画有蓝发动漫女孩",   # 含「一张」
        "今天摸鱼被老板抓到",
        "暗号：ZXA7",
        "宿舍：302宿舍",
        "宠物：养了一只叫糖包的橘猫",
        "习惯：有午睡习惯，目的是让下午学习更高效",
        "喜好：喜欢猫（喵的那种）",
        "国庆期间与用户见面",
        "出行时用户会去接对方",
        "使用技能表里那个运势",              # 只按「技能」二字判就会误杀
    ]

    def test_none_of_them_is_blocked(self):
        bad = []
        for t in self.FACTS:
            why = M._memory_should_skip(t)
            if why:
                bad.append((t, why))
        self.assertEqual(bad, [], "误伤了：%r" % bad)


class TestEarlierRulesStillWork(unittest.TestCase):
    """补规则不能把原来拦住的放回来。"""

    CASES = [
        ("__scope_init__", "内部标记"),
        ("中通快递单号79144766431485已送达", "快递单号"),
        ("查询英伟达最新股价", "工具调用指令"),
        ("查一下英伟达最新股价", "工具调用指令"),
        ("搜索DeepSeek最新模型", "工具调用指令"),
        ("查询杭州的天气", "工具调用指令"),
        ("用户想了解Python 3.14的最新特性，需要联网搜索", "执行说明"),
        ("顺便查询一下汇率", "工具调用指令"),
    ]

    def test_still_blocked(self):
        for text, why in self.CASES:
            self.assertEqual(M._memory_should_skip(text), why, text)

    def test_completed_actions_are_still_kept(self):
        """「已查到X」是结果，值得记 —— 别把它一起拦了。"""
        for t in ["用户想了解DeepSeek最新模型，已查到V4.1 Flash和V4 Pro正式版",
                  "查询记录显示快递已签收"]:
            self.assertIsNone(M._memory_should_skip(t), t)


class TestDecisionsAreNotAccidentallyAllBlocked(unittest.TestCase):
    """判据自检：先验过滤器真的在干活，而不是"全部拦下"也算通过。"""

    def test_trivial_inputs(self):
        self.assertEqual(M._memory_should_skip(""), "空")
        self.assertIsNone(M._memory_should_skip("随便一句话"))

    def test_the_real_62_split_into_both_buckets(self):
        """真样本集上，拦下的和放行的**都**得非空。

        只报"拦下 N 条"看不出对错 —— 全拦也对、全放也对。
        """
        raw = (
            "明天下午三点去图书馆复习高数|去图书馆时记得带充电宝|晚上一起吃饭（待确认）|"
            "失眠睡不着|过两天考试周开始|近期需要上早八课|计划去吃烤肉自助|"
            "25号放假|用户已订好酒店|今天学习了Python一天|正在学习编程中的if语句|"
            "今天摸鱼被老板抓到|暗号：ZXA7|宿舍：302宿舍|宠物：养了一只叫糖包的橘猫|"
            "查询英伟达最新股价|查一下英伟达最新股价|搜索DeepSeek最新模型|"
            "查询杭州的天气|__scope_init__|"
            "使用 daily-fortune__today 技能工具抽取今日运势|"
            "20分钟后提醒用户|从网上随便存一张新的趣图"
        )
        items = raw.split("|")
        blocked = [t for t in items if M._memory_should_skip(t)]
        passed = [t for t in items if not M._memory_should_skip(t)]
        self.assertEqual(len(blocked) + len(passed), len(items))
        self.assertGreater(len(blocked), 0)
        self.assertGreater(len(passed), 0)
        # 三条新规则各自都得在这个真实集合上至少抓到一条
        reasons = set(M._memory_should_skip(t) for t in items)
        for need in ("技能调用", "提醒指令", "图片工具"):
            self.assertIn(need, reasons, "%s 没抓到任何真实样本" % need)


class TestStripLeadNoise(unittest.TestCase):
    def test_strips_stacked_prefixes(self):
        self.assertEqual(M._strip_lead_noise("帮我顺便查"), "查")

    def test_leaves_normal_text_alone(self):
        """不在剥离列表里的开头必须原样保留。

        注意： 「我想」**在**剥离列表里（老代码就有，不是这次加的）——
        别把它写进"必须原样保留"的用例，那测的是一个不存在的行为。
        """
        for t in ["她说了一句", "图书馆在", "今天摸鱼被老板抓到",
                  "喜欢猫", "国庆期间与用户见面"]:
            self.assertEqual(M._strip_lead_noise(t), t, t)

    def test_documented_prefixes_are_stripped(self):
        """剥离列表里的确实会被剥 —— 这是老行为，钉住免得被无声改掉。"""
        for nz in M._MEM_LEAD_NOISE:
            self.assertEqual(M._strip_lead_noise(nz + "查一下"), "查一下", nz)


if __name__ == "__main__":
    unittest.main(verbosity=2)