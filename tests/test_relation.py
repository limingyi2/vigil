# -*- coding: utf-8 -*-
"""关系定位：不再有两档，改成 persona 里的自由文本字段。

原来写死 friend/partner 二选一，抽出来是为了让人设文件里写"我们是情侣"
能压过 CORE_RULES（它固定缀在最后，权重最高）。但两档枚举永远补不完 ——
"十年老友""正在追她""同居"都不在里头。现在整个概念去掉：
关系就是人设的一个字段，你说多少是多少。

同时验证 CORE_RULES 里保留下来的那两条 —— 原来 friend 和 partner 两个档里
各写了一遍，删档时一起丢了会静默失效，所以特意搬进去了。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "brain"))

import persona_store as ps   # noqa: E402


class TestRelationIsAField(unittest.TestCase):
    """relation 是人设字段，不是 config 里的两档选择"""

    def test_field_is_in_whitelist(self):
        """不在 FIELDS 里的话 App 保存一次就会被静默丢掉。"""
        self.assertIn("relation", ps.FIELDS)

    def test_old_two_tiers_are_gone(self):
        for name in ("RELATION_FRIEND", "RELATION_PARTNER", "RELATIONS"):
            self.assertFalse(hasattr(ps, name), "%s 还留着" % name)

    def test_relation_of_reads_persona_not_config(self):
        import inspect
        src = inspect.getsource(ps.relation_of)
        self.assertIn("persona", src)
        self.assertNotIn("api_config", src)


class TestRelationOf(unittest.TestCase):
    def test_reads_from_persona(self):
        self.assertEqual(ps.relation_of({"relation": "十年老友，各自城市"}),
                         "十年老友，各自城市")

    def test_freetext_is_kept_verbatim(self):
        """写什么留什么 —— 不做归一化、不查白名单。"""
        for v in ("正在追她，还没答应", "同居两年", "她是你妹",
                  "刚认识两周", "随便什么写法都行"):
            self.assertEqual(ps.relation_of({"relation": v}), v)

    def test_empty_falls_back_to_default(self):
        """留空退回默认，不是变成空。

        选这个而不是"真留空"：误操作清空这一栏时有个兜底比没兜底安全 ——
        这跟原来两档"认错时退回 friend"是同一个思路（认错时静默退回最保守的）。
        真不想要约束的人会直接删掉那栏，不会留着空白。
        """
        for p in ({}, {"relation": ""}, {"relation": "   "}, None):
            self.assertEqual(ps.relation_of(p), ps.DEFAULT_RELATION)

    def test_non_string_is_ignored(self):
        """字段被写成数字/列表时别炸，退回默认。"""
        for v in (123, ["a"], {"x": 1}):
            self.assertEqual(ps.relation_of({"relation": v}), ps.DEFAULT_RELATION)


class TestBuildSystemText(unittest.TestCase):
    def _txt(self, **kw):
        p = {"name": "她", "background": "背景", "scene": "场景",
             "personality": "性格"}
        p.update(kw)
        return ps.build_system_text(p)

    def test_relation_appears_in_text(self):
        t = self._txt(relation="你们是十年老友")
        self.assertIn("你们是十年老友", t)
        self.assertIn("【你和他是什么关系】", t)

    def test_no_signature_param(self):
        """关系档位参数删了 —— 关系不再是外部传进来的。

        第二个参数是 src（2026-10-08 加的）：底线按渠道选，PC 客户端走
        助手规则、其余渠道走陪伴规则（见 persona_store.ASSISTANT_SRCS）。
        它有默认值，不传仍按 app 处理 —— 所以老调用方一行都不用改。
        """
        import inspect
        params = inspect.signature(ps.build_system_text).parameters
        self.assertEqual(list(params), ["p", "src"])
        self.assertEqual(params["src"].default, "app")

    def test_empty_relation_still_has_default(self):
        """空值走默认，所以标题和默认文案都在 —— 这跟"真留空"的预期相反，
        是有意选的行为（见 TestRelationOf.test_empty_falls_back_to_default）。"""
        t = self._txt(relation="")
        self.assertIn("【你和他是什么关系】", t)
        self.assertIn(ps.DEFAULT_RELATION, t)

    def test_relation_goes_before_core_rules(self):
        """关系段夹在 notes 和 CORE_RULES 之间：它是"他们是什么关系"这个事实，
        CORE_RULES 是无论什么关系都成立的底线 —— 底线压在最末尾。"""
        t = self._txt(relation="同居两年")
        self.assertLess(t.index("同居两年"), t.index("【你是谁"))

    def test_core_rules_keeps_shared_constraints(self):
        for frag in ("打断他时换个说法", "不当真承诺"):
            self.assertIn(frag, ps.CORE_RULES)

    def test_core_rules_has_no_tier_specific_text(self):
        """档位专属的那些不能留在 CORE_RULES，否则又变成焊死的。"""
        for frag in ("不是恋人", "不搞暧昧", "在谈的恋人"):
            self.assertNotIn(frag, ps.CORE_RULES)


def _tracked_persona_files():
    """**入库**的 personas/*.json（相对仓库根的路径）。

    不能扫工作目录：作者本机放着好几份本地实验用的人设（真实角色、各种
    preset），它们**都没有入库**。扫目录就会把这些当成"仓库里预置了角色"
    而报红 —— 本机红、CI 绿，正好反了，而且会把人往"隐私泄露了"的方向带。
    这个类的本意是"仓库不预置角色"，那就只能看入库清单。
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run(["git", "ls-files", "brain/config/personas"],
                           cwd=root, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except Exception:
        return []
    return [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]


class TestPersonaFiles(unittest.TestCase):
    """预设已删，只留当前这套 + 空模板"""

    def setUp(self):
        self.d = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "brain", "config", "personas")

    def test_no_presets(self):
        tracked = _tracked_persona_files()
        if not tracked:
            self.skipTest("不在 git 工作区（或没有 git），看不到入库清单")
        left = [os.path.basename(x) for x in tracked
                if os.path.basename(x).startswith("preset_")]
        self.assertEqual(left, [], "预设还入库着：%s" % left)

    def test_template_has_relation_field(self):
        import json
        d = json.load(open(os.path.join(self.d, "default.json"),
                           encoding="utf-8"))
        self.assertIn("relation", d)


class TestApiSurface(unittest.TestCase):
    """App 侧：关系档接口没了，换成人设编辑接口"""

    def setUp(self):
        self.src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "brain", "server.py"),
                        encoding="utf-8").read()

    def test_relation_apply_endpoint_removed(self):
        self.assertNotIn("/api/relation/apply", self.src)

    def test_persona_editor_endpoints_exist(self):
        self.assertIn('"/api/persona/full"', self.src)
        self.assertIn('"/api/persona/save"', self.src)

    def test_full_is_in_get_branch(self):
        """编辑器是用 GET 读人设的（只读）。接口写在 POST 分支里就404。

        这条踩过：接口写对了、内容也部署上去了，但前端GET 就是 404 ——
        查文件在、行号对、语法没问题，最后才发现它挂在 POST 那条链上。
        """
        i = self.src.index('if path == "/api/persona/full"')
        # 往前找最近的分支判断：GET 分支用 "if path =="，POST 用 "elif"
        head = self.src[max(0, i - 1200):i]
        last_if = head.rfind("if path ==")
        last_elif = head.rfind("elif path ==")
        self.assertGreater(self.src.rfind("def do_GET", 0, i), self.src.rfind("def do_POST", 0, i),
                           "persona/full 挂在 elif（POST）链上，GET 会404")

    def test_save_validates_name(self):
        """空名字不能存 —— 否则她连自己叫什么都不知道。"""
        i = self.src.index('"/api/persona/save"')
        self.assertIn("总得有个名字", self.src[i:i + 2000])

    def test_full_endpoint_guards_path_traversal(self):
        """key 来自查询串，必须 basename + isfile 双重校验。"""
        i = self.src.index('if path == "/api/persona/full"')
        seg = self.src[i:i + 1600]
        self.assertIn("os.path.basename", seg)
        self.assertIn("isfile", seg)

    def test_brain_no_longer_passes_relation(self):
        b = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "brain", "brain.py"), encoding="utf-8").read()
        self.assertNotIn("self.relation", b)
        self.assertNotIn("relation_of", b)


class TestFrontendEditor(unittest.TestCase):
    """App 里有人设编辑器了"""

    def setUp(self):
        # 读**入库**的那份（assets/chat.html）。
        # 原来读根目录 _chat.html —— 那是打包母本，被 .gitignore 挡着，
        # 任何克隆和 CI 上都不存在，于是这个类必然 FileNotFoundError（8 个测试
        # 全变 error），本机看着绿只是因为作者手上有那个文件。
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "android-app", "app", "src", "main",
                         "js", "pages.js")
        if not os.path.isfile(p):
            self.skipTest("assets/chat.html 不在")
        with open(p, encoding="utf-8") as f:
            self.src = f.read()

    def test_no_relation_tier_ui(self):
        for frag in ("relationList", "relationCur", "applyRelation",
                     "RELATION_LABELS"):
            self.assertNotIn(frag, self.src, frag)

    def test_editor_has_all_fields(self):
        # notes 现在**没有独立输入框**了：改成"粘一整份角色卡"那个框（pf_raw）
        # 来填，保存时整段写回 out.notes。所以这里查的是"每个字段都有出处"，
        # 而不是"每个字段都有一个 k: 'xxx'"。
        for k in ("name", "nicknames", "call_user", "relation", "background",
                  "scene", "personality", "appearance"):
            self.assertIn("k: '%s'" % k, self.src)
        # notes 的出处：整份粘贴框，保存时写回
        self.assertIn("pf_raw", self.src)
        self.assertIn("out.notes = ", self.src)

    def test_editor_calls_new_endpoints(self):
        self.assertIn("/api/persona/save", self.src)
        self.assertIn("/api/persona/full", self.src)


class TestSaveKeepsUnsentFields(unittest.TestCase):
    """App 编辑器只发 9 个字段，save 不能把其余的清掉。

    label/desc 是列表元数据，编辑器里没有这两栏（用户不该看见它们），
    所以前端永远不会传。原先 save 一律 data.get(k, DEFAULT_PERSONA[k]) 兜底，
    在 App 里保存一次就把它们清成空串，列表里的副标题跟着没了。
    """

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.orig = ps.PERSONA_DIR
        ps.PERSONA_DIR = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ps, "PERSONA_DIR", self.orig)

    def test_label_desc_survive_a_save(self):
        ps.save({"name": "她", "label": "原版 · 老朋友",
                 "desc": "顺手跟你说两句"}, "k")
        ps.save({"name": "她", "relation": "同居两年"}, "k")
        import json
        d = json.load(open(ps.path_of("k"), encoding="utf-8"))
        self.assertEqual(d["label"], "原版 · 老朋友")
        self.assertEqual(d["desc"], "顺手跟你说两句")
        self.assertEqual(d["relation"], "同居两年")

    def test_empty_string_still_clears(self):
        """传了空串是用户主动清空，要照写 —— 跟"没传"不是一回事。"""
        ps.save({"name": "她", "label": "有值"}, "k")
        ps.save({"name": "她", "label": ""}, "k")
        import json
        d = json.load(open(ps.path_of("k"), encoding="utf-8"))
        self.assertEqual(d["label"], "")

    def test_missing_file_falls_back_to_defaults(self):
        """文件不存在时不能炸，缺的字段用默认。"""
        ps.save({"name": "她"}, "new")
        import json
        d = json.load(open(ps.path_of("new"), encoding="utf-8"))
        self.assertEqual(d["name"], "她")
        self.assertIn("relation", d)


class TestDraftRulesNotBindingRelation(unittest.TestCase):
    """原来这里钉的是"规则别把人设绑死"（不许写死异地/同城、不许许诺见面）。

    2026-10-08 起约束整体移进人设文件，`build_draft_rules` 恒返回空串 ——
    所以这两类绑死在代码里的约束**整个不存在了**，也就无从"绑"。
    这条现在钉的是新现实：规则为空，且这些关系判断只可能来自人设。
    """

    def setUp(self):
        import persona_store
        self.rules = persona_store.build_draft_rules({})

    def test_rules_are_gone(self):
        self.assertEqual(self.rules, "",
                         "代码里又开始拼关系约束了；该写进人设文件")

    def test_no_hardcoded_distance_or_meeting_promise(self):
        """异地/同城这类判断不该由代码决定，更不该出现在任何拼接文本里。"""
        for frag in ("重要：异地", "我马上过来", "假装看得见他身边"):
            self.assertNotIn(frag, self.rules)


class TestAssistantChannel(unittest.TestCase):
    """PC 客户端（src=pc）走助手底线，其余渠道仍是陪伴底线。

    2026-10-08 加的。为什么要分渠道：CORE_RULES 第一句就是"你是一个具体的
    人，不是助手"，而且它固定缀在提示词**最末尾**（权重最高）。人设文件里
    写"你是 AI 助手"压不过它 —— 不换底线的话，人设写了、行为没变。
    """

    def _txt(self, src):
        return ps.build_system_text({"name": "Vigil", "relation": "搭档"},
                                    src=src)

    def test_pc_gets_the_natural_agent_rules(self):
        t = self._txt("pc")
        self.assertTrue(t.endswith(ps.ASSISTANT_RULES))
        self.assertIn("自然、友好、专业的 AI Agent", t)
        self.assertIn("回应寒暄", t)
        self.assertIn("讨论想法", t)
        self.assertIn("严谨地读代码", t)

    def test_pc_does_not_get_the_companion_rules(self):
        """陪伴底线与助手底线互斥 —— 两套同时在场等于没指定身份。

        断言这两句（身份定义 + 段标题），别用"不是谁的情绪出口"那种
        两边措辞相同的句子 —— 助手版本来也要说"不提供情绪价值"。
        """
        t = self._txt("pc")
        self.assertNotIn("你是一个具体的人", t)
        self.assertNotIn("你有不想聊的时候", t)
        self.assertNotIn("【你是谁（优先级最高", t)

    def test_pc_has_no_relation_block(self):
        """助手渠道不写"你和他是什么关系"。

        那套问法是她扮演某个人的伴侣时用的；助手是干活的，加这一段
        等于把她往「女朋友」上带（人设文件的字段就是这么写的）。
        """
        self.assertNotIn("【你和他是什么关系】", self._txt("pc"))

    def test_assistant_rules_preserve_truthfulness_and_safety(self):
        """PC Agent 保留诚实、权限和工具安全规则。"""
        self.assertIn("直接、如实地回答", ps.ASSISTANT_RULES)
        self.assertIn("遵守权限和工具安全要求", ps.ASSISTANT_RULES)
        self.assertIn("不编造事实", ps.ASSISTANT_RULES)
        self.assertNotIn("你不知道自己是程序", ps.ASSISTANT_RULES)

    def test_pc_rules_allow_normal_conversation(self):
        t = self._txt("pc")
        for phrase in ("不寒暄", "不陪聊", "不提供情绪", "不用接话"):
            self.assertNotIn(phrase, t)

    def test_other_channels_keep_the_companion_rules(self):
        """换渠道不能把手机/微信那边也变成助手。"""
        for src in ("app", "wechat"):
            t = self._txt(src)
            self.assertIn("你是一个具体的人", t)
            self.assertIn("【你和他是什么关系】", t)
            self.assertNotIn("你是 AI 助手", t)

    def test_pc_is_the_only_assistant_channel(self):
        """渠道名单是显式的 —— 加渠道要改这里，别让别处悄悄走错底线。"""
        self.assertEqual(ps.ASSISTANT_SRCS, ("pc",))

    def test_the_two_rule_sets_do_not_overlap(self):
        """两套底线不许共享句子：共享的部分会变成"两套身份都成立"。"""
        a = set(l.strip() for l in ps.CORE_RULES.splitlines() if l.strip())
        b = set(l.strip() for l in ps.ASSISTANT_RULES.splitlines() if l.strip())
        self.assertEqual(a & b, set())


if __name__ == "__main__":
    unittest.main()
