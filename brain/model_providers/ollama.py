# -*- coding: utf-8 -*-
"""本机 Ollama 提供者（OpenAI 兼容层 + 原生 /api/tags）。

实测事实（vigil-eval G0/A01-A06，2026-10）：
- POST {base}/v1/chat/completions 能驱动 brain.py 原生 FC 多轮工具循环；
- shim 会把请求体里的 `options` 透给 Ollama：num_ctx / num_predict 必须在这里
  注入，否则吃 Ollama 默认 4096 上下文，ReAct 长循环必被截断；
- efficientthink 系列的思考开关是**顶层** `think`（不是 options.thinking）；
- Authorization 会被忽略，但 brain.py 入口有"没 api_key 就不调"的门，
  所以 ready() 不把 key 当必需项（由 brain 改问 provider.ready()）；
- usage 随 OpenAI 形态回 prompt_tokens/completion_tokens，brain 现有统计直接能用。

本类只做"和 Ollama 对话"的通用能力；任务限额（请求数/工具数/墙钟）是
P2 Dispatcher 的职责，不放在这里。
"""
import httpx

from .base import (
    ModelProvider, ProviderUnavailable, ProviderHTTPError, ProviderResponseError,
)

DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 16384
DEFAULT_NUM_PREDICT = 2048
PLACEHOLDER_KEY = "ollama"          # Ollama 不校验，但 OpenAI 客户端习惯带 Authorization

# 非 200 响应体里的典型错误线索 → 归类（上层据此决定冷却/重试/换模型）
_OOM_HINTS = ("out of memory", "outofmemory", "oom", "insufficient",
              "gpu memory", "cuda error", "no space left")
_MODEL_HINTS = ("model not found", "no such model", "not found",
                "try pulling", "unable to find")


class OllamaUnavailable(ProviderUnavailable):
    """Ollama 没装 / 服务没起 / 模型没加载。hybrid 下应回退云端主模型。"""


def _opt_int(v, default):
    """None/缺失 → default；合法整数（含 0）→ 整数；非法 → default。"""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _classify_http(status, body):
    """把非 200 响应归成 (reason, 人话)。reason 供程序判断。"""
    low = (body or "").lower()
    if any(h in low for h in _OOM_HINTS):
        return "oom", "本地模型显存/内存不足"
    if status == 404 or any(h in low for h in _MODEL_HINTS):
        return "model_not_found", "本地没有这个模型，先在 Ollama 里 pull"
    if status in (400, 422):
        return "bad_request", "本地模型拒绝了请求参数"
    return "http_error", "本地模型返回 %s" % status


class OllamaProvider(ModelProvider):
    kind = "ollama"
    supports_tools = True            # /v1 兼容层支持 tools（A0x 已实证）
    supports_stream = False          # Native NDJSON is not exposed as SSE yet.
    supports_vision = False          # 首版接的是纯文本高效思考模型

    def __init__(self, ollama_cfg=None):
        cfg = ollama_cfg or {}
        self._cap_cfg = {'model_mode': 'local', 'ollama': cfg}
        # 存"裸"地址：允许用户带不带 /v1，统一剥掉后自己拼，避免 /v1/v1。
        base = str(cfg.get("base") or DEFAULT_BASE).strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        self.base = base or DEFAULT_BASE
        self.model = str(cfg.get("model") or "").strip()
        self.api_key = str(cfg.get("api_key") or PLACEHOLDER_KEY) or PLACEHOLDER_KEY
        # 上下文/输出预算。显式 0 = 不注入这个 option（交回 Ollama 默认）；
        # 缺省(None)才用内置默认值。注意不能用 `x or DEFAULT`，0 会被吞成默认。
        self.num_ctx = _opt_int(cfg.get("num_ctx"), DEFAULT_NUM_CTX)
        self.num_predict = _opt_int(cfg.get("num_predict"), DEFAULT_NUM_PREDICT)
        # think：true/false 显式下发；None 表示不带上这个字段（交给 ollama 默认）
        t = cfg.get("think", True)
        self.think = None if t is None else bool(t)
        try:
            self.request_timeout = float(cfg.get("request_timeout") or 120)
        except (TypeError, ValueError):
            self.request_timeout = 120.0

    # --- 就绪/健康 ---
    def ready(self):
        """brain.py 入口门用：本地模式不要求云端 api_key，但要有地址和模型名。"""
        return bool(self.base and self.model)

    def _tags(self, timeout=4):
        r = httpx.get(self.base + "/api/tags", timeout=timeout)
        if r.status_code != 200:
            raise ProviderHTTPError("Ollama 返回 %s" % r.status_code,
                                    status=r.status_code, body=r.text)
        data = r.json()
        out = []
        for m in data.get("models") or []:
            if isinstance(m, dict) and m.get("name"):
                out.append({"name": m["name"],
                            "size": m.get("size") or 0,
                            "details": m.get("details") or {}})
        return out

    def list_models(self, timeout=4):
        """已安装模型清单（[{name,size,details}]）。失败抛 ProviderUnavailable。"""
        try:
            return self._tags(timeout=timeout)
        except ProviderHTTPError:
            raise
        except Exception as e:
            raise OllamaUnavailable("连不上 Ollama（%s: %s）"
                                    % (type(e).__name__, str(e)[:100]))

    def health(self, timeout=4):
        """供 UI/接口：永不抛异常，回 (ok, {models|err,model_configured})。"""
        try:
            models = self._tags(timeout=timeout)
            names = [m["name"] for m in models]
            return True, {"models": names,
                          "model_configured": self.model,
                          "model_installed": (not self.model) or self.model in names}
        except ProviderHTTPError as e:
            return False, {"err": e.body or str(e), "status": e.status}
        except Exception as e:
            return False, {"err": "%s: %s" % (type(e).__name__, str(e)[:120])}

    # --- 请求组装（走 Ollama 原生 /api/chat）---
    # 为什么不用 /v1 OpenAI 兼容层：2026-10 实测当前 Ollama 版的 /v1 shim
    # **忽略 options.num_ctx**（大上下文一律按默认 4096 拒），原生 /api/chat
    # 才认 num_ctx/num_predict/think。好消息：原生端点的 tools 定义、assistant
    # 带 tool_calls + role:tool 回灌，和 OpenAI 形态一致（实测两轮 FC 通过），
    # 所以 Brain 那边的消息构造一字不用改，这里只做请求/响应适配。
    def _headers(self):
        return {"Authorization": "Bearer " + self.api_key}

    @staticmethod
    def _norm_message(m):
        """单条消息的请求方向适配。

        多轮工具调用时 Brain 会把上一轮 assistant 消息原样回灌，里面
        tool_calls.function.arguments 是 OpenAI 形态的 **JSON 字符串**；
        Ollama 原生端要求它是**对象**（字符串会 400：
        "Value looks like object, but can't find closing '}' symbol"）。
        """
        import json as _json
        m = dict(m or {})
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            new_tcs = []
            for tc in tcs:
                tc2 = dict(tc or {})
                fn = dict(tc2.get("function") or {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = _json.loads(args) if args.strip() else {}
                    except Exception:
                        fn["arguments"] = {}
                tc2["function"] = fn
                new_tcs.append(tc2)
            m["tool_calls"] = new_tcs
        return m

    def build_payload(self, payload):
        """OpenAI chat.completions payload → Ollama 原生 /api/chat payload。"""
        p = dict(payload or {})
        if self.model:
            p["model"] = self.model
        p["stream"] = False
        if isinstance(p.get("messages"), list):
            p["messages"] = [self._norm_message(m) for m in p["messages"]]
        # reasoning_effort 是云端特有参数，Ollama 用 think + num_predict 控制，
        # 带着发过去可能被当非法字段，删掉。
        p.pop("reasoning_effort", None)
        # format 是原生 /api/chat 顶层字段（修复 B 起在无工具收口/修正轮携带）：
        # "json" 字符串 = 裸 JSON mode；dict = JSON Schema 约束解码（官方支持，
        # 逐 token 掩码保证输出符合 schema）。dict 必须原样透传——转 str 会把
        # Python repr 发给端点直接 400。工具轮不带该字段，避免破坏 FC 输出。
        if "format" in p and p["format"] not in (None, ""):
            if not isinstance(p["format"], (str, dict)):
                p["format"] = str(p["format"])
        opts = dict(p.get("options") or {})
        if self.num_ctx:
            opts.setdefault("num_ctx", self.num_ctx)
        if self.num_predict:
            # OpenAI 侧的 max_tokens 在 Ollama 这映射成 num_predict（原生字段，
            # 优先级确定）；max_tokens 自身删掉免得两个上限打架。
            opts.setdefault("num_predict",
                            p.pop("max_tokens", self.num_predict))
        p.pop("max_tokens", None)
        if opts:
            p["options"] = opts
        if self.think is not None:
            p["think"] = self.think
        return p

    @staticmethod
    def _adapt_response(data):
        """Ollama 原生响应 → Brain 期望的 OpenAI chat.completions 形态。

        差异：message.tool_calls[].function.arguments 原生给 **dict**，
        OpenAI/Brain 期望 JSON 字符串；思考在 message.thinking（→
        reasoning_content）；用量字段名不同。
        """
        import json as _json
        msg = dict(data.get("message") or {})
        tcs_out = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if not isinstance(args, str):
                args = _json.dumps(args or {}, ensure_ascii=False)
            tcs_out.append({"id": tc.get("id") or "",
                            "type": "function",
                            "function": {"name": fn.get("name") or "",
                                         "arguments": args}})
        omsg = {"role": msg.get("role") or "assistant",
                "content": msg.get("content") or "",
                "reasoning_content": msg.get("thinking") or ""}
        if tcs_out:
            omsg["tool_calls"] = tcs_out
        finish = 'length' if data.get('done_reason') == 'length' else ('tool_calls' if tcs_out else 'stop')
        return {"choices": [{"message": omsg, "finish_reason": finish}],
                "usage": {"prompt_tokens": data.get("prompt_eval_count") or 0,
                          "completion_tokens": data.get("eval_count") or 0}}

    def chat(self, payload, timeout=None):
        """发一次对话补全，回 OpenAI 形态的响应 dict。

        连不上/超时 → OllamaUnavailable（hybrid 据此回退云端）；
        非 200 → ProviderHTTPError（.reason 带 oom/model_not_found/...）。
        """
        url = self.base + "/api/chat"
        body = self.build_payload(payload)
        to = timeout or self.request_timeout
        try:
            r = httpx.post(url, headers=self._headers(), json=body, timeout=to)
        except httpx.TimeoutException as e:
            raise OllamaUnavailable("Ollama 请求超时：%s" % str(e)[:100])
        except httpx.ConnectError as e:
            raise OllamaUnavailable("Ollama 连不上（确认它已启动）：%s" % str(e)[:100])
        if r.status_code != 200:
            reason, why = _classify_http(r.status_code, r.text)
            err = ProviderHTTPError(why, status=r.status_code, body=r.text)
            err.reason = reason
            raise err
        try:
            data = r.json()
        except Exception as e:
            raise ProviderResponseError("Ollama 响应不是合法 JSON：%s" % e)
        out = self._adapt_response(data)
        if not out["choices"][0]["message"].get("content") \
                and not out["choices"][0]["message"].get("tool_calls") \
                and not out["choices"][0]["message"].get("reasoning_content"):
            raise ProviderResponseError("Ollama 响应里没有正文也没有工具调用：%s"
                                        % str(data)[:200])
        return out

    def stream(self, payload, timeout=None):
        """P1 暂不支持本地流式：/api/chat 的流式是 NDJSON，不是 SSE。

        Brain.stream_chat 外层会接住 NotImplementedError 回 error 事件，
        客户端已有"降级非流式"路径。NDJSON→SSE 适配留到 P3。
        """
        raise NotImplementedError("本地模型流式在 P1 未开放，请走非流式")
