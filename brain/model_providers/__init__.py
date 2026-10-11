# -*- coding: utf-8 -*-
"""模型提供者（Model Provider）统一入口。

为什么有这一层：brain.py 原来在 4 处直接 httpx.post 拼 OpenAI 请求，
"换模型执行端"只能改 config 里的 api_base。混合模型模式要在**同一套 Brain**
上支持云端 API 与本机 Ollama，差异不只是地址（Ollama 要注入 options.num_ctx、
顶层 think 开关、占位 key、不同的错误语义），散在调用点一定会漏，所以收口成
Provider：

    cloud  → CloudProvider  ：今天的 httpx 直调，行为零变化（默认）
    local  → OllamaProvider ：本机 Ollama OpenAI 兼容层 + /api/tags
    hybrid → 主模型仍走 CloudProvider；P2 的 delegate_to_local 再按需实例化
             OllamaProvider（混合模式本身不改主循环出口）

工厂只按 config 选择，不在构造时发网络请求（健康检查要显式调 health()），
这样 import/配置热更新不会被网络拖死。
"""
from .base import (
    ModelProvider, ChatResult, ProviderError,
    ProviderUnavailable, ProviderHTTPError, ProviderResponseError,
)
from .cloud import CloudProvider
from .ollama import OllamaProvider, OllamaUnavailable

MODES = ("cloud", "local", "hybrid")


def normalize_mode(v):
    """把配置里的模式值收口；缺省/乱填一律 cloud（fail-safe 回现状）。"""
    m = str(v or "").strip().lower()
    return m if m in MODES else "cloud"


def get_provider(cfg):
    """按 api_config 选一个 provider 实例。

    构造函数不联网；调用方在真正发请求时才会碰到网络错误。
    选不出来（配置损坏/未知模式）一律退回 CloudProvider —— 保证"没配新功能
    就是老行为"，绝不能因为这个新模块让她一句都说不出来。
    """
    cfg = cfg or {}
    mode = normalize_mode(cfg.get("model_mode"))
    if mode == "local":
        return OllamaProvider(cfg.get("ollama") if isinstance(cfg.get("ollama"), dict)
                              else {})
    # cloud 和 hybrid 的主模型都走云端（hybrid 的本地委派在 P2 单独取 OllamaProvider）
    return CloudProvider(cfg)


__all__ = [
    "ModelProvider", "ChatResult", "ProviderError",
    "ProviderUnavailable", "ProviderHTTPError", "ProviderResponseError",
    "CloudProvider", "OllamaProvider", "OllamaUnavailable",
    "MODES", "normalize_mode", "get_provider",
]
