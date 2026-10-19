"""Decimal cloud presets and public history status; no model/network calls."""
import copy
import ast
import contextlib
import hmac
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import conversation_context as cc
import history_retrieval as hr
import model_hub
from test_p85_context_history import setup, feed, build


class Brain:
    def __init__(self, api):
        self.api = api
    def _api_snapshot(self):
        return copy.deepcopy(self.api)
    def _provider(self, api):
        class Provider:
            kind = 'ollama' if api.get('model_mode') == 'local' else 'cloud'
            num_ctx = 12288
            num_predict = 2048
        return Provider()


@pytest.mark.parametrize('mode', ['cloud', 'hybrid'])
@pytest.mark.parametrize('tier,trigger,target', [(96000, 72000, 48000), (128000, 96000, 64000), (300000, 225000, 150000)])
def test_exact_decimal_mapping(mode, tier, trigger, target):
    api = cc.set_cloud_tier({'model_mode': mode, 'model_capabilities': {'context_tokens': 1000000}}, tier)
    b = Brain(api)
    info = cc.budgets(b, api['context_compression'])
    assert info['input_budget'] == tier
    assert info['trigger_tokens'] == trigger and info['target_tokens'] == target
    assert info['input_budget'] + info['reserve_out'] + info['margin'] <= 1000000
    assert cc.cloud_policy(api)['effective'] == tier


@pytest.mark.parametrize('capacity,expected,label', [(300000, 300000, '300K'), (200000, 128000, '128K'),
    (100000, 96000, '96K'), (96000, 96000, '96K'), (16384, None, '受限 16,384 Token'),
    (None, None, '保守 8,192 Token')])
def test_actual_capacity_limits_presets(capacity, expected, label):
    api = cc.set_cloud_tier({'model_capabilities': {'context_tokens': capacity}}, 300000)
    policy = cc.cloud_policy(api)
    assert policy['effective'] == expected and policy['label'] == label
    info = cc.budgets(Brain(api), api['context_compression'])
    assert info['input_budget'] + info['reserve_out'] + info['margin'] <= (capacity or 8192)
    assert info['target_tokens'] <= info['trigger_tokens'] <= info['input_budget']


def test_input_ceiling_and_output_reserve_intersection():
    api = cc.set_cloud_tier({'model_capabilities': {
        'context_tokens': 300000, 'input_tokens': 50000, 'max_output_tokens': 8192}}, 300000)
    info = cc.budgets(Brain(api), api['context_compression'])
    assert info['input_budget'] == 50000 - 8192 - 256
    assert info['reserve_out'] == 8192


@pytest.mark.parametrize('tier', [96000, 128000, 300000])
def test_history_evidence_shares_cloud_input_budget(setup, tier):
    b, provider = setup
    provider.kind = 'cloud'
    b.api.update(model_mode='cloud', api_key='fixture', model_capabilities={'context_tokens': 500000})
    b.api['context_compression']['cloud_context_tier'] = tier
    feed(b, 30, {5: '实验 ALPHA 端口 18883 路径 F:/synthetic/alpha.json'})
    messages, info = build(b, '之前实验 ALPHA 的端口和路径是什么')
    assert '18883' in str(messages)
    assert info['retrieval']['method'] == 'bm25'
    assert 0 < info['retrieval']['budget'] <= 2000
    assert info['estimated_input_tokens'] <= info['input_budget'] <= tier
    assert info['estimated_input_tokens'] + info['reserve_out'] + info['margin'] <= 500000


@pytest.mark.parametrize('tier', [96000, 128000, 300000])
def test_cloud_selection_preserves_local_budget_and_config(tmp_path, monkeypatch, tier):
    original = {'model_mode': 'local', 'ollama': {'num_ctx': 12288, 'num_predict': 2048},
                'api_key': 'fixture-key', 'vision': {'model': 'fixture-vl'},
                'context_compression': {'local_work_tokens': 9000, 'local_trigger_tokens': 7500,
                                        'local_target_tokens': 6000, 'cloud_work_tokens': 131072}}
    before = copy.deepcopy(original)
    configured = cc.set_cloud_tier(original, tier)
    assert original == before
    assert configured['ollama'] == original['ollama']
    b = Brain(configured)
    info = cc.budgets(b, configured['context_compression'])
    assert (info['capacity'], info['input_budget'], info['trigger_tokens'], info['target_tokens']) == (12288, 9000, 7500, 6000)
    monkeypatch.setattr(model_hub, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    model_hub.save(configured)
    restored = model_hub.load()
    assert restored == configured and cc.cloud_policy(restored)['selected'] == tier
    assert restored['api_key'] == 'fixture-key' and restored['vision'] == original['vision']


@pytest.mark.parametrize('value', [True, 64000, 131072, 256000, '300K', None])
def test_only_three_valid_choices(value):
    with pytest.raises(ValueError):
        cc.set_cloud_tier({}, value)


def test_model_capabilities_never_follow_another_model_or_endpoint():
    original = {'provider': 'custom', 'api_base': 'https://a.invalid/v1', 'model': 'large',
                'api_key': 'fixture', 'model_capabilities': {'context_tokens': 500000},
                'context_compression': {'cloud_context_tier': 300000}}
    other, _ = model_hub.apply_cfg(original, {'chat': 'small'})
    assert cc.cloud_policy(other)['capacity_known'] is False
    assert 'model_capabilities' not in other
    restored, _ = model_hub.apply_cfg(other, {'chat': 'large'})
    assert cc.cloud_policy(restored)['effective'] == 300000
    endpoint, _ = model_hub.apply_cfg(restored, {'api_base': 'https://b.invalid/v1'})
    assert cc.cloud_policy(endpoint)['capacity_known'] is False
    assert original['model_capabilities'] == {'context_tokens': 500000}


def test_selection_overrides_legacy_cloud_thresholds_without_mutating_local():
    api = cc.set_cloud_tier({'model_capabilities': {'context_tokens': 500000},
        'context_compression': {'cloud_work_tokens': 131072, 'cloud_trigger_tokens': 98304,
                                'cloud_target_tokens': 65536, 'local_work_tokens': 9000}}, 300000)
    assert cc.budgets(Brain(api), api['context_compression'])['input_budget'] == 300000
    assert api['context_compression']['cloud_work_tokens'] == 131072


def test_retrieval_diagnostics_never_probe_or_claim_unverified_vectors(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('status must not call embedding')
    monkeypatch.setattr(hr.Embedder, 'embed', forbidden)
    assert hr.retrieval_status({})['label'] == 'BM25 关键词检索'
    assert hr.retrieval_status({'model_mode': 'local'})['vector_available'] is False
    configured = {'history_retrieval': {'embedding_model': 'fake', 'embedding_base': 'https://example.invalid/v1'}}
    tag = hr.Embedder(configured).tag
    assert hr.retrieval_status(configured)['vector_available'] is None
    failed = hr.retrieval_status(configured, {'embedding_identity': tag, 'vector_reason': 'TimeoutError'})
    assert failed['label'] == 'BM25 关键词检索' and failed['vector_available'] is False
    ready = hr.retrieval_status(configured, {'embedding_identity': tag, 'vector_available': True})
    assert ready['label'] == 'BM25 + 语义向量检索'
    assert '语义' not in hr.retrieval_status({}, {'vector_available': True})['label']


def test_atomic_config_save_failure_keeps_previous_file(tmp_path, monkeypatch):
    file = tmp_path / 'config.json'
    monkeypatch.setattr(model_hub, 'CONFIG_PATH', str(file))
    model_hub.save({'unrelated': 'keep'})
    original = file.read_bytes()
    def fail_replace(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(hr.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        model_hub.save(cc.set_cloud_tier({}, 300000))
    assert file.read_bytes() == original


def test_runtime_whitelist_and_hot_refresh_reach_actual_brain_budgets():
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'brain/runtime.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'api_cfg_subset') or
        (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'API_CFG_KEYS' for t in n.targets))]
    runtime_ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<runtime-subset>', 'exec'), runtime_ns)
    source = {'model_mode': 'cloud', 'ollama': {'num_ctx': 12288},
              'model_capabilities': {'context_tokens': 500000}, 'history_retrieval': {'enabled': False}}
    shared = runtime_ns['api_cfg_subset'](source)
    brain = Brain(shared)
    refresh = next(n for n in ast.parse((root / 'brain/server.py').read_text(encoding='utf-8')).body
                   if isinstance(n, ast.FunctionDef) and n.name == '_refresh_api_cfg')
    ns = {'API_CFG': shared, '_runtime': types.SimpleNamespace(api_cfg_subset=runtime_ns['api_cfg_subset'])}
    exec(compile(ast.Module(body=[refresh], type_ignores=[]), '<refresh>', 'exec'), ns)
    for tier in cc.CLOUD_TIERS:
        ns['_refresh_api_cfg'](cc.set_cloud_tier(source, tier))
        assert brain.api is shared
        assert cc.budgets(brain, brain.api['context_compression'])['input_budget'] == tier
        assert brain.api['history_retrieval']['enabled'] is False
        assert brain.api['ollama']['num_ctx'] == 12288


def test_real_http_save_restart_and_public_status(tmp_path, monkeypatch):
    import paths
    monkeypatch.setenv('VIGIL_HOME', str(tmp_path))
    monkeypatch.setattr(paths, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(model_hub, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    api = {'bind_host': '127.0.0.1', 'provider': 'custom', 'model': 'fixture',
           'api_base': 'https://example.invalid/v1', 'api_key': 'fixture-secret',
           'ollama': {'num_ctx': 12288}, 'model_capabilities': {'context_tokens': 200000}}
    source = Path(__file__).resolve().parents[1] / 'brain/server.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    names = {'start_remote_api', '_auth_ok', '_is_loopback', '_device_path_allowed', '_query_param', '_norm_src'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    constants = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id.startswith('DEVICE_') for t in n.targets)]
    ns = dict(http=http, os=os, json=json, hmac=hmac, threading=threading, time=time,
        api_config=api, API_CFG=api, model_hub=model_hub, MOB_CFG={}, BRAIN_TOKEN='fixture-token',
        MAX_BODY=1024*1024, note_client_ip=lambda ip: None, _device_role=lambda _: 'device',
        _refresh_api_cfg=lambda cfg: None, _stats_today=lambda: {'ok': True},
        errlog=types.SimpleNamespace(log_exc=lambda *a: None))
    exec(compile(ast.Module(body=constants+nodes, type_ignores=[]), str(source), 'exec'), ns)
    monkeypatch.setenv('VIGIL_PORT', '0')
    with contextlib.redirect_stdout(io.StringIO()):
        server = ns['start_remote_api'](None)
    def request(method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None,
                {'Authorization': 'Bearer fixture-token', 'Content-Type': 'application/json'})
            reply = connection.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            connection.close()
    try:
        assert request('POST', '/api/models/context', {'tier': 131072})[0] == 400
        assert not Path(model_hub.CONFIG_PATH).exists()
        status, result = request('POST', '/api/models/context', {'tier': 300000})
        assert status == 200 and result['context_window']['effective'] == 128000
        saved = model_hub.load()
        assert saved['context_compression']['cloud_context_tier'] == 300000
        assert saved['ollama'] == {'num_ctx': 12288} and saved['api_key'] == 'fixture-secret'
        status, result = request('GET', '/api/models/saved')
        assert status == 200 and result['context_window']['label'] == '128K'
        assert result['history_retrieval']['label'] == 'BM25 关键词检索'
        assert 'fixture-secret' not in json.dumps(result)
        assert request('GET', '/api/stats')[1]['history_retrieval']['label'] == 'BM25 关键词检索'
        api.clear()
        api.update(model_hub.load())  # simulate a server reloading the saved configuration
        assert request('GET', '/api/models/saved')[1]['context_window']['selected'] == 300000
    finally:
        server.shutdown()
        server.server_close()
