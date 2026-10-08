# -*- coding: utf-8 -*-
"""App 前端（assets/chat.html）的不变量。纯静态检查，不需要浏览器。

2026-10-08 改造：App 从完整聊天界面改成**框架页**（聊天 / 日记 / 朋友圈 / 人设 /
模型面板全部移除，界面留待后续重做）。所以这一批断言跟着换了目标 —— 从"界面里必须有
某个 DOM / 某个函数"变成"**框架必须保住的那几样东西还在**"。

框架页刻意保留的东西（少一样，以后重做就得重写）：
  1. DEF_BASE / DEF_TOKEN / APP_CODE 三个占位常量 —— deploy/rebuild_apk.py 构建时
     按正则注入地址、口令、版本号；格式一变注入就静默失败，APK 里会是空值。
  2. authedFetch：带 Authorization 头 + 把 token 从 URL 上摘掉。
  3. window.Android.* 调用（saveConn / installUpdate）—— MainActivity 用
     @JavascriptInterface 注入，名字写错就是 undefined：连接信息存不进本机、
     更新装不上。
  4. 仓库里不能出现真实地址 / 口令（app_conn.json 是 gitignore 的）。
  5. 空地址时要给出提示，不能静默发一堆失败请求。
另外钉一条"已删的不许回来"：前端不再请求日记 / 朋友圈接口（brain 侧已删）。
"""
import hashlib
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_HTML = os.path.join(ROOT, "android-app", "app", "src", "main",
                          "assets", "chat.html")
ROOT_HTML = os.path.join(ROOT, "_chat.html")


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _src():
    with io.open(ASSET_HTML, encoding="utf-8") as f:
        return f.read()


class TestFrameworkInvariants(unittest.TestCase):

    def test_asset_html_exists(self):
        self.assertTrue(os.path.isfile(ASSET_HTML))

    def test_the_two_copies_are_byte_identical(self):
        """母本被 gitignore，克隆下来本来就没有 → 那种情况下跳过。"""
        if not os.path.isfile(ROOT_HTML):
            self.skipTest("根目录 _chat.html 不在（它不入库，克隆后本就没有）")
        self.assertEqual(_sha256(ROOT_HTML), _sha256(ASSET_HTML),
                         "_chat.html 与 assets/chat.html 不一致：改前端必须两份一起改")

    # --- 1) 构建注入契约 -------------------------------------------------
    def test_def_base_is_empty_placeholder(self):
        """必须空占位：真实地址由构建注入，写死在仓库里等于泄露。"""
        self.assertIn("const DEF_BASE  = '';", _src())

    def test_def_token_is_empty_placeholder(self):
        self.assertIn("const DEF_TOKEN = '';", _src())

    def test_app_code_placeholder_is_numeric(self):
        """APP_CODE 由构建注入；占位必须是数字 0，构建脚本的正则才替换得掉。"""
        m = re.search(r"const APP_CODE\s*=\s*(\d+);", _src())
        self.assertTrue(m, "找不到 const APP_CODE = <数字>; 这一行")
        self.assertEqual(int(m.group(1)), 0,
                         "仓库里的 APP_CODE 应是占位 0，实际版本号由 version.json 注入")

    # --- 2) 安卓壳桥接 ---------------------------------------------------
    def test_android_bridge_is_still_called(self):
        src = _src()
        self.assertIn("window.Android && window.Android.saveConn", src)
        self.assertIn("window.Android.installUpdate", src)

    # --- 3) 鉴权 ---------------------------------------------------------
    def test_api_calls_carry_an_auth_header(self):
        self.assertIn("Authorization", _src())

    def test_fetch_wrapper_strips_the_token_from_the_url(self):
        """token 拼在 URL 里会进访问日志和 Referer；带过头之后要把 query 里那份摘掉。"""
        src = _src()
        self.assertIn("Bearer", src)
        # 直接断言字面量：源码里那段是 replace(/([?&])token=[^&]*&?/g, '$1')，
        # 用正则去匹配它反而会被里面的 &? 当成量词读歪。
        self.assertIn("replace(/([?&])token=[^&]*&?/g,", src,
                      "authedFetch 没把 URL 上的 token 摘掉")

    # --- 4) 仓库卫生 -----------------------------------------------------
    def test_no_public_ip_or_brain_token_in_html(self):
        src = _src()
        self.assertNotRegex(src, r"\b\d{1,3}(\.\d{1,3}){3}\b:\d+",
                            "前端源码里出现了裸的 IP:端口")
        self.assertNotRegex(src, r"token\s*=\s*['\"][0-9a-f]{8,}['\"]",
                            "前端源码里写了鉴权 token")

    def test_app_conn_is_gitignored(self):
        p = os.path.join(ROOT, "brain", "config", "app_conn.json")
        if not os.path.exists(p):
            self.skipTest("app_conn.json 不在本机")
        with io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("app_conn.json", f.read(),
                          "app_conn.json 装着地址+口令，必须在 .gitignore 里")

    # --- 5) 空地址要说人话 ------------------------------------------------
    def test_empty_base_is_reported_not_silent(self):
        """没填地址时不能静默发请求：每次都失败，日志一片"连不上"，真原因被埋掉。"""
        self.assertRegex(_src(), r"还没填地址|请填|未填")

    # --- 6) 已删的模块不许回来 -------------------------------------------
    def test_no_diary_or_moments_requests(self):
        """brain 侧 /api/diary/* 与 /api/moments 已整体删除，前端再调就是一直报错。"""
        src = _src()
        for pat in ("/api/diary", "/api/moments", "/api/moment/"):
            self.assertNotIn(pat, src, "框架页还在请求已删除的接口：" + pat)

    def test_no_removed_page_entrypoints(self):
        """框架页不该再有打开日记 / 朋友圈 / 人设 / 模型的入口。"""
        src = _src()
        for name in ("openDiary", "openMoments", "openModels", "loadDiaryList"):
            self.assertNotIn(name, src, "框架页还留着 " + name)


if __name__ == "__main__":
    unittest.main()