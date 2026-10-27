# -*- coding: utf-8 -*-
"""云端 OpenAI 兼容服务商：把 brain.py 里原有的 httpx 直调原样包一层。

 这个类存在的意义是"行为零变化"：默认模式（model_mode 缺省 = cloud）
发出去的请求体、headers、超时、异常语义必须和改造前逐字节一致。
它只负责"知道用哪个地址/哪把 key/哪个模型"，不加任何额外参数。
"""
import httpx as _httpx

from .base import (
    ModelProvider, ProviderUnavailable, ProviderHTTPError, ProviderResponseError,
)


def _active_http():
    """实际发请求用的 httpx 模块。

    生产里 `brain.httpx` 就是真 httpx；测试和 vigil-eval worker 会替换
    `brain.httpx`（或对 httpx 模块做 monkeypatch）来打桩。传输层统一从
    brain 模块取，老的打桩点（draft_native 内联时代就是这么桩的）就不用
    因为 provider 收口而全部改道。
    """
    try:
        import brain
        mod = getattr(brain, "httpx", None)
        if mod is not None:
            return mod
    except Exception:
        pass
    return _httpx


class CloudProvider(ModelProvider):
    kind = "cloud"
    supports_tools = True
    supports_stream = True
    supports_vision = True

    def __init__(self, cfg=None):
        cfg = cfg or {}
        self._cap_cfg = cfg
        self.supports_stream = cfg.get('model_stream', True) is not False
        self.api_base = str(cfg.get("api_base") or "").rstrip("/")
        from model_hub import current
        self.api_key = current(cfg)['api_key']
        self.model = str(cfg.get("model") or "")
        self.provider = str(cfg.get("provider") or "").strip().lower()
        overrides = cfg.get('model_capabilities') or {}
        if isinstance(overrides, dict):
            self.supports_tools = overrides.get('native_tool_calls') is not False
        if (not self.provider
                and self.api_base.lower() == "https://api.xiaomimimo.com/v1"):
            # Older configs may predate the provider field. The exact
            # first-party base is safe to recognize; arbitrary custom URLs
            # remain unsupported for the XML compatibility path.
            self.provider = "xiaomi"
        # MiMo's OpenAI-compatible endpoint accepts the tools schema, but
        # some deployed MiMo versions emit complete XML-like tool requests in
        # content alongside incomplete message.tool_calls (see wire evidence).
        # Keep this
        # opt-in narrowly bound to the first-party provider and model family;
        # arbitrary custom endpoints must never gain a text execution path.
        self.supports_xml_tools = (
            self.provider == "xiaomi"
            and self.model.lower().startswith("mimo-")
            and str(cfg.get("allow_xml_tool_fallback", True)).lower() not in ('false', '0', 'off', 'no')
        )
        # MiMo documents the OpenAI-compatible reasoning_effort field.  Keep
        # this knowledge inside the adapter; arbitrary custom gateways do not
        # inherit it from a model name.
        if (self.provider == 'xiaomi' and self.model.lower().startswith('mimo-')
                and not isinstance(cfg.get('model_capabilities'), dict)):
            self._cap_cfg = dict(cfg)
            self._cap_cfg['model_capabilities'] = {
                'reasoning': True, 'reasoning_parameter': 'reasoning_effort',
                'reasoning_history_field': 'reasoning_content'}

    def _url(self):
        return self.api_base + "/chat/completions"

    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key}

    def assistant_history_message(self, message, content_override=None, tool_calls=None):
        out = super().assistant_history_message(message, content_override, tool_calls)
        # Some OpenAI-compatible endpoints require their reasoning field to be
        # echoed alongside tool calls. This is opt-in per endpoint capability;
        # no provider/model name inference or cross-provider replay occurs.
        opts = (self._cap_cfg.get('model_capabilities') or {}) if isinstance(self._cap_cfg, dict) else {}
        field = opts.get('reasoning_history_field') if isinstance(opts, dict) else None
        reasoning = message.get('reasoning') or message.get('reasoning_content')
        if field == 'reasoning_content' and isinstance(reasoning, str) and reasoning:
            out[field] = reasoning
        return out

    def chat(self, payload, timeout=90):
        if not self.api_base or not self.api_key:
            # 与 brain.py 旧门语义一致：没配 key 时上层直接判"没稿"，
            # 不当成异常打日志吓人。
            return None
        http = _active_http()
        try:
            r = http.post(self._url(), headers=self._headers(),
                          json=payload, timeout=timeout)
        except _httpx.TimeoutException as e:
            raise ProviderUnavailable("云端请求超时：%s" % str(e)[:120])
        except _httpx.ConnectError as e:
            raise ProviderUnavailable("云端连接失败：%s" % str(e)[:120])
        if r.status_code != 200:
            raise ProviderHTTPError("云端返回 %s" % r.status_code,
                                    status=r.status_code, body=r.text)
        try:
            return r.json()
        except Exception as e:
            raise ProviderResponseError("云端响应不是合法 JSON：%s" % e)

    def stream(self, payload, timeout=120):
        """直通 httpx.stream；调用方用法与改造前完全相同。"""
        if not self.api_base or not self.api_key:
            raise ProviderUnavailable('当前服务商和 API Base URL 未配置密钥')
        return _active_http().stream("POST", self._url(),
                                     headers=self._headers(),
                                     json=payload, timeout=timeout)

    def health(self, timeout=4):
        if not self.api_base:
            return False, {"err": "没配接口地址"}
        if not self.api_key:
            return False, {'err': '当前服务商和 API Base URL 未配置密钥'}
        try:
            r = _active_http().get(self.api_base.rstrip("/v1") + "/models",
                                   headers=self._headers(), timeout=timeout)
            if r.status_code == 200:
                return True, {}
            return False, {"status": r.status_code}
        except Exception as e:
            return False, {"err": "%s: %s" % (type(e).__name__, str(e)[:100])}
