# -*- coding: utf-8 -*-
"""run-mode 切换不得丢配置键。

历史事故：server 的 /api/run-mode 把白名单子集 API_CFG 传给 run_modes.switch，
返回的 _cfg 被整份写回 config.json —— 子集里没有 brain_token，落一次盘凭据
就没了。客户端每次现读配置拿不到 token，之后所有请求 401 "bad token"，
且永远换不回云端。这里锁死：_cfg 必须保住传入配置的每一个键。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))

import run_modes  # noqa: E402


def _full_cfg():
    return {
        "model_mode": "local",
        "brain_token": "tok-should-survive",
        "mobile": {"token": "legacy"},
        "features": {"pc": {"x": 1}},
        "api_key": "sk-test",
        "api_base": "https://api.example.com/v1",
        "model": "test-model",
        "provider": "siliconflow",
        "api_keys": {"siliconflow": {"api_key": "sk-test"}},
        "ollama": {"base": "http://127.0.0.1:11434", "model": "qwen2b"},
        "persona_file": "persona.md",
    }


def test_switch_back_to_cloud_keeps_every_key(monkeypatch):
    monkeypatch.setattr(run_modes, "unload_idle",
                        lambda cfg: {"unloaded": [], "kept": [], "failed": []})
    cfg = _full_cfg()
    res = run_modes.switch(cfg, "cloud")
    assert res.get("ok"), res
    nc = res.get("_cfg") or {}
    missing = {k for k in cfg if k not in nc}
    assert not missing, "落盘配置丢了键：%s" % sorted(missing)
    assert nc.get("brain_token") == "tok-should-survive"


def test_switch_to_local_keeps_every_key(monkeypatch):
    # 不要求本机真有 Ollama：ensure_service 失败时 switch 直接返回 not ok，
    # 此时本就没有 _cfg，不落盘、也无从丢键 —— 这个用例锁的是成功路径。
    monkeypatch.setattr(run_modes, "ensure_service",
                        lambda cfg: {"ok": True})
    monkeypatch.setattr(run_modes, "_tags", lambda cfg: ["qwen2b"])
    monkeypatch.setattr(run_modes, "warmup",
                        lambda cfg, m: {"ok": True, "ms": 1})
    cfg = _full_cfg()
    cfg["model_mode"] = "cloud"      # 从云端切到本地，走真正落配置的路径
    res = run_modes.switch(cfg, "local", "qwen2b")
    assert res.get("ok"), res
    nc = res.get("_cfg") or {}
    missing = {k for k in cfg if k not in nc}
    assert not missing, "落盘配置丢了键：%s" % sorted(missing)
    assert nc.get("brain_token") == "tok-should-survive"
