"""Model limits and completion semantics shared by chat and Creator.

No credentials are stored in the capability cache. Unknown gateways never
inherit limits solely because their model ID resembles an official model.
"""
import copy
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .base import ProviderResponseError
from .tool_protocol import ThinkTextBoundary

_CACHE = {}
_LOCK = threading.Lock()
PARAMETERS = ('max_tokens', 'max_completion_tokens', 'max_output_tokens')
REASONING_STRATEGIES = ('fast', 'balanced', 'deep')
_REASONING_LEVELS = {'fast': 'low', 'balanced': 'medium', 'deep': 'high'}
_CONTEXT_LIMIT = re.compile(
    r'context[_ -]?length|maximum context|context window|prompt.{0,24}too long|'
    r'too many tokens|input tokens?.{0,32}(limit|maximum|exceed)|'
    r'maximum.{0,24}tokens?.{0,24}(context|input)|上下文.{0,16}(超|长度|限制)|'
    r'(输入|提示词).{0,16}(token|长度).{0,12}(超|限制)|'
    r'输入超过已配置模型容量|上下文空间不足', re.I)


def endpoint_id(endpoint):
    p = urlsplit(str(endpoint or ''))
    # Drop userinfo, query and fragments, which can carry secrets.
    host = p.hostname or ''
    if p.port:
        host += ':' + str(p.port)
    return urlunsplit((p.scheme.lower(), host.lower(), p.path.rstrip('/'), '', ''))


def _positive(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 10000000 else None


def capabilities(cfg, provider=None):
    cfg = cfg or {}
    local = cfg.get('model_mode') == 'local'
    conf = cfg.get('ollama', {}) if local else cfg
    model = str(conf.get('model') or '')
    endpoint = endpoint_id(conf.get('base') if local else cfg.get('api_base'))
    vendor = 'ollama' if local else str(cfg.get('provider') or 'custom').lower()
    overrides = conf.get('model_capabilities') or {}
    if not isinstance(overrides, dict):
        overrides = {}
    # Whitelist non-secret metadata before hashing or caching.
    opts = {k: overrides.get(k) for k in ('max_output_tokens', 'input_tokens',
        'context_tokens', 'output_parameter', 'requires_output_limit', 'compat_output_tokens',
        'reasoning', 'reasoning_parameter', 'reasoning_history_field', 'usage', 'native_tool_calls')}
    key = (vendor, endpoint, model)
    signature = hashlib.sha256(json.dumps(opts, sort_keys=True).encode()).hexdigest()
    with _LOCK:
        previous = _CACHE.get(key)
        if previous and previous['signature'] == signature:
            return copy.deepcopy(previous['record'])
    parameter = opts['output_parameter']
    if parameter not in PARAMETERS:
        parameter = 'max_tokens'
    record = dict(provider=vendor, endpoint=endpoint, model=model,
        output_parameter=parameter, max_output_tokens=_positive(opts['max_output_tokens']),
        input_tokens=_positive(opts['input_tokens']), context_tokens=_positive(opts['context_tokens']),
        requires_output_limit=opts['requires_output_limit'] is True,
        compat_output_tokens=_positive(opts['compat_output_tokens']) or 4096,
        source='administrator' if any(v is not None for v in opts.values()) else 'unknown',
        updated_at=time.time())
    record['confirmed'] = record['max_output_tokens'] is not None
    record['capacity_verified'] = record['context_tokens'] is not None
    record['input_limit_verified'] = record['input_tokens'] is not None
    record['reasoning_supported'] = opts['reasoning'] is True
    record['reasoning_parameter'] = (str(opts['reasoning_parameter'])
        if opts['reasoning_parameter'] in ('reasoning_effort', 'thinking', 'think') else None)
    record['reasoning_history_field'] = (opts['reasoning_history_field']
        if opts['reasoning_history_field'] == 'reasoning_content' else None)
    record['usage_supported'] = opts['usage'] is not False
    record['native_tool_calls'] = opts['native_tool_calls'] is not False
    record['reasoning_strategies'] = list(REASONING_STRATEGIES) if record['reasoning_supported'] else []
    with _LOCK:
        # Bound cache growth when models/endpoints are frequently changed.
        if len(_CACHE) >= 256:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = dict(signature=signature, record=record)
    return copy.deepcopy(record)


def strategy_info(cfg, provider=None):
    """Describe Vigil reasoning tiers without inventing provider features."""
    cap_cfg = getattr(provider, '_cap_cfg', None) or cfg
    cap = capabilities(cap_cfg, provider=provider)
    kind = getattr(provider, 'kind', None) if provider is not None else None
    if kind == 'ollama':
        think = getattr(provider, 'think', None)
        return {'supported': think is not None,
                'strategies': list(REASONING_STRATEGIES) if think is not None else [],
                'parameter': 'think' if think is not None else None,
                'mapping': {'fast': False, 'balanced': True, 'deep': True} if think is not None else {},
                'downgrade': None if think is not None else 'Ollama 未启用思考开关'}
    if cap['reasoning_supported'] and cap['reasoning_parameter']:
        return {'supported': True, 'strategies': list(REASONING_STRATEGIES),
                'parameter': cap['reasoning_parameter'], 'mapping': dict(_REASONING_LEVELS),
                'downgrade': None}
    return {'supported': False, 'strategies': [], 'parameter': None, 'mapping': {},
            'downgrade': '模型未确认原生推理参数，已使用普通生成'}


def apply_reasoning_strategy(payload, cfg, provider, strategy='balanced'):
    """Map a framework strategy through the provider contract.

    Unsupported strategies are reported and removed from the wire payload.
    """
    strategy = str(strategy or 'balanced').lower()
    if strategy not in REASONING_STRATEGIES:
        strategy = 'balanced'
    out = dict(payload or {})
    out.pop('vigil_reasoning_strategy', None)
    info = strategy_info(cfg, provider)
    if info['supported']:
        value = info['mapping'].get(strategy)
        if info['parameter'] == 'think':
            out['think'] = bool(value)
            out.pop('reasoning_effort', None)
        else:
            out[info['parameter']] = value
        return out, dict(info, requested=strategy, applied=strategy, downgraded=False)
    out.pop('reasoning_effort', None)
    out.pop('thinking', None)
    return out, dict(info, requested=strategy, applied=None, downgraded=True)


def prepare(payload, cfg, *, step_tokens=None, remaining_tokens=None):
    if remaining_tokens is not None and (not _positive(remaining_tokens)):
        raise ProviderResponseError('剩余输出预算不足')
    out = dict(payload)
    for param in PARAMETERS:
        out.pop(param, None)
    cap = capabilities(cfg)
    limits = [v for v in (cap['max_output_tokens'], _positive(step_tokens),
                         _positive(remaining_tokens)) if v is not None]
    # Use the same character-based token estimate as the shared context builder.
    # UTF-8 byte length is not a token count (especially for CJK prompts).
    from conversation_context import estimate_tokens, MSG_OVERHEAD, MARGIN_TOKENS
    messages = out.get('messages', [])
    request_shape = {'messages': messages}
    if out.get('tools'):
        request_shape['tools'] = out['tools']
    estimated_input = estimate_tokens(json.dumps(request_shape, ensure_ascii=False))
    if isinstance(messages, list):
        estimated_input += len(messages) * MSG_OVERHEAD
    if cap['input_tokens'] and estimated_input > cap['input_tokens']:
        raise ProviderResponseError('输入超过已配置模型容量')
    if cap['context_tokens']:
        compression_cfg = cfg.get('context_compression') or {}
        try:
            margin = max(0, int(compression_cfg.get('margin_tokens', MARGIN_TOKENS)))
        except (TypeError, ValueError):
            margin = MARGIN_TOKENS
        available = cap['context_tokens'] - estimated_input - margin
        if available <= 0:
            raise ProviderResponseError('上下文空间不足')
        limits.append(available)
    if limits:
        out[cap['output_parameter']] = min(limits)
    elif cap['requires_output_limit']:
        out[cap['output_parameter']] = cap['compat_output_tokens']
    return out


def is_context_limit_error(exc):
    """True only for explicit provider/context-length rejection messages."""
    parts = [str(exc or '')]
    body = getattr(exc, 'body', None)
    if body:
        parts.append(str(body))
    response = getattr(exc, 'response', None)
    if response is not None:
        try:
            parts.append(str(response.text or ''))
        except Exception:
            pass
    return bool(_CONTEXT_LIMIT.search('\n'.join(parts)))


_THINK_BLOCK = re.compile(r'^\s*<think>(.*?)</think>\s*', re.S | re.I)


def _extract_inline_think(content, reasoning):
    """非流式响应：把 content 开头的内联 <think>…</think> 块并入 reasoning。

    只搬**成对闭合**的块；没有闭合标签的内容原样保留（保守优先：
    误删合法正文比多显示一段思考更糟）。流式路径由 ThinkTextBoundary 处理。
    """
    moved = []
    rest = content or ''
    while True:
        match = _THINK_BLOCK.match(rest)
        if not match:
            break
        moved.append(match[1])
        rest = rest[match.end():]
    if moved:
        thinking = ''.join(moved)
        reasoning = (reasoning + thinking) if reasoning else thinking
    return rest, reasoning


def normalize(data):
    result = dict(content='', reasoning='', tool_calls=[], usage=None, finish_reason=None,
                  reason='invalid_structure', complete=False)
    if not isinstance(data, dict):
        return result
    try:
        choice = data['choices'][0]
        message = choice['message']
        if not isinstance(message, dict):
            return result
        content = message.get('content') or ''
        reasoning = message.get('reasoning_content') or ''
        calls = message.get('tool_calls') or []
        if not isinstance(content, str) or not isinstance(reasoning, str) or not isinstance(calls, list):
            return result
        content, reasoning = _extract_inline_think(content, reasoning)
        usage = data.get('usage')
        safe_usage = {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
            if k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        # Keep only the small, common detail fields needed for shared accounting.
        # Provider-specific signed reasoning blocks remain provider-owned.
        if isinstance(usage, dict):
            for group in ('prompt_tokens_details', 'completion_tokens_details'):
                details = usage.get(group)
                if isinstance(details, dict):
                    allowed = ('cached_tokens',) if group == 'prompt_tokens_details' else ('reasoning_tokens',)
                    clean = {k: details[k] for k in allowed
                             if isinstance(details.get(k), int) and not isinstance(details.get(k), bool)
                             and details[k] >= 0}
                    if clean:
                        safe_usage[group] = clean
        result.update(content=content, reasoning=reasoning, tool_calls=calls,
                      usage=safe_usage or None)
        finish = choice.get('finish_reason')
        result['finish_reason'] = finish if isinstance(finish, str) else None
        if finish in ('length', 'max_tokens'):
            result['reason'] = 'length'
        elif finish in ('content_filter', 'refusal') or message.get('refusal'):
            result['reason'] = 'refusal'
        elif calls:
            result.update(reason='tool_calls', complete=True)
        elif finish not in (None, 'stop', 'end_turn'):
            result['reason'] = 'service_error'
        elif content.strip():
            # Legacy compatible providers sometimes omit finish_reason on JSON
            # responses. Streaming must supply its explicit finish separately.
            result.update(reason='normal', complete=True)
        elif reasoning.strip():
            result['reason'] = 'thinking_only'
        else:
            result['reason'] = 'empty_content'
        return result
    except (KeyError, TypeError, IndexError):
        return result


def assistant_history_message(provider, message, content_override=None, tool_calls=None):
    """Use the provider's replay contract, with a safe standard fallback."""
    hook = getattr(provider, 'assistant_history_message', None)
    if callable(hook):
        result = hook(message, content_override=content_override, tool_calls=tool_calls)
        if isinstance(result, dict) and result.get('role') == 'assistant':
            return result
    message = message if isinstance(message, dict) else {}
    out = {'role': 'assistant', 'content': message.get('content', '')
           if content_override is None else content_override}
    calls = tool_calls if tool_calls is not None else message.get('tool_calls')
    if calls:
        out['tool_calls'] = calls
    return out


ERROR_MESSAGES = {'length': '模型输出被截断，未生成完整结果',
    'empty_content': '模型未返回正文',
    'thinking_only': '模型仅返回思考，未得到正文',
    'invalid_structure': '模型返回结构错误', 'refusal': '模型拒绝生成',
    'connection_lost': '模型连接中断或超时', 'service_error': '模型服务请求失败',
    'auth': '模型服务认证失败（API Key 或权限无效）',
    'network': '无法连接模型服务（网络或网关不可达）',
    'parse': '模型返回内容无法解析',
    'codegen': '生成产物构造失败',
    'static_validation': '生成产物未通过静态校验',
    'draft_save': '草稿保存失败'}

# 失败阶段：每次失败都要能定位到具体环节，界面不得所有失败都显示同一句“创作失败”。
STAGES = ('model_request', 'network', 'auth', 'parse', 'codegen',
          'static_validation', 'draft_save')

_STAGE_OF_REASON = {'invalid_structure': 'parse', 'empty_content': 'model_request',
    'thinking_only': 'model_request', 'length': 'model_request',
    'refusal': 'model_request', 'connection_lost': 'network',
    'auth': 'auth', 'service_error': 'model_request'}

_STAGE_HINTS = {'model_request': '可稍后重试，或换用更稳定的模型/缩短输出',
    'network': '检查网络、代理与模型网关地址后重试',
    'auth': '检查模型 API Key、账号权限与余额',
    'parse': '模型未按契约输出，请重试或更换模型',
    'codegen': '检查生成产物模板与模型输出内容',
    'static_validation': '按校验报告修正后重新生成',
    'draft_save': '检查磁盘空间与目录写权限'}


def stage_of(reason):
    """把底层归一化原因映射到失败阶段（用于界面分阶段提示）。"""
    if reason in STAGES:
        return reason
    return _STAGE_OF_REASON.get(reason or '', 'model_request')


def diagnose(reason, stage=None, *, message=None, error_code=None,
             http_status=None, hint=None, detail=None, log_id=None,
             missing=None, position=None):
    """结构化诊断：阶段 + 原因 + 可选错误码/HTTP 状态 + 建议 + 内部细节。

    仅包含非敏感字段；禁止写入 API Key、Token 或完整系统提示词。
    内部异常链与日志 ID 供开发者排查。
    """
    reason = reason or 'service_error'
    stage = stage or stage_of(reason)
    info = {'class': reason, 'stage': stage, 'reason': reason,
            'message': message or ERROR_MESSAGES.get(reason, '请求未能完成，请检查连接或模型配置'),
            'hint': hint or _STAGE_HINTS.get(stage, '请稍后重试')}
    if error_code is not None:
        info['error_code'] = str(error_code)[:64]
    if http_status is not None:
        try:
            info['http_status'] = int(http_status)
        except (TypeError, ValueError):
            pass
    if missing:
        info['missing'] = [str(m)[:64] for m in missing][:16]
    if position is not None:
        try:
            info['position'] = int(position)
        except (TypeError, ValueError):
            pass
    if detail:
        info['detail'] = str(detail)[:300]
    if log_id:
        info['log_id'] = str(log_id)[:64]
    return info


class Cancelled(Exception):
    """The caller asked to stop; no result may be used or persisted."""


class StopToken:
    """Cross-thread stop signal for one model request.

    ``cancel()`` closes the live HTTP response when one is attached, so a read
    blocked on the socket returns immediately instead of waiting for the next
    token. Providers may already have produced (and billed) tokens upstream —
    closing the connection stops local delivery, it cannot un-send a request.
    """
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._closer = None

    @property
    def requested(self):
        return self._event.is_set()

    def attach(self, closer):
        with self._lock:
            self._closer = closer
            already = self._event.is_set()
        if already:
            self._invoke(closer)

    def detach(self):
        with self._lock:
            self._closer = None

    def cancel(self):
        self._event.set()
        with self._lock:
            closer = self._closer
        if closer:
            self._invoke(closer)

    @staticmethod
    def _invoke(closer):
        try:
            closer()
        except Exception:
            # Fixing up a half-read response must never break the canceller.
            pass


def merge_identifier(current, fragment):
    """Merge a streamed tool id/name without duplicating repeated sends.

    Gateways disagree on identifier streaming: some send the whole name once,
    some repeat it in every delta, a few split it across deltas. Concatenating
    unconditionally corrupts the first two shapes, which is how a valid call
    reaches the dispatcher as an unknown tool name.
    """
    if not fragment:
        return current
    if not current:
        return fragment
    if fragment == current or current.startswith(fragment):
        return current              # repeated send, or a shorter repeat
    if fragment.startswith(current):
        return fragment             # longer repeat: take the fuller value
    return current + fragment       # genuine split fragment


def _merge_identifier(current, fragment):
    return merge_identifier(current, fragment)


def completion_events(provider, payload, timeout=180, stop=None):
    """One request only. Yield deltas then a normalized completion, never retry.

    ``stop`` is an optional StopToken. When it fires the request ends as soon as
    the transport can be interrupted; a non-streaming call can only be stopped
    after the provider responds, and its result is then discarded.
    """
    def _check():
        if stop is not None and stop.requested:
            raise Cancelled('cancelled')
    _check()
    if not provider.supports_stream:
        result = normalize(provider.chat(payload, timeout=timeout))
        result['diagnostics'] = _completion_diagnostics(result, transport='non_stream')
        _check()
        # t：收到这段增量的单调时刻。只用来量"从收到到送出去"的延迟，
        # 不进库、不上线（见 agent_runs._delta）。
        if result['reasoning']:
            yield dict(type='think_delta', text=result['reasoning'], t=time.monotonic())
        if result['content']:
            yield dict(type='text_delta', text=result['content'], t=time.monotonic())
        yield dict(type='completion', result=result)
        return
    content, reasoning, finish, usage = [], [], None, None
    calls = {}
    sse_chunks = 0
    argument_fragments = {}
    # MiMo 等端点把推理以内联 <think> 块混进 content 流；在这里分流，
    # 聊天层与 Agent Run 拿到的 text_delta 就永远不含思考正文。
    think_boundary = ThinkTextBoundary()
    with provider.stream(dict(payload, stream=True, stream_options={'include_usage': True}), timeout=timeout) as response:
        if stop is not None:
            stop.attach(getattr(response, 'close', None) or (lambda: None))
        try:
            _check()
            response.raise_for_status()
            for line in response.iter_lines():
                _check()
                if isinstance(line, bytes):
                    line = line.decode('utf-8')
                if not line or not line.startswith('data:'):
                    continue
                blob = line[5:].strip()
                if blob == '[DONE]':
                    break
                sse_chunks += 1
                try:
                    obj = json.loads(blob)
                    if obj.get('usage'):
                        usage = obj['usage']
                    if not obj.get('choices'):
                        continue
                    choice = obj['choices'][0]
                    delta = choice.get('delta') or {}
                    if choice.get('finish_reason') is not None:
                        finish = choice['finish_reason']
                    for call in delta.get('tool_calls') or []:
                        index = call.get('index')
                        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 128:
                            raise ValueError('invalid tool index')
                        entry = calls.setdefault(index, {'id': '', 'type': 'function',
                            'function': {'name': '', 'arguments': ''}})
                        if call.get('type', 'function') not in ('function', None):
                            raise ValueError('invalid tool type')
                        fn = call.get('function') or {}
                        # Only arguments is a genuine stream fragment. Identifiers are
                        # sent whole by some gateways and repeated in every delta by
                        # others; blind concatenation turns a legal call into an
                        # unknown tool ("read_file" -> "read_fileread_file") and
                        # overruns the call-id length limit.
                        for source, target, key in [(call, entry, 'id'),
                            (fn, entry['function'], 'name'), (fn, entry['function'], 'arguments')]:
                            value = source.get(key)
                            if value is not None:
                                if not isinstance(value, str):
                                    raise ValueError('invalid tool fragment')
                                if key == 'arguments':
                                    target[key] += value
                                    if value:
                                        argument_fragments[index] = argument_fragments.get(index, 0) + 1
                                else:
                                    target[key] = _merge_identifier(target[key], value)
                    value = delta.get('reasoning_content')
                    if value:
                        if not isinstance(value, str):
                            raise ValueError('invalid delta')
                        reasoning.append(value)
                        yield dict(type='think_delta', text=value, t=time.monotonic())
                    value = delta.get('content')
                    if value:
                        if not isinstance(value, str):
                            raise ValueError('invalid delta')
                        for kind, text in think_boundary.feed(value):
                            (reasoning if kind == 'think' else content).append(text)
                            yield dict(type='think_delta' if kind == 'think' else 'text_delta',
                                       text=text, t=time.monotonic())
                except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                    raise ProviderResponseError('流式响应结构错误')
        finally:
            if stop is not None:
                stop.detach()
    _check()
    # 流结束的滞留（半个标签、未闭合的 think 块）在这里收尾。
    for kind, text in think_boundary.feed('', final=True):
        (reasoning if kind == 'think' else content).append(text)
        yield dict(type='think_delta' if kind == 'think' else 'text_delta',
                   text=text, t=time.monotonic())
    result = normalize({'choices': [{'message': {'content': ''.join(content),
        'reasoning_content': ''.join(reasoning),
        'tool_calls': [calls[i] for i in sorted(calls)]}, 'finish_reason': finish}], 'usage': usage})
    if finish is None:
        result.update(reason='connection_lost', complete=False)
    result['diagnostics'] = _completion_diagnostics(result, transport='sse',
        sse_chunks=sse_chunks, argument_fragments=argument_fragments)
    yield dict(type='completion', result=result)


def _completion_diagnostics(result, *, transport, sse_chunks=0, argument_fragments=None):
    """Small, content-free counters for distinguishing truncation from parsing.

    Never include tool names, ids, argument values, paths, or provider headers.
    """
    calls = result.get('tool_calls') or []
    fragments = argument_fragments or {}
    return dict(transport=transport, sse_chunks=int(sse_chunks),
        tool_call_count=len(calls), finish_reason=result.get('finish_reason'),
        response_complete=result.get('finish_reason') is not None,
        tool_arguments=[dict(index=index,
            argument_chars=len(((call.get('function') or {}).get('arguments') or '')),
            argument_fragments=int(fragments.get(index, 0)))
            for index, call in enumerate(calls)])
