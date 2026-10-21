import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p13_metrics import render, summarize


def test_metrics_preserve_unknowns_and_compare_by_model_and_variant():
    records = [
        {'task_id': 'F01', 'model_id': 'm1', 'variant': 'before', 'success': False,
         'confirmed_defects': 1, 'false_positive_defects': 1, 'repair_attempted': True,
         'repair_verified': False, 'tool_errors': 1, 'context_overflow': True,
         'resume_expected': True, 'resume_succeeded': False, 'provider_protocol': 'openai-chat'},
        {'task_id': 'F01', 'model_id': 'm1', 'variant': 'after', 'success': True,
         'confirmed_defects': 1, 'false_positive_defects': 0, 'repair_attempted': True,
         'repair_verified': True, 'tool_errors': 0, 'context_overflow': False,
         'resume_expected': True, 'resume_succeeded': True, 'output_tokens': 100,
         'elapsed_seconds': 2.5, 'provider_protocol': 'ollama-chat'},
    ]
    got = summarize(records)['m1']
    assert got['before']['defect_precision'] == 0.5
    assert got['after']['defect_precision'] == 1.0
    assert got['before']['mean_output_tokens'] is None
    assert got['after']['mean_output_tokens'] == 100
    assert 'NOT RUN' in render([])
    assert 'openai-chat' in render(records) and 'ollama-chat' in render(records)
