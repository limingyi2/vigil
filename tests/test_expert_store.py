# -*- coding: utf-8 -*-
"""专家（expert_store）单元与接入测试：目录包、匹配、绑定、system 叠加、
工具白名单、召唤/卸下 HTTP。不调模型、不联网。"""
import json
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
import paths
import expert_store
import server
from brain import Brain


GOOD_META = {
    "name": "测试专家",
    "description": "单元测试专家",
    "scenarios": ["代码", "报错", "调试"],
    "default_init_prompt": "看看这段",
    "tools": ["search"],
}


def _make_expert(root, key, meta=None, skill="# 专家规则\n\n只做测试。"):
    d = Path(root) / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "expert.json").write_text(
        json.dumps(meta if meta is not None else GOOD_META, ensure_ascii=False),
        encoding='utf-8')
    (d / "SKILL.md").write_text(skill, encoding='utf-8')


@pytest.fixture
def experts(tmp_path, monkeypatch):
    root = tmp_path / 'experts'
    root.mkdir()
    monkeypatch.setattr(expert_store, 'experts_root', lambda: str(root))
    expert_store._CACHE.clear()
    yield root
    expert_store._CACHE.clear()


# --- key 校验 ---

def test_validate_key_rejects_traversal_and_blanks():
    assert expert_store.validate_key('code-expert') == 'code-expert'
    assert expert_store.validate_key('../escape') == ''
    assert expert_store.validate_key('a/b') == ''
    assert expert_store.validate_key('') == ''
    assert expert_store.validate_key('  ') == ''
    assert expert_store.validate_key('x' * 65) == ''


# --- 目录包扫描与加载 ---

def test_list_and_load(experts):
    _make_expert(experts, 'code-expert')
    _make_expert(experts, 'writing', meta={"name": "写作", "scenarios": ["文章"]})
    keys = [e['key'] for e in expert_store.list_experts()]
    assert keys == ['code-expert', 'writing']
    e = expert_store.load_expert('code-expert')
    assert e['name'] == '测试专家'
    assert e['tools'] == ['search']
    assert '专家规则' in e['prompt']
    # include_prompt=False 不读正文
    assert 'prompt' not in expert_store.load_expert('code-expert',
                                                    include_prompt=False)


def test_load_missing_and_broken(experts):
    assert expert_store.load_expert('nope') is None
    (experts / 'bad').mkdir()
    (experts / 'bad' / 'expert.json').write_text('{broken', encoding='utf-8')
    assert expert_store.load_expert('bad') is None
    assert expert_store.list_experts() == []   # 坏包跳过，不炸清单


def test_skill_too_large_rejected(experts):
    _make_expert(experts, 'huge', skill='x' * (expert_store.MAX_SKILL_CHARS + 1))
    assert expert_store.load_expert('huge') is None


def test_cache_invalidates_on_mtime(experts):
    _make_expert(experts, 'k', meta={"name": "旧名"})
    assert expert_store.load_expert('k')['name'] == '旧名'
    import os, time
    p = experts / 'k' / 'expert.json'
    time.sleep(0.02)
    _make_expert(experts, 'k', meta={"name": "新名"})
    os.utime(p, (time.time() + 1, time.time() + 1))
    assert expert_store.load_expert('k')['name'] == '新名'


# --- 绑定 ---

def test_binding_reads_config():
    cfg = {'experts_by_src': {'pc': 'code-expert', 'app': '../bad'}}
    assert expert_store.binding(cfg, 'pc') == 'code-expert'
    assert expert_store.binding(cfg, 'app') == ''      # 非法 key 视为未绑定
    assert expert_store.binding(cfg, 'wechat') == ''
    assert expert_store.binding(None, 'pc') == ''
    assert expert_store.bindings(cfg) == {'pc': 'code-expert'}


def test_binding_survives_runtime_config_reload():
    import runtime
    cfg = {'experts_by_src': {'pc': 'code-expert'}, 'api_key': 'test'}
    assert expert_store.binding(runtime.api_cfg_subset(cfg), 'pc') == 'code-expert'


def test_frozen_defaults_seed_without_overwriting_user_experts(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    defaults = bundle / 'config' / 'experts'
    _make_expert(defaults, 'code-expert', meta={'name': '内置代码'})
    _make_expert(defaults, 'writing-expert', meta={'name': '内置写作'})
    root = tmp_path / 'user-experts'
    _make_expert(root, 'code-expert', meta={'name': '用户代码'})
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    monkeypatch.setattr(paths, 'EXPERTS_DIR', str(root))
    expert_store._CACHE.clear()
    assert expert_store.experts_root() == str(root)
    assert expert_store.load_expert('code-expert')['name'] == '用户代码'
    assert expert_store.load_expert('writing-expert')['name'] == '内置写作'
    expert_store._CACHE.clear()


def test_tool_allowlist_none_means_keep_all(experts):
    _make_expert(experts, 'code-expert', meta=dict(GOOD_META, tools=[]))  # 空 -> 不收窄
    _make_expert(experts, 'strict', meta=dict(GOOD_META, tools=['search', 'weather']))
    assert expert_store.tool_allowlist({'experts_by_src': {'pc': 'code-expert'}}, 'pc') is None
    assert expert_store.tool_allowlist(
        {'experts_by_src': {'pc': 'strict'}}, 'pc') == frozenset({'search', 'weather'})
    assert expert_store.tool_allowlist({'experts_by_src': {}}, 'pc') is None


# --- 场景匹配 ---

def test_match_experts_ranks_by_hits(experts):
    _make_expert(experts, 'code-expert')
    _make_expert(experts, 'writing', meta={"name": "写作",
                                           "scenarios": ["报错", "总结"]})
    ranked = expert_store.match_experts('这段代码报错了，帮我调试')
    assert [m['key'] for m in ranked] == ['code-expert', 'writing']
    assert ranked[0]['score'] == 3
    assert expert_store.match_experts('今天天气不错') == []
    assert expert_store.match_experts('') == []


# --- system 叠加 ---

def test_system_overlay_format(experts):
    _make_expert(experts, 'code-expert')
    txt = expert_store.system_overlay('code-expert')
    assert '【专家模式：测试专家】' in txt
    assert '专家规则' in txt
    assert expert_store.system_overlay('nope') == ''
    assert expert_store.system_overlay('') == ''


# --- Brain 接入：_persona_text_for 带专家叠加 ---

@pytest.fixture
def brain(tmp_path, monkeypatch):
    personas = tmp_path / 'personas'
    personas.mkdir()
    (personas / 'default.json').write_text(json.dumps(
        {'name': '小智', 'personality': '陪伴底线'}), encoding='utf-8')
    cfg = {'persona_file': 'personas/default.json',
           'experts_by_src': {}, 'features': {'tools': True}}
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    monkeypatch.setattr(paths, 'CONFIG_DIR', str(tmp_path))
    import persona_store
    monkeypatch.setattr(persona_store, 'PERSONA_DIR', str(personas))
    root = tmp_path / 'experts'
    root.mkdir()
    monkeypatch.setattr(expert_store, 'experts_root', lambda: str(root))
    expert_store._CACHE.clear()
    b = Brain(None, cfg)
    yield b, root, cfg
    expert_store._CACHE.clear()


def test_brain_persona_text_includes_expert_overlay(brain):
    b, root, cfg = brain
    _make_expert(root, 'code-expert')
    base = b._persona_text_for('pc')
    assert '专家模式' not in base
    cfg['experts_by_src'] = {'pc': 'code-expert'}
    b._ptext_cache = {}
    overlaid = b._persona_text_for('pc')
    assert '专家模式：测试专家' in overlaid
    assert overlaid.startswith(base)      # 人设在前，专家叠加在后
    # 卸下后回到纯人设
    cfg['experts_by_src'] = {}
    b._ptext_cache = {}
    assert b._persona_text_for('pc') == base


# --- tools.all_specs 白名单收窄 ---

def test_all_specs_narrowed_by_expert_allowlist(brain):
    b, root, cfg = brain
    import tools
    _make_expert(root, 'strict', meta=dict(GOOD_META, tools=['weather']))
    cfg['experts_by_src'] = {'pc': 'strict'}
    specs = tools.all_specs(b, 'pc')
    names = {s['function']['name'] for s in specs}
    assert names <= {'weather'}
    cfg['experts_by_src'] = {}
    specs = tools.all_specs(b, 'pc')
    assert specs          # 未绑定专家时不收窄，工具表照常


# --- HTTP：召唤 / 卸下 ---

@pytest.fixture
def core(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    personas = tmp_path / 'personas'
    personas.mkdir()
    (personas / 'alpha.json').write_text(json.dumps(
        {'name': 'alpha', 'personality': 'PROMPT'}), encoding='utf-8')
    experts_dir = tmp_path / 'experts'
    experts_dir.mkdir()
    _make_expert(experts_dir, 'code-expert')
    cfg = {'persona_file': 'personas/alpha.json',
           'features': {'tools': True, 'packages': False}}
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(paths, 'DATA_DIR', str(data))
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(config))
    monkeypatch.setattr(paths, 'CONFIG_DIR', str(tmp_path))
    import persona_store
    monkeypatch.setattr(persona_store, 'PERSONA_DIR', str(personas))
    monkeypatch.setattr(expert_store, 'experts_root', lambda: str(experts_dir))
    expert_store._CACHE.clear()
    monkeypatch.setattr(server, 'api_config', cfg)
    monkeypatch.setattr(server, 'API_CFG', dict(cfg))
    b = Brain(None, cfg)
    monkeypatch.setitem(server._LOCAL, 'brain', b)
    monkeypatch.setattr(server, 'BRAIN_TOKEN', 'expert-test')
    monkeypatch.setattr(b, '_mem_for', lambda src=None: None)
    monkeypatch.setattr(b, 'draft_native', lambda *a, **k: 'REPLY')
    import plugin_host
    monkeypatch.setattr(plugin_host, 'on_turn_in', lambda ctx: '')
    monkeypatch.setattr(plugin_host, 'on_reply', lambda ctx, text: text)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv('VIGIL_PORT', str(port))
    monkeypatch.setenv('VIGIL_BIND_HOST', '127.0.0.1')
    httpd = server.start_remote_api(b)

    def request(path, body=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path),
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer expert-test',
                     'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)
    yield b, request, cfg, config
    httpd.shutdown()
    httpd.server_close()
    expert_store._CACHE.clear()


def test_summon_dismiss_roundtrip(core):
    b, request, cfg, config = core
    status, r = request('/api/expert?src=pc')
    assert status == 200 and r['ok'] is True
    assert [e['key'] for e in r['experts']] == ['code-expert']
    assert r['active'] == ''

    status, r = request('/api/expert/summon',
                        {'key': 'code-expert', 'src': 'pc'})
    assert status == 200 and r['ok'] is True and r['name'] == '测试专家'
    assert r['default_init_prompt'] == '看看这段'
    # 内存与磁盘的绑定都改了
    assert b.api['experts_by_src']['pc'] == 'code-expert'
    assert json.loads(config.read_text(encoding='utf-8'))[
        'experts_by_src']['pc'] == 'code-expert'
    # system 段真的换装了（历史首条跟着 reload_persona 刷新）
    assert '专家模式：测试专家' in b._persona_text_for('pc')

    status, r = request('/api/expert?src=pc')
    assert r['active'] == 'code-expert'

    status, r = request('/api/expert/dismiss', {'src': 'pc'})
    assert status == 200 and r['ok'] is True
    assert 'experts_by_src' not in b.api or \
        b.api['experts_by_src'].get('pc') in (None, '')
    assert '专家模式' not in b._persona_text_for('pc')


def test_summon_rejects_unknown_expert(core):
    _b, request, _cfg, _config = core
    status, r = request('/api/expert/summon', {'key': 'ghost', 'src': 'pc'})
    assert status == 200 and r['ok'] is False


def test_match_endpoint(core):
    _b, request, _cfg, _config = core
    from urllib.parse import quote
    status, r = request('/api/expert/match?text=' + quote('这段代码报错了'))
    assert status == 200 and r['ok'] is True
    assert r['matches'][0]['key'] == 'code-expert'
