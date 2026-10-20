# -*- coding: utf-8 -*-
"""C1 / C2 / C3 的接线护栏。改坏了立刻红，不需要开浏览器。

为什么用源码断言而不只靠 e2e：这三条修的都是"文案分类型 / 消费返回值 /
持久判据"，e2e 要真跑一遍 Electron + Brain + Host（分钟级），而这里钉的是
**不许退回去的形状**：

  C1  toast 文案按操作类型分（插件→重启内核，技能→重启技能宿主）；
  C2  卸载回调必须消费 restart_needed，不能把"文件删了"显示成"运行态也停了"；
  C3  界面的"待重启"来自服务端磁盘判据（changed_at > started_at），
      不是 toast 记忆、不是前端变量 —— 所以刷新后还在，也不可能虚假消解。

判据本身的行为由 brain/tests/test_restart_state.py 覆盖（真跑时间戳），
这里只负责"两端有没有接上"。
"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "client", "src", "js", "pages.js")
SERVER = os.path.join(ROOT, "brain", "server.py")
SKILL = os.path.join(ROOT, "brain", "plugins", "skill.py")
HOST = os.path.join(ROOT, "brain", "skill_host", "server.js")
PLUGIN_OPS = os.path.join(ROOT, "brain", "plugin_ops.py")
RESTART_STATE = os.path.join(ROOT, "brain", "restart_state.py")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


class C1ToastWordingByKind(unittest.TestCase):
    """Skill 的提示不许再被叫成"插件"。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(PAGES)

    def test_install_zip_takes_a_kind_argument(self):
        self.assertIn("async function installZip(file, url, body, kind)",
                      self.src, "installZip 少了 kind 参数 —— 它没法区分装的是插件还是技能")

    def test_wording_map_has_both_kinds(self):
        self.assertIn("const RN = {", self.src)
        self.assertIn("plugin:", self.src)
        self.assertIn("skill:", self.src)

    def test_plugin_and_skill_wordings_differ(self):
        """同一句话两边复用就是 C1 本身。"""
        i = self.src.index("const RN = {")
        block = self.src[i:i + 400]
        self.assertIn("重启内核", block)
        self.assertIn("重启技能宿主", block)

    def test_plugin_call_passes_plugin_kind(self):
        self.assertIn("installZip(f, '/api/plugins/install', { source: '客户端' }, 'plugin')",
                      self.src)

    def test_skill_call_passes_skill_kind(self):
        self.assertIn("installZip(f, '/api/skill/install', { note: '客户端' }, 'skill')",
                      self.src)

    def test_old_single_wording_is_not_hardcoded_anymore(self):
        """原来那句被所有类型共用；现在它只能出现在 plugin 分支里。"""
        n = self.src.count("有后台线程的插件要重启内核才完全生效")
        self.assertEqual(n, 1, "这句文案应当只属于插件分支，出现 %d 处" % n)


class C2UninstallConsumesRestartNeeded(unittest.TestCase):
    """卸载成功 != 运行态已经停了。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(PAGES)

    def test_plugin_uninstall_reads_restart_needed(self):
        i = self.src.index('data-un="')
        block = self.src[i:i + 1400]
        self.assertIn("rr.restart_needed", block,
                      "插件卸载没有消费 restart_needed —— 后台线程还在跑，界面却说卸完了")

    def test_skill_uninstall_reads_restart_needed(self):
        i = self.src.index("data-sun")
        block = self.src[i:i + 1400]
        self.assertIn("rr.restart_needed", block,
                      "技能卸载没有消费 restart_needed")

    def test_uninstall_failure_is_reported(self):
        """以前卸载是 await 完就重画，失败也看着像成功。"""
        i = self.src.index('data-un="')
        block = self.src[i:i + 1400]
        self.assertIn("!rr.ok", block)
        self.assertIn("卸载失败", block)

    def test_uninstall_prompt_says_what_is_still_running(self):
        i = self.src.index('data-un="')
        block = self.src[i:i + 1400]
        self.assertIn("后台线程还在跑", block)


class C3PendingStateHasAReliableSource(unittest.TestCase):
    """界面上的"待重启"必须来自服务端判据，且刷新后还在。"""

    @classmethod
    def setUpClass(cls):
        cls.pages = _read(PAGES)
        cls.server = _read(SERVER)

    def test_pages_reads_the_server_field(self):
        self.assertIn("r.pending_restart", self.pages,
                      "插件页没有读服务端的 pending_restart —— 提示就没有可靠来源")

    def test_pages_does_not_invent_the_state_itself(self):
        """待重启判据不许在前端算：那正是 C3 要消灭的东西。

        只查 pending_restart 周边那段 —— Date.now() 在别处（时间显示）
        是正当用途，一刀切会误伤。
        """
        for anchor in ("r.pending_restart", "skPend = r.pending_restart"):
            if anchor not in self.pages:
                continue
            i = self.pages.index(anchor)
            block = self.pages[max(0, i - 500):i + 1200]
            # 待重启判据附近不许出现本地时间计算，也不许落到浏览器存储里
            # （那正是"刷新即丢"的旧行为）。localStorage 在别处的正当用途
            # 不在一刀切范围内。
            self.assertNotIn("Date.now()", block,
                             "待重启判据附近出现了本地时间计算")
            for bad in ("localStorage", "sessionStorage"):
                self.assertNotIn(bad, block,
                                 "待重启状态落在 %s 里就刷新即丢" % bad)

    def test_both_pages_have_the_banner(self):
        self.assertEqual(self.pages.count("有改动还没生效"), 2,
                         "插件页和技能页都该有这条持久提示")

    def test_banner_names_the_right_restart_target(self):
        self.assertIn("待重启内核", self.pages)
        self.assertIn("待重启技能宿主", self.pages)

    def test_plugins_get_returns_the_judgement(self):
        i = self.server.index('if path == "/api/plugins":')
        self.assertIn("pending_restart", self.server[i:i + 1200])

    def test_uninstall_restart_needed_comes_from_the_uninstalled_one_only(self):
        """卸载的 restart_needed 只准是"刚卸掉的那个"的信号。

        曾经把重载返回的"剩下的插件里有带的"也 OR 进来。真实 UI 验收
        （harness-p4-c123-e2e.js C2-4）实测：卸一个没有 start 钩子的插件，
        内置插件（reminder 等）照样让 need=True，界面于是说"它注册的后台
        线程还在跑"—— 被卸的插件根本没有线程，这句话是谎话。重载刚把内存
        重拼到和磁盘一致，其余插件的线程本来就是当前代码在跑，不需要为
        它们重启，所以第二个来源必须去掉。
        """
        i = self.server.index('if path == "/api/plugins/uninstall":')
        block = self.server[i:i + 900]
        self.assertIn('res.get("restart_needed")', block,
                      "卸载没消费 plugin_ops 返回的 restart_needed")
        self.assertNotIn('bool(res.get("restart_needed")) or bool(need)', block,
                         "又把重载的 need OR 进来了 —— 没有 start 钩子的插件"
                         "会被谎称后台线程还在跑（C2-4 实测回归）")

    def test_skill_status_returns_the_judgement(self):
        self.assertIn('d["pending_restart"]', _read(SKILL))

    def test_brain_records_changes_on_both_paths(self):
        """装和卸两条路都要记，漏一条那条路上刷新后就看不到提示。"""
        self.assertIn("note_change", _read(PLUGIN_OPS))
        self.assertIn("note_change", _read(SKILL))

    def test_host_registers_its_own_start_time(self):
        """判据的另一端：宿主启动时写 started_at，否则永远显示待重启。"""
        host = _read(HOST)
        self.assertIn("function noteStartedAt()", host)
        i = host.index("loadAll({ allowPartial: true })")
        self.assertIn("noteStartedAt()", host[i:i + 300],
                      "noteStartedAt 没在 listen 成功回调里调 —— 启动时刻登记不上")

    def test_judgement_is_a_timestamp_comparison(self):
        """钉死判据本身：时间戳比较，不是"打勾再取消"。"""
        src = _read(RESTART_STATE)
        self.assertIn('d["changed_at"] > d["started_at"]', src)
        self.assertIn("note_start", src)

    def test_start_time_is_registered_after_the_bind(self):
        """启动时刻必须登记在 HTTP 绑定成功之后。

        曾经登记在 run_server 开头。真实 UI 验收（C3-7b/c）实测复现了后果：
        端口被占时 run_server 只打印"没起来"然后继续跑收尾循环、进程并不退出，
        started_at 被这次失败的重启推新，"重启失败"反而把用户真正待重启的
        提示悄悄抹掉 —— 界面看着像重启成功了。所以登记点只能在 srv 构造
        成功之后；重载/装卸都在服务起来之后，不会漏登记。
        """
        srv = _read(SERVER)
        i = srv.index("def start_remote_api(")
        j = srv.index("def run_server():")
        block = srv[i:j]
        k = block.index("ThreadingHTTPServer(")
        self.assertIn('note_start("plugin"', block[k:],
                      "note_start 没在绑定成功之后调用 —— 重启失败会虚假清除待重启提示")
        # 反向护栏：run_server 在调到 start_remote_api 之前不许再登记一遍
        tail = srv[j:]
        call_at = tail.index("start_remote_api(")
        self.assertNotIn('note_start("plugin"', tail[:call_at],
                         "run_server 在绑定之前又登记了一遍启动时刻")


if __name__ == "__main__":
    unittest.main()
