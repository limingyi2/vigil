"""P23 验收：管理心跳开关必须 fail-closed。

回归的是一条真实风险：旧实现写的是 `if mg.get("enabled") is False: return`，
enabled 键一旦缺失就等于启用；又因为 base 会回退到 sync.cloud_url，配置里
只要同时留着 node_id、token 和一个旧云地址，开机就会静默每 15 秒外发一次
带 Bearer token 的心跳。这里逐条钉住"缺省即关闭"。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'brain'))

import management_agent as mgmt


FULL = {"cloud_url": "http://127.0.0.1:8788", "node_id": "local-pc",
        "token": "t" * 32}


def cfg(**management):
    return {"management": dict(management),
            "sync": {"enabled": True, "cloud_url": "http://203.0.113.45:8788",
                     "cloud_token": "legacy-token"}}


class EnabledMustBeExplicit(unittest.TestCase):
    def test_missing_enabled_is_off(self):
        """enabled 键被删掉（配置迁移/清理脚本）不能等于开启。"""
        self.assertIsNone(mgmt._resolve_from_config(cfg(**FULL)))

    def test_none_is_off(self):
        self.assertIsNone(mgmt._resolve_from_config(cfg(enabled=None, **FULL)))

    def test_false_is_off(self):
        self.assertIsNone(mgmt._resolve_from_config(cfg(enabled=False, **FULL)))

    def test_truthy_string_is_not_enough(self):
        """从 env/JSON 里读进来的 "true" 是字符串，不能当布尔真。"""
        self.assertIsNone(mgmt._resolve_from_config(cfg(enabled="true", **FULL)))

    def test_one_is_not_enough(self):
        self.assertIsNone(mgmt._resolve_from_config(cfg(enabled=1, **FULL)))

    def test_explicit_true_starts(self):
        p = mgmt._resolve_from_config(cfg(enabled=True, **FULL))
        self.assertIsNotNone(p)
        self.assertEqual(p["base"], "http://127.0.0.1:8788")
        self.assertEqual(p["node_id"], "local-pc")


class NoSyncFallback(unittest.TestCase):
    def test_sync_cloud_url_does_not_enable_reporting(self):
        """sync 段是被废弃的同步链路，不能再给管理上报兜底地址。"""
        c = cfg(enabled=True, node_id="local-pc", token="t" * 32)
        c["management"].pop("cloud_url", None)
        self.assertIsNone(mgmt._resolve_from_config(c))

    def test_sync_block_alone_never_starts(self):
        c = {"sync": {"enabled": True, "cloud_url": "http://203.0.113.45:8788",
                      "cloud_token": "legacy-token"}}
        self.assertIsNone(mgmt._resolve_from_config(c))

    def test_no_config_at_all(self):
        for value in (None, {}, {"management": None}, {"management": {}}):
            self.assertIsNone(mgmt._resolve_from_config(value))


class StartIsQuiet(unittest.TestCase):
    def test_start_returns_false_and_starts_no_thread(self):
        mgmt._STARTED = False
        self.assertFalse(mgmt.start_from_config(cfg(**FULL)))
        self.assertFalse(mgmt._STARTED)

    def test_broken_config_never_raises(self):
        for value in (None, {}, {"management": "not-a-dict"}):
            self.assertFalse(mgmt.start_from_config(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
