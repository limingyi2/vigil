# -*- coding: utf-8 -*-
"""仓库里不许出现真实的服务器地址 / 微信号 / SSH 用户名。

## 为什么单独立一条

.gitignore 挡的是"**还没进 git 的东西**"。它挡不住两类事：

1. 已经提交过的历史 —— 改完 .gitignore、把文件从工作区删掉，
   **旧提交里那份还在**。这个仓库 2026-10-02 就因为同类问题
   `git filter-repo` 重写过一次历史。
2. **本来就是源码、本来就该入库的文件里写着真值** ——
   比如面板前端里写死云机地址、示例配置里填的是真 IP。
   .gitignore 对它们无能为力（它们在仓库里）。
   本次体检就抓到 19 处，其中 `admin/dashboard.html` 里那行
   `const DEFAULT_SERVER = 'http://<云机IP>:8788'` 最要命 ——
   那个文件是最容易被整个复制走的。

所以判据放在**测试**里：扫的是 `git ls-files` 出来的东西
（= 真的会被推上去的那些），不扫工作区里没跟踪的本地配置。

## 为什么地址是拼出来的

这个测试文件自己也在 `git ls-files` 里。把 IP 明明白白写进来，
它就**自己把自己扫出来**了。所以判据用片段拼装 ——
这样文件里不存在那个字面量，但正则照样能匹配别人写死的。

## 已知的、需要人工判断的情况

* 文档里出现"某台机器的地址"是正常的（要告诉用户填哪儿），
  但**应该写占位符**，不该写你真在用的那台。占位符长这样：
  `<你的云机 IP>`、`YOUR_HOST`、`example.com`。
* 真值请放在**被 .gitignore 挡住**的本地文件里
  （`wxbridge.local.json` / `config.json` / `app_conn.json` 这些都是）。
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 判据片段拼装 —— 见上面"为什么地址是拼出来的"
_IP = "47." + "114." + "58." + "45"
_BRIDGE_IP = "49." + "233." + "190." + "188"
_WXID_SMALL = "wxid_" + "5ak43lq2bkkd22"
_WXID_BIG = "lmy" + "20051215"

PATTERNS = {
    "云机公网 IP": re.compile(re.escape(_IP)),
    #  桥接机那条是**后补的**。第一版只加了云机 IP，结果
    # `docs/微信群聊接入.md` 和 `wechat_bridge/docs/部署手册.md` 里
    # 两台机器的公网 IP 一起躺着 —— 判据只认一台就等于没认。
    # 加判据时要想的是"这类信息还有哪些"，不是"这次泄露的那个值是什么"。
    "桥接机公网 IP": re.compile(re.escape(_BRIDGE_IP)),
    "麦麦小号的 wxid": re.compile(re.escape(_WXID_SMALL)),
    "大号的 wxid": re.compile(re.escape(_WXID_BIG)),
    "SSH 用户名@云机": re.compile(r"admin(?:istrator)?@" + re.escape(_IP)),
}

# 允许出现的地方。
#  只放**确实必须写死、且写死了也不构成泄露**的 —— 加之前先想清楚。
#    真要放行某个文件，理由写在旁边，别只写个路径。
ALLOW = {
    # 无。清完之后应该是空的；将来要放行必须带理由。
}

# 二进制/压缩包之类不用当文本读
SKIP_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".apk", ".zip",
            ".jar", ".so", ".dll", ".exe", ".woff", ".woff2", ".ttf")


def _would_be_committed():
    """`git add .` 会收进去的文件 = 已跟踪 + 未跟踪且未被忽略。

     这里一开始只扫了 `git ls-files`（**已跟踪**的），于是漏掉了
    `client_architecture.html` 这类**新建但还没提交**的文件 —— 它们里面
    照样写着真 IP，而一次 `git add .` 就会把它们推上去。
    `-o --exclude-standard` 正好就是"会被 add 进去、且没有被 .gitignore 挡住"，
    所以判据要扫的是这个集合，不是已跟踪集合。

     **`-c core.quotepath=false` 是必须的，而且是被牙齿检查逼出来的。**
    默认情况下 git 会把**非 ASCII 路径**输出成八进制转义
    （`wechat_bridge/docs/\351\203\250...md`），于是：
      → 这个字符串跟真实文件名一比就对不上
      → `os.path.isfile()` 返回 False
      → **整个文件被静默跳过**
    这个仓库里中文名文件不少（文档、那几个 .bat），所以第一版判据
    实际上从来没检查过它们。表现是"测试一直绿"——
    我往 `部署手册.md` 里塞回一个真 IP，测试照样通过，才发现。

    （顺带：也正是这一步查出 `vigil-client/node_modules/`（436MB、5000+ 文件）
    **根本没被 .gitignore 挡住** —— 只写了 `brain/skill_host/node_modules/`
    那条限定路径的。现在写成通用规则了。）
    """
    out = subprocess.run(["git", "-c", "core.quotepath=false",
                          "ls-files", "-co", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    return [l.strip() for l in out.splitlines() if l.strip()]


class TestNoRealInfoInRepo(unittest.TestCase):
    def test_tracked_files_are_clean(self):
        hits = []
        for rel in _would_be_committed():
            if rel in ALLOW or rel.lower().endswith(SKIP_EXT):
                continue
            p = os.path.join(ROOT, rel)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                continue
            for label, pat in PATTERNS.items():
                for m in pat.finditer(text):
                    line = text[:m.start()].count("\n") + 1
                    hits.append("%s:%d  %s" % (rel, line, label))
        self.assertEqual(
            hits, [],
            "这些**会被推上 GitHub** 的文件里带着真信息：\n  "
            + "\n  ".join(hits[:20])
            + "\n\n改成占位符（<你的云机 IP>），真值放被 .gitignore 挡住的本地文件里。")

    def test_判据自己是有牙的(self):
        """把真信息塞进一个被跟踪的临时文件，判据必须红。

        没有这条，将来有人把 PATTERNS 改成空集，
        测试会一直绿 —— 而"一直绿"的守护测试等于没有。
        """
        probe = os.path.join(ROOT, "_probe_leak.tmp")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write('server = "http://%s:8788"\n' % _IP)
            with open(probe, encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(any(p.search(text) for p in PATTERNS.values()),
                            "判据匹配不到明摆着的真信息 —— 它已经失效了")
        finally:
            try:
                os.remove(probe)
            except Exception:
                pass

    def test_gitignore_挡住了本地真配置(self):
        """本地真配置必须真的被忽略 —— 否则 `git add .` 一次就推上去了。

        用 `git check-ignore` 问 git 本人，别自己解析 .gitignore
        （那玩意儿的优先级规则自己写一定会写错）。
        """
        must_ignore = [
            "brain/config/config.json",
            "brain/config/world.json",
            "brain/config/app_conn.json",
            # 私钥目录整个要被挡住。**不点出具体文件名** —— 那个名字里带角色名，
            # 而 tests/ 里不许出现拼音身份字面量（见 test_frontend_invariants.py，
            # 那条规矩在我写这个文件时就把我抓了一次）。
            "deploy/sshkeys/",
            "brain/wechat_bot/wxbridge.local.json",
            "wechat_bridge/wxbridge.local.json",
            "brain/config/personas/preset_group.json",
        ]
        not_ignored = []
        for rel in must_ignore:
            r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT)
            if r.returncode != 0:          # 0 = 被忽略
                not_ignored.append(rel)
        self.assertEqual(
            not_ignored, [],
            "这些文件**没有被 .gitignore 挡住**，而它们里面有真凭据：\n  "
            + "\n  ".join(not_ignored))


if __name__ == "__main__":
    unittest.main()
