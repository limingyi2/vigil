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


# --- 绑定：会话级（P27） ---
#
# 以前绑定在 experts_by_src[src] 上，PC 只有一个渠道 => 等于全局。
# 现在落在 conversations.expert，一个会话一份。

def _new_conv(owner='o', src='pc', persona='p', expert=None):
    import conversations
    return conversations.create(owner, src, persona, None, expert)


def test_binding_is_per_conversation():
    a = _new_conv(expert='code-expert')
    b = _new_conv()
    assert expert_store.binding_for(a['id']) == 'code-expert'
    assert expert_store.binding_for(b['id']) == ''
    # 两个会话各绑各的，互不串味
    expert_store.bind(b['id'], 'writing-expert')
    assert expert_store.binding_for(a['id']) == 'code-expert'
    assert expert_store.binding_for(b['id']) == 'writing-expert'
    # 卸下只影响自己
    expert_store.bind(a['id'], '')
    assert expert_store.binding_for(a['id']) == ''
    assert expert_store.binding_for(b['id']) == 'writing-expert'


def test_binding_rejects_bad_conversation_id():
    assert expert_store.binding_for('') == ''
    assert expert_store.binding_for('not-a-uuid') == ''
    assert expert_store.binding_for(None) == ''
    assert expert_store.bind('../../etc', 'code-expert') is None


def test_runtime_no_longer_carries_experts_by_src():
    """旧配置键已下线：配置刷新不该再把它搬进内存（搬了就会有人去读）。"""
    import runtime
    assert 'experts_by_src' not in runtime.API_CFG_KEYS
    assert 'experts_by_src' not in runtime.api_cfg_subset(
        {'experts_by_src': {'pc': 'code-expert'}, 'api_key': 'k'})


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
    loose = _new_conv(expert='code-expert')
    strict = _new_conv(expert='strict')
    plain = _new_conv()
    assert expert_store.tool_allowlist(loose['id']) is None
    assert expert_store.tool_allowlist(strict['id']) == frozenset({'search', 'weather'})
    assert expert_store.tool_allowlist(plain['id']) is None
    assert expert_store.tool_allowlist('') is None


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


# --- 资源包与技能（专家不再只是一段提示词） ---

def test_resource_pack_is_listed_not_inlined(experts):
    _make_expert(experts, 'code-expert')
    ref = experts / 'code-expert' / 'references'
    ref.mkdir()
    (ref / '风格.md').write_text('这是一份很长的样稿。' * 500, encoding='utf-8')
    txt = expert_store.system_overlay('code-expert')
    assert '【专家资源包：测试专家】' in txt
    assert 'references/风格.md' in txt
    # 列清单而不是塞正文：完整路径要给（模型真去读），正文不进来
    assert str(ref / '风格.md') in txt
    assert '这是一份很长的样稿' not in txt
    listed = [e for e in expert_store.list_experts() if e['key'] == 'code-expert'][0]
    assert listed['resources'] == 1


def test_icon_field_is_validated_and_listed(experts):
    """icon 只收 [a-z0-9-] 且必须是 sprite 名；非法值丢空由前端退回首字。"""
    _make_expert(experts, 'ok', meta=dict(GOOD_META, icon='Term'))
    _make_expert(experts, 'bad', meta=dict(GOOD_META, icon='../etc/passwd'))
    _make_expert(experts, 'none')
    assert expert_store.load_expert('ok', include_prompt=False)['icon'] == 'term'
    assert expert_store.load_expert('bad', include_prompt=False)['icon'] == ''
    assert expert_store.load_expert('none', include_prompt=False)['icon'] == ''
    # 清单页要带上 icon：会话列表和输入框芯片都靠它画徽标
    listed = {e['key']: e['icon'] for e in expert_store.list_experts()}
    assert listed == {'ok': 'term', 'bad': '', 'none': ''}
    # 界面不再渲染 emoji 头像，但老包带 avatar 不能炸
    _make_expert(experts, 'legacy', meta=dict(GOOD_META, avatar='✍️'))
    assert expert_store.load_expert('legacy', include_prompt=False)['avatar'] == '✍️'


def test_declared_files_and_skills_are_listed(experts):
    _make_expert(experts, 'k', meta={
        "name": "带包专家",
        "files": ["templates/weekly.md", "missing.md"],
        "skills": ["data-clean", {"id": "pdf", "name": "PDF 工具", "desc": "拆页与抽表"}],
    })
    (experts / 'k' / 'templates').mkdir()
    (experts / 'k' / 'templates' / 'weekly.md').write_text('周报', encoding='utf-8')
    txt = expert_store.system_overlay('k')
    assert 'templates/weekly.md' in txt
    assert 'missing.md' not in txt            # 声明了但不存在 -> 不列，不骗模型
    assert '【本专家带的技能】' in txt
    assert 'PDF 工具' in txt and 'data-clean' in txt
    assert 'skill_read' in txt


def test_absent_resource_dirs_add_nothing(experts):
    """没有 references/ 的专家包，overlay 与加资源包之前完全一致。"""
    _make_expert(experts, 'plain')
    txt = expert_store.system_overlay('plain')
    assert '资源包' not in txt and '带的技能' not in txt


# --- Brain 接入：_persona_text_for 带专家叠加 ---

@pytest.fixture
def brain(tmp_path, monkeypatch):
    personas = tmp_path / 'personas'
    personas.mkdir()
    (personas / 'default.json').write_text(json.dumps(
        {'name': '小智', 'personality': '陪伴底线'}), encoding='utf-8')
    cfg = {'persona_file': 'personas/default.json',
           'features': {'tools': True}}
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
    cid = _new_conv()['id']
    base = b._persona_text_for('pc', cid)
    assert '专家模式' not in base
    expert_store.bind(cid, 'code-expert')
    b._ptext_cache = {}
    overlaid = b._persona_text_for('pc', cid)
    assert '专家模式：测试专家' in overlaid
    assert overlaid.startswith(base)      # 人设在前，专家叠加在后
    # 卸下后回到纯人设
    expert_store.bind(cid, '')
    b._ptext_cache = {}
    assert b._persona_text_for('pc', cid) == base


def test_brain_persona_text_is_isolated_between_conversations(brain):
    """会话级隔离的核心断言：A 召唤了专家，B 里的 system 段必须还是纯人设。

    这是从渠道级搬过来的**唯一理由** —— 渠道级在 PC 上等于全局，
    同一个 src 下所有对话共用一份 system 段，A 换装 B 跟着变。
    """
    b, root, cfg = brain
    _make_expert(root, 'code-expert')
    conv_a = _new_conv()['id']
    conv_b = _new_conv()['id']
    plain = b._persona_text_for('pc', conv_b)
    expert_store.bind(conv_a, 'code-expert')
    b._ptext_cache = {}
    assert '专家模式：测试专家' in b._persona_text_for('pc', conv_a)
    assert b._persona_text_for('pc', conv_b) == plain
    # 历史首条也各走各的
    assert '专家模式' not in b._hist('pc', conv_b)[0]['content']
    assert '专家模式' in b._hist('pc', conv_a)[0]['content']


# --- tools.all_specs 白名单收窄 ---

def test_all_specs_narrowed_by_expert_allowlist(brain):
    b, root, cfg = brain
    import tools
    _make_expert(root, 'strict', meta=dict(GOOD_META, tools=['weather']))
    cid = _new_conv(expert='strict')['id']
    b.use_src('pc', cid)
    specs = tools.all_specs(b, 'pc')
    names = {s['function']['name'] for s in specs}
    assert names <= {'weather'}
    # 换到没绑专家的会话：工具表照常（收窄只对那一个会话生效）
    b.use_src('pc', _new_conv()['id'])
    specs = tools.all_specs(b, 'pc')
    assert specs


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


def test_summon_creates_session_and_binds_expert(core):
    b, request, cfg, config = core
    status, r = request('/api/expert?src=pc')
    assert status == 200 and r['ok'] is True
    assert [e['key'] for e in r['experts']] == ['code-expert']
    assert r['active'] == ''
    assert r['conversation_id'] is None

    # 召唤 = 新开一个会话 + 在这个会话上绑定专家
    status, r = request('/api/expert/summon',
                        {'key': 'code-expert', 'src': 'pc', 'new_session': True})
    assert status == 200 and r['ok'] is True and r['name'] == '测试专家'
    assert r['default_init_prompt'] == '看看这段'
    assert r['created'] is True
    cid = r['conversation_id']
    assert cid

    import conversations
    assert conversations.expert_of_id(cid) == 'code-expert'
    # 绑定不再写配置文件
    assert 'experts_by_src' not in json.loads(config.read_text(encoding='utf-8'))
    # system 段真的换装了（历史首条跟着 reload_persona 刷新）
    assert '专家模式：测试专家' in b._persona_text_for('pc', cid)

    status, r = request('/api/expert?src=pc&conversation_id=' + cid)
    assert r['active'] == 'code-expert'
    assert r['active_name'] == '测试专家'

    # 另一个会话完全不受影响（会话级隔离）
    other = _new_conv()['id']
    assert '专家模式' not in b._persona_text_for('pc', other)

    status, r = request('/api/expert/dismiss', {'src': 'pc', 'conversation_id': cid})
    assert status == 200 and r['ok'] is True
    assert conversations.expert_of_id(cid) == ''
    assert '专家模式' not in b._persona_text_for('pc', cid)


def test_summon_without_new_session_binds_current_conversation(core):
    """composer 里的实时推荐走这条：绑在正在写的那个会话上，不新开。"""
    b, request, cfg, config = core
    import conversations
    # 走接口建会话：owner 由 identity 推导，手写 create() 拿到的行不属于同一个 owner
    status, r = request('/api/conversations', {'src': 'pc'})
    assert status in (200, 201) and r['ok'] is True
    real = r['item']['id']

    status, r = request('/api/expert/summon',
                        {'key': 'code-expert', 'src': 'pc', 'conversation_id': real})
    assert status == 200 and r['ok'] is True
    assert r['created'] is False
    assert r['conversation_id'] == real
    assert conversations.expert_of_id(real) == 'code-expert'

    # 会话列表里带出绑定与名字，且空会话不会因为"还没内容"被藏掉
    status, r = request('/api/conversations?src=pc')
    assert status == 200 and r['ok'] is True
    item = next(i for i in r['items'] if i['id'] == real)
    assert item['expert'] == 'code-expert'
    assert item['expert_name'] == '测试专家'


def test_summon_rejects_unknown_expert(core):
    _b, request, _cfg, _config = core
    status, r = request('/api/expert/summon', {'key': 'ghost', 'src': 'pc'})
    assert status == 200 and r['ok'] is False


def test_dismiss_rejects_missing_conversation(core):
    _b, request, _cfg, _config = core
    status, r = request('/api/expert/dismiss',
                        {'src': 'pc', 'conversation_id': '22222222-2222-4222-8222-222222222222'})
    assert status == 200 and r['ok'] is False


def test_match_endpoint(core):
    _b, request, _cfg, _config = core
    from urllib.parse import quote
    status, r = request('/api/expert/match?text=' + quote('这段代码报错了'))
    assert status == 200 and r['ok'] is True
    assert r['matches'][0]['key'] == 'code-expert'
