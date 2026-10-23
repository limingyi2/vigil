"""The background agenda refresh remains independent of retired recap jobs."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))
import jobs


def test_agenda_scopes_cover_channels_and_personas():
    class Brain:
        def persona_key_of(self, src):
            return 'persona-' + src

    assert jobs._agenda_scopes(Brain()) == [
        ('persona-wechat', 'wechat'),
        ('persona-wechat_group', 'wechat_group'),
        ('persona-pc', 'pc'),
    ]


def test_agenda_loop_refreshes_all_scopes_without_recap(monkeypatch):
    import agenda

    refreshed = []
    sleeps = []

    class Brain:
        api = {}

        def persona_key_of(self, src):
            return 'persona-' + src

    def sleep(seconds):
        sleeps.append(seconds)
        if seconds == 1200:
            raise RuntimeError('stop test loop')

    monkeypatch.setattr(jobs.time, 'sleep', sleep)
    monkeypatch.setattr(agenda, 'refresh', lambda **kw: refreshed.append(kw))
    try:
        jobs._agenda_loop(Brain())
    except RuntimeError as exc:
        assert str(exc) == 'stop test loop'

    assert sleeps == [60, 1200]
    assert refreshed == [
        {'persona_key': 'persona-wechat', 'src': 'wechat'},
        {'persona_key': 'persona-wechat_group', 'src': 'wechat_group'},
        {'persona_key': 'persona-pc', 'src': 'pc'},
    ]
