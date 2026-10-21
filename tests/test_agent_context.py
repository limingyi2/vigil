import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))


def test_shared_usage_prefers_provider_and_marks_estimates():
    from agent_context import usage_record

    actual = usage_record({'prompt_tokens': 120, 'completion_tokens': 30,
                           'prompt_tokens_details': {'cached_tokens': 40},
                           'completion_tokens_details': {'reasoning_tokens': 8}}, 'x', 'y')
    assert actual == {'input_tokens': 120, 'output_tokens': 30, 'cached_tokens': 40,
                      'reasoning_tokens': 8, 'estimated_input': False,
                      'estimated_output': False, 'estimated': False, 'source': 'provider'}
    estimate = usage_record({'prompt_tokens': 120}, 'some input', '输出')
    assert estimate['estimated'] is True
    assert estimate['source'] == 'estimate'
    assert estimate['input_tokens'] == 120
    assert estimate['output_tokens'] > 0


def test_compaction_uses_token_budget_and_preserves_tool_call_pairs():
    from agent_context import compact_messages

    messages = [
        {'role': 'system', 'content': 'task goal'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'call-1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'x' * 3000},
        {'role': 'user', 'content': 'current question'},
        {'role': 'assistant', 'content': 'latest answer'}]
    original = [dict(m) for m in messages]
    compacted, stats = compact_messages(messages, 300, keep_recent=2, stub_chars=120)
    assert stats['saved_tokens'] > 0
    assert compacted[1].get('tool_calls') == messages[1]['tool_calls']
    assert compacted[2].get('tool_call_id') == 'call-1'
    assert len(messages[2]['content']) == len(original[2]['content'])
    assert compacted[0]['content'] == 'task goal'


def test_conversation_and_agent_share_the_same_estimator():
    import agent_context
    import conversation_context
    assert conversation_context.estimate_tokens('中文 hello') == agent_context.estimate_tokens('中文 hello')


def test_provider_normalize_keeps_common_usage_details_only():
    from model_providers.execution import normalize
    out = normalize({'usage': {'prompt_tokens': 50, 'completion_tokens': 12,
        'prompt_tokens_details': {'cached_tokens': 20, 'private': 'drop'},
        'completion_tokens_details': {'reasoning_tokens': 4}},
        'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
    assert out['usage'] == {'prompt_tokens': 50, 'completion_tokens': 12,
        'prompt_tokens_details': {'cached_tokens': 20},
        'completion_tokens_details': {'reasoning_tokens': 4}}


def test_reasoning_history_replay_is_explicit_and_provider_scoped():
    from model_providers.cloud import CloudProvider
    from model_providers.ollama import OllamaProvider

    response = {'content': 'tool call', 'reasoning': 'private provider reasoning',
                'tool_calls': [{'id': 'c1', 'type': 'function',
                    'function': {'name': 'read', 'arguments': '{}'}}]}
    default = CloudProvider({'provider': 'custom', 'model': 'm'})
    replay = default.assistant_history_message(response)
    assert 'reasoning_content' not in replay
    assert replay['tool_calls'] == response['tool_calls']

    opted_in = CloudProvider({'provider': 'custom', 'model': 'm',
        'model_capabilities': {'reasoning_history_field': 'reasoning_content'}})
    replay = opted_in.assistant_history_message(response)
    assert replay['reasoning_content'] == response['reasoning']

    local = OllamaProvider({'model': 'm', 'think': True})
    replay = local.assistant_history_message(response)
    assert 'reasoning_content' not in replay
