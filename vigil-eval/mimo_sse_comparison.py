"""Replay identical fake SSE against dev's old read-only loop and current code.

No model/service requests. Reuses the wire-level regression harness so both
paths consume completion_events(), rather than mocked normalized completions.
"""
import ast
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'brain'))
import brain
import model_providers.execution as execution
from test_readonly_mimo_protocol import harness, text_chunks, xml

BASELINE = 'ac09d7eaf2f07011d945f0e3e5740988bcb829f4'


def old_function(path, name, namespace, class_name=None):
    source = subprocess.check_output(['git', 'show', BASELINE + ':' + path], cwd=ROOT).decode('utf-8')
    tree = ast.parse(source)
    nodes = tree.body
    if class_name:
        nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name).body
    function = next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[function], type_ignores=[])
    scope = dict(namespace)
    exec(compile(module, BASELINE + ':' + path, 'exec'), scope)
    return scope[name]


baseline_loop = old_function('brain/brain.py', '_stream_readonly_tool_chat', vars(brain), 'Brain')
baseline_normalize = old_function('brain/model_providers/execution.py', 'normalize', vars(execution))
current_loop, current_normalize = brain.Brain._stream_readonly_tool_chat, execution.normalize
native = {'tool_calls': [{'index': 0, 'id': 'native', 'type': 'function', 'function': {
    'name': 'search', 'arguments': '{"query":"native"}'}}]}
mixed = {'tool_calls': [{'index': 0, 'id': 'stub', 'type': 'function', 'function': {
    'name': 'search', 'arguments': '{"query": '}}]}
cases = {
    'xml_cross_chunk': [text_chunks(xml(), True), text_chunks('查询完成。')],
    'native_fc': [[native], text_chunks('查询完成。')],
    'mimo_mixed_response': [[mixed] + text_chunks(xml(), True), text_chunks('查询完成。')],
}
report = {'baseline': BASELINE, 'model_requests': 0, 'service_requests': 0, 'cases': {}}
for name, rounds in cases.items():
    results = {}
    for label, loop, normalize in [('before', baseline_loop, baseline_normalize),
                                   ('after', current_loop, current_normalize)]:
        with patch.object(brain.Brain, '_stream_readonly_tool_chat', loop), \
             patch.object(execution, 'normalize', normalize), harness(rounds) as h:
            events = h.run()
            results[label] = {
                'fixture_model_requests': len(h.provider.payloads),
                'fixture_tool_calls': len(h.calls),
                'sse_xml_leaked': '<tool_call' in ''.join(event.get('text', '')
                    for event in events if event['type'] == 'text'),
                'history_xml_leaked': any('<tool_call' in answer for _user, answer in h.history),
                'terminal_type': events[-1]['type'],
                'terminal_class': events[-1].get('class'),
                'final_text': events[-1].get('text'),
            }
    assert results['after']['fixture_tool_calls'] == 1
    assert results['after']['terminal_type'] == 'done'
    assert not results['after']['sse_xml_leaked']
    assert not results['after']['history_xml_leaked']
    report['cases'][name] = results

target = ROOT / 'docs/evaluation/mimo-sse-before-after.json'
target.write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + chr(10)).encode('utf-8'))
print(json.dumps(report, ensure_ascii=False))
