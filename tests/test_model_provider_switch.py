# -*- coding: utf-8 -*-
"""切了服务商，模型列表必须跟着换 —— 前后端都要钉住。

## 这个 bug 长什么样

面板上点「硅基流动」，按钮高亮了、地址也填上了，但下面的模型下拉里
还是一排 `mimo-v2.5` / `mimo-v2.6-pro`。第一反应是"分类逻辑错了"，
其实分类没问题 —— **服务端压根没用刚选的那家去拉**。

`/api/models` 原来只读 `api_config`（已保存的配置）里的 base 和 key。
前端点服务商只改了输入框里那行文字，没告诉服务端。于是：
界面说硅基流动、请求发的是小米地址、列表是小米的。

## 为什么两个库都要判

`brain/model_hub.py` 的缓存**按 base 分键**（`_read_cache(base)` 里
`c.get("base") == base` 才命中），所以切地址本来就能各拿各的列表，
缺的只是"用请求参数而不是配置参数"这一条路。这是服务端一处改动。
前端那边则是"切了要不要重拉、要不要把 base 传过去"，面板和 App 各写一份，
两边都错过同一个错 —— 所以两个都判。

## 判据钉的是哪几件静默失效的事

1. 服务端**认** `base`/`key` 参数（认了才可能按新地址拉）
2. 服务端**用** 它 —— 传了 base 却仍然读 `api_config` 的话，
   上面那条断言照样绿，bug 还在
3. 前端切服务商**会调**重拉，且**把输入框里的地址传过去**
4. 当前选中的模型不在新列表里时**要说一句** —— 不说的话用户以为换好了，
   保存后她照样调不上那个模型，而界面全程正常
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "admin", "dashboard.html")
APP = os.path.join(ROOT, "vigil-client", "src", "js", "pages.js")
SERVER = os.path.join(ROOT, "brain", "server.py")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _server_src():
    return _read(SERVER)


def _models_route():
    """抠出 /api/models 那一段路由。判据不能扫全文 —— 全文里有的是
    「回客户端用脱敏版」这类正确写法，混在一起会掩盖真问题。"""
    src = _server_src()
    i = src.find('elif path == "/api/models"')
    self = None
    assert i > 0, "找不到 /api/models 路由"
    seg = src[i:i + 2600]
    return seg


class TestServerHonorsPreviewParams(unittest.TestCase):
    def setUp(self):
        self.seg = _models_route()

    def test_get_preview_ignores_url_credentials(self):
        self.assertIn('_query_param(self.path, "base")', self.seg)
        self.assertNotIn('_query_param(self.path, "key")', self.seg)
        self.assertIn('p_key = ""', self.seg)
        self.assertIn('"/api/models/list"', _server_src())

    def test_preview_uses_selected_base_without_cross_endpoint_fallback(self):
        self.assertIn('api_base=p_base', self.seg)
        self.assertIn('provider_key(api_config, p_prov, p_base)', self.seg)
        self.assertIn('api_key=k or ""', self.seg)
        self.assertNotIn('api_key=k or cur.get("api_key")', self.seg)

    def test_回给客户端的仍然是脱敏版(self):
        """加了 preview 之后最容易犯的错：为了方便把 cur 直接回出去，
        真 key 就跟着出去了。只在这一次调用里用，不能进响应。"""
        self.assertIn('"current": model_hub.public_current(api_config)', self.seg,
                      "响应里的 current 必须是 public_current（脱敏版）")
        self.assertNotIn('"current": cur', self.seg,
                         "把内部那份 cur 回出去了 —— 里面有真 key")


class TestPanelRefetchesOnProviderSwitch(unittest.TestCase):
    def setUp(self):
        self.h = _read(PANEL)

    def test_点服务商_会_调重拉(self):
        """点服务商的处理已经抽进 switchProvider，所以判据要跟着它走 ——
        只看按钮那几行的话，逻辑搬到哪儿都能过。

        但也**不能**只判"switchProvider 里有没有 pullModels"：
        那样把重拉挪到一个永远不调的分支里照样绿。判据要求
        onclick 确实指向它，且它确实调了重拉。
        """
        m = re.search(r"mProvs\.forEach\(x => \{(.*?)\n    \}, x\.name", self.h, re.S)
        self.assertIsNotNone(m, "找不到服务商按钮的渲染")
        self.assertIn("switchProvider(x)", m.group(1),
                      "按钮没接到切换逻辑上")
        i = self.h.find("async function switchProvider(")
        self.assertGreater(i, 0, "找不到 switchProvider")
        body = self.h[i:i + 2000]
        self.assertIn("await pullModels(false, x.key)", body,
                      "点服务商只是改了高亮和地址，没重新拉列表")

    def test_重拉_把输入框里的地址和密钥传过去(self):
        m = re.search(r"async function pullModels\(force\) \{(.*?)\n  \}", self.h, re.S)
        self.assertIsNotNone(m, "找不到 pullModels")
        body = m.group(1)
        self.assertIn("apiBase.value", body, "没读输入框里的地址")
        self.assertIn("base=' + encodeURIComponent(base)", body,
                      "base 没传给服务端 —— 服务端只能读已保存的配置，"
                      "于是又变回「换了服务商、列表还是旧的」")

    def test_列表来源变了_要提示(self):
        """不提示的话用户以为换好了，保存后她照样调不上那个模型。"""
        self.assertIn("不在这份列表里", self.h,
                      "当前选中的模型不在新列表里时没有任何提示")

    def test_旧写法_不再出现(self):
        """切完服务商后不再直接调不带 base 的 /api/models。"""
        m = re.search(r"async function pullModels\(force\) \{(.*?)\n  \}", self.h, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("api('/api/models')", m.group(1),
                         "还在用不带参数的那个调用（读的是已保存配置）")


class TestAppRefetchesOnProviderSwitch(unittest.TestCase):
    """换服务商后要能看到"那家自己的模型列表"。

    2026-10-08：这段界面搬到客户端了，实现从"点一下立刻重拉"改成
    **换下拉先带出默认地址，按「重新拉取模型」才重拉**（客户端地址栏是手填的，
    自动重拉会把他刚填的地址冲掉）。断言跟着实现走，但钉的还是三件事：
      1. 换下拉会带出这一家的地址；
      2. 重拉时把**地址和密钥**都传过去（不传就成了拿旧家的列表）；
      3. 按钮不再走"不带参数、只靠服务端当前配置"那条老路。
    """

    def setUp(self):
        self.h = _read(APP)

    def test_换下拉_会_带出该家地址(self):
        self.assertIn("provSel.addEventListener('change'", self.h,
                      "客户端换服务商没有联动地址")
        self.assertIn("baseInp.value = p.base", self.h,
                      "换服务商没带出该家的默认地址")

    def test_重拉_把地址与密钥都传过去(self):
        self.assertIn("/api/models?force=1&base=", self.h,
                      "重新拉取没有带 base —— 拿到的还是当前配置那家的列表")
        self.assertIn("encodeURIComponent(key)", self.h,
                      "重新拉取没带 key：用户换地址后可能也换了密钥")

    def test_重拉按钮存在且不是旧路径(self):
        self.assertIn("id='mPull'", self.h.replace('"', "'") or self.h,
                      "客户端没有「重新拉取模型」按钮")
        self.assertNotIn('onclick="loadModelCfg(true)"', self.h,
                         "按钮还在走不带参的老路径")


class TestCacheIsKeyedByBase(unittest.TestCase):
    """服务端按 base 分键缓存 —— 这是"切地址能各拿各的列表"的前提。
    如果哪天改成不按 base 分键，切服务商就会拿到上一家的缓存，
    而上面两条判据全绿。"""

    def setUp(self):
        self.src = _read(os.path.join(ROOT, "brain", "model_hub.py"))

    def test_缓存命中条件里比对了_base(self):
        self.assertRegex(self.src, r'c\.get\("base"\) == base',
                         "缓存不再按 base 分键 —— 切地址会命中上一家的缓存")
        self.assertRegex(self.src, r'"base":\s*base,', "写缓存时没记 base")


if __name__ == "__main__":
    unittest.main()
