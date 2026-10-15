# -*- coding: utf-8 -*-
"""Provider 基类与共用数据结构。

chat() 的契约是按 brain.py 现有调用方式裁的（不多做）：
- 入参 payload：OpenAI chat.completions 请求体（model/messages/max_tokens/
  temperature/tools/...），timeout 秒；
- 返回 **已解析的 dict**（响应 JSON），调用方不再碰 httpx.Response；
- HTTP 非 2xx 抛 ProviderHTTPError（含状态码与响应摘要），
  连不上/超时抛 ProviderUnavailable，响应体坏掉抛 ProviderResponseError。
  brain.draft_native 外层已有"异常 → 去工具表重试/明说失败"的兜底，
  错误分类让上层将来能区分"本地模型没起（应回退主模型）"和"普通请求失败"。
"""


class ProviderError(Exception):
    """所有 provider 错误的基类。category 给程序判，message 给人看。"""

    category = "provider_error"

    def __init__(self, message="", *, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = str(body or "")[:500]


class ProviderUnavailable(ProviderError):
    """服务没起来 / 连接被拒 / 超时 —— 混合模式下应回退主模型，而不是报错给用户。"""
    category = "unavailable"


class ProviderHTTPError(ProviderError):
    """服务在线但返回非 2xx（含 OOM、参数不支持等）。"""
    category = "http_error"


class ProviderResponseError(ProviderError):
    """200 但响应体解析不出 choices（模型/网关给了坏格式）。"""
    category = "bad_response"


class ChatResult(object):
    """轻量响应包装（可选使用）：目前工厂直接回 dict，这个类预留给流式/统一观测。"""

    __slots__ = ("data", "raw")

    def __init__(self, data, raw=None):
        self.data = data
        self.raw = raw

    def json(self):
        return self.data


class ModelProvider(object):
    """Provider 接口。chat 必须实现；health/list_models 不支持就返回保守值。"""

    #: 这台执行端的能力声明（供 UI/上层做功能降级）
    supports_tools = True
    # XML-like tool text is never treated as an executable protocol by default.
    # Providers must opt in explicitly after proving that format is part of their
    # supported API contract; native structured tool_calls remain the only default.
    supports_xml_tools = False
    supports_stream = True
    supports_vision = False
    kind = "base"

    def chat(self, payload, timeout=90):
        raise NotImplementedError

    def stream(self, payload, timeout=120):
        """返回一个上下文管理器，行为对齐 httpx.stream（响应对象逐行 SSE）。"""
        raise NotImplementedError

    def health(self, timeout=4):
        """→ (ok: bool, info: dict)。不抛异常（健康检查失败也要能给 UI 看原因）。"""
        return False, {"err": "not_implemented"}

    def capabilities(self):
        out = {
            "kind": self.kind,
            "tools": self.supports_tools,
            "native_tool_calls": self.supports_tools,
            "xml_tool_text": self.supports_xml_tools,
            "stream": self.supports_stream,
            "vision": self.supports_vision,
        }
        from .execution import capabilities
        out['model'] = capabilities(getattr(self, '_cap_cfg', {}))
        return out
