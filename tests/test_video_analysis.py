"""Offline standalone indexing contracts; fixture models do not prove accuracy."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'brain'))
from video_analysis import VideoContentIndex, VideoAnalysisError, VisionModel
import video_analysis


class Frame:
    def __init__(self, timestamp):
        self.jpeg = b'fixture'
        data = dict(timestamp=timestamp, sha256='fixture-frame-%s' % timestamp)
        self.record = SimpleNamespace(timestamp=timestamp, model_dump=lambda: data)


class Video:
    def __init__(self, path, duration=90):
        self.path = path
        self.duration = duration
        self.decoded = []

    def sample_interval(self, start, end, count):
        self.decoded.append((start, end, count))
        return [Frame(start + (i + .5) * (end - start) / count) for i in range(count)]


class Model:
    identity = 'fixture-vision-model'
    is_fake = True

    def __init__(self):
        self.calls = []
        self.bad_evidence = False
        self.fail_on = None

    def complete(self, prompt, frames):
        self.calls.append((prompt, frames))
        if len(self.calls) == self.fail_on:
            raise RuntimeError('model down')
        if prompt.startswith('描述'):
            # Synthetic descriptions are fixtures, not inferred video content.
            return {'description': '人物把金属开门物品放进蓝色抽屉' if 30 <= frames[0].record.timestamp < 60 else '空桌子'}
        if '片段：' in prompt:
            items = json.loads(prompt.split('片段：')[1])
            return {'ids': [s['id'] for s in items if '开门物品' in s['description']][:2]}
        if '候选：' in prompt:
            return {'ids': [s['id'] for s in json.loads(prompt.split('候选：')[1])][:2]}
        return {'status': 'answered', 'answer': '放进蓝色抽屉',
                'timestamps': [9999 if self.bad_evidence else frames[0].record.timestamp],
                'uncertainties': ['采样间隔内可能漏掉短暂事件']}


@pytest.fixture
def index(tmp_path):
    video = tmp_path / 'clip.webm'
    video.write_bytes(b'fixture video bytes')
    return VideoContentIndex(Video(video), Model(), tmp_path / 'cache')


def test_full_timeline_index_and_model_selection_review_original_frames(index):
    status = index.build()
    assert status['complete'] and status['timeline_coverage'] == 1
    assert status['sampled_only'] and status['is_fake'] and not status['embeddings']
    assert index.video.decoded == [(0, 30, 3), (30, 60, 3), (60, 90, 3)]
    result = index.ask('钥匙放在哪里？')
    assert result['answer'] == '放进蓝色抽屉'
    assert result['candidates'][0]['start'] == 30
    assert index.video.decoded[-1] == (30, 60, 4)
    assert result['evidence'][0]['timestamp'] == 33.75


def test_resume_uses_cached_descriptions_and_does_not_claim_partial_coverage(index):
    with pytest.raises(VideoAnalysisError, match='预算'):
        index.build(max_new_segments=1)
    assert index.status()['indexed_segments'] == 1
    with pytest.raises(VideoAnalysisError, match='未完成'):
        index.search('钥匙')
    reopened = VideoContentIndex(index.video, index.model, index.path.parent)
    reopened.build()
    assert len(index.model.calls) == 3
    reopened.build()
    assert len(index.model.calls) == 3


def test_model_failure_is_an_error_not_motion_or_keyword_fallback(index):
    index.model.fail_on = 2
    with pytest.raises(VideoAnalysisError, match='失败'):
        index.build()
    assert len(index.model.calls) == 2 and index.status()['indexed_segments'] == 1


def test_no_relevant_candidate_is_insufficient_evidence_not_a_fabricated_answer(index, monkeypatch):
    index.build()
    monkeypatch.setattr(index.model, 'complete', lambda *args: {'ids': []})
    result = index.ask('不在索引里的内容')
    assert result['status'] == 'insufficient_evidence' and result['answer'] == '' and not result['evidence']


def test_unobserved_evidence_and_foreign_candidate_ids_are_rejected(index, monkeypatch):
    index.build()
    index.model.bad_evidence = True
    with pytest.raises(VideoAnalysisError, match='时间戳'):
        index.ask('钥匙在哪里')
    monkeypatch.setattr(index.model, 'complete', lambda *args: {'ids': [999]})
    with pytest.raises(VideoAnalysisError, match='越界'):
        index.search('钥匙')


def test_source_change_cancellation_and_model_change_are_explicit(index):
    with pytest.raises(VideoAnalysisError, match='取消'):
        index.build(cancelled=lambda: True)
    assert not index.model.calls
    other = Model()
    other.identity = 'other-model'
    assert VideoContentIndex(index.video, other, index.path.parent).path != index.path
    index.video.path.write_bytes(b'changed video')
    with pytest.raises(VideoAnalysisError, match='已改变'):
        index.build()


def test_test_and_real_cache_namespaces_do_not_mix(index):
    other = Model()
    other.is_fake = False
    assert VideoContentIndex(index.video, other, index.path.parent).path != index.path


def test_ego_adapter_reuses_observation_protocol_and_never_calls_old_motion_search(index):
    index.build()
    base = SimpleNamespace(video_sha256=index.digest, summary=lambda: {'duration':90},
                           search_windows=lambda *args: pytest.fail('old search must not be called'))
    session = SimpleNamespace(active=False, video_sha256=index.digest, index=base)
    index.attach(session)
    rows = session.index.search_windows('钥匙在哪里', 2)
    assert rows[0]['start'] == 30 and rows[0]['source'] == 'vision_model_selection'
    assert not rows[0]['evidence_verified']
    assert session.index.summary()['content_index']['complete']
    assert session.index.video_sha256 == index.digest
    index.model.fail_on = len(index.model.calls) + 1
    with pytest.raises(RuntimeError):
        session.index.search_windows('钥匙在哪里')


def test_settings_save_only_selected_model_and_never_expose_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(video_analysis, 'settings_path', lambda: tmp_path / 'settings.json')
    cfg = {'api_key': 'not-for-client', 'model': 'vision-test', 'provider': 'custom', 'api_base': 'http://127.0.0.1:19999/v1'}
    value = video_analysis.settings(cfg)
    assert 'not-for-client' not in json.dumps(value)
    assert value['embeddings'] is False
    selected = value['models'][-1]['id']
    video_analysis.save_settings(cfg, {'model_id': selected})
    assert video_analysis.settings(cfg)['model_id'] == selected
    with pytest.raises(ValueError):
        video_analysis.save_settings(cfg, {'model_id': 'unknown'})


def test_model_calls_require_authorization_and_enforce_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(video_analysis, 'settings_path', lambda: tmp_path / 'settings.json')
    with pytest.raises(VideoAnalysisError, match='明确允许'):
        VisionModel({})
    import model_providers
    calls = []
    provider = SimpleNamespace(ready=lambda: True, chat=lambda payload, **kwargs:
        calls.append(payload) or {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ids":[]}'}}]})
    monkeypatch.setattr(model_providers, 'get_provider', lambda cfg: provider)
    model = VisionModel({}, authorized=True, max_calls=1, audit_path=tmp_path / 'calls.jsonl')
    assert model.complete('question', []) == {'ids': []}
    with pytest.raises(VideoAnalysisError, match='预算'):
        model.complete('question', [])
    assert len(calls) == 1
    states = [json.loads(line)['state'] for line in (tmp_path / 'calls.jsonl').read_text().splitlines()]
    assert states == ['reserved', 'returned']


def test_settings_http_route_checks_admin_and_validates_model(tmp_path, monkeypatch):
    import ast
    import contextlib
    monkeypatch.setattr(video_analysis, 'settings_path', lambda: tmp_path / 'settings.json')
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'brain/server.py').read_text(encoding='utf-8'))
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Compare)
                 and any(isinstance(c, ast.Constant) and c.value == '/api/video-analysis/settings' for c in node.test.comparators)
                 and any(isinstance(n, ast.Attribute) and n.attr == '_is_admin' for n in ast.walk(node)))
    fn = ast.FunctionDef(name='handler', args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='self'), ast.arg(arg='body')],
        kwonlyargs=[], kw_defaults=[], defaults=[]), body=route.body, decorator_list=[], type_params=[])
    namespace = dict(API_CFG={}, _chat_lock=contextlib.nullcontext)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<video-settings-route>', 'exec'), namespace)
    calls = []
    handler = SimpleNamespace(_is_admin=lambda: False, _json=lambda *args: calls.append(args))
    namespace['handler'](handler, {})
    assert calls[-1][0] == 403 and not (tmp_path / 'settings.json').exists()
    handler._is_admin = lambda: True
    namespace['handler'](handler, {'model_id':'unknown'})
    assert calls[-1][0] == 400
    selected = video_analysis.settings({})['model_id']
    namespace['handler'](handler, {'model_id':selected})
    assert calls[-1][0] == 200 and calls[-1][1]['embeddings'] is False
