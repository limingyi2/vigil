"""Model limits and completion semantics shared by chat and Creator.

No credentials are stored in the capability cache. Unknown gateways never
inherit limits solely because their model ID resembles an official model.
"""
import copy
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .base import ProviderResponseError

_CACHE = {}
_LOCK = threading.Lock()
PARAMETERS = ('max_tokens', 'max_completion_tokens', 'max_output_tokens')


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
        'context_tokens', 'output_parameter', 'requires_output_limit', 'compat_output_tokens')}
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
    with _LOCK:
        # Bound cache growth when models/endpoints are frequently changed.
        if len(_CACHE) >= 256:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = dict(signature=signature, record=record)
    return copy.deepcopy(record)


def prepare(payload, cfg, *, step_tokens=None, remaining_tokens=None):
    if remaining_tokens is not None and (not _positive(remaining_tokens)):
        raise ProviderResponseError('剩余输出预算不足')
    out = dict(payload)
    for param in PARAMETERS:
        out.pop(param, None)
    cap = capabilities(cfg)
    limits = [v for v in (cap['max_output_tokens'], _positive(step_tokens),
                         _positive(remaining_tokens)) if v is not None]
    # Conservative estimate, explicitly not represented as exact token usage.
    estimated_input = len(json.dumps(out.get('messages', []), ensure_ascii=False).encode('utf-8'))
    if cap['input_tokens'] and estimated_input > cap['input_tokens']:
        raise ProviderResponseError('输入超过已配置模型容量')
    if cap['context_tokens']:
        available = cap['context_tokens'] - estimated_input
        if available <= 0:
            raise ProviderResponseError('上下文空间不足')
        limits.append(available)
    if limits:
        out[cap['output_parameter']] = min(limits)
    elif cap['requires_output_limit']:
        out[cap['output_parameter']] = cap['compat_output_tokens']
    return out


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
        usage = data.get('usage')
        safe_usage = {k: v for k, v in (usage.items() if isinstance(usage, dict) else [])
            if k in ('prompt_tokens', 'completion_tokens', 'total_tokens') and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        result.update(content=content, reasoning=reasoning, tool_calls=calls,
                      usage=safe_usage or None)
        finish = choice.get('finish_reason')
        result['finish_reason'] = finish if isinstance(finish, str) else None
        if finish in ('length', 'max_tokens'):
            result['reason'] = 'length'
        elif finish in ('content_filter', 'refusal') or message.get('refusal'):
            result['reason'] = 'refusal'
        elif calls:
            result['reason'] = 'tool_calls'
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


ERROR_MESSAGES = {'length': '模型输出被截断，未生成完整结果',
    'empty_content': '模型未返回正文',
    'thinking_only': '模型仅返回思考，未得到正文',
    'invalid_structure': '模型返回结构错误', 'refusal': '模型拒绝生成',
    'connection_lost': '模型连接中断或超时', 'service_error': '模型服务请求失败'}


def completion_events(provider, payload, timeout=180):
    """One request only. Yield deltas then a normalized completion, never retry."""
    if not provider.supports_stream:
        result = normalize(provider.chat(payload, timeout=timeout))
        if result['reasoning']:
            yield dict(type='think_delta', text=result['reasoning'])
        if result['content']:
            yield dict(type='text_delta', text=result['content'])
        yield dict(type='completion', result=result)
        return
    content, reasoning, finish, usage = [], [], None, None
    with provider.stream(dict(payload, stream=True, stream_options={'include_usage': True}), timeout=timeout) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if not line or not line.startswith('data:'):
                continue
            blob = line[5:].strip()
            if blob == '[DONE]':
                break
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
                for field, buffer, kind in [('content', content, 'text_delta'),
                    ('reasoning_content', reasoning, 'think_delta')]:
                    value = delta.get(field)
                    if value:
                        if not isinstance(value, str):
                            raise ValueError('invalid delta')
                        buffer.append(value)
                        yield dict(type=kind, text=value)
            except (ValueError, TypeError, KeyError, IndexError):
                raise ProviderResponseError('流式响应结构错误')
    result = normalize({'choices': [{'message': {'content': ''.join(content),
        'reasoning_content': ''.join(reasoning)}, 'finish_reason': finish}], 'usage': usage})
    if finish is None:
        result.update(reason='connection_lost', complete=False)
    yield dict(type='completion', result=result)
