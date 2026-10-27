from scripts.test_windows_candidate_lifecycle import is_known_unsigned_p212_startup_block
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('official', [False, True])
def test_historical_subprocess_receives_parent_build_kind(tmp_path, monkeypatch, official):
    from scripts import test_windows_candidate_lifecycle as lifecycle
    def download(url, target, expected_sha):
        if target.suffix == '.json':
            target.write_text(json.dumps({
                'source_commit': 'd0a7e90409614961fda3a4a33523e42d4d4b92dc',
                'ver': '1.1.9-p21.2.20261025.1', 'candidate': False,
                'sha256': '954ae2096bfc8a1327c35b50165404dc887b674a8ba32e45e079c33e598bcfd6',
                'size': 162533456,
            }), encoding='utf-8')
        else:
            target.write_bytes(b'fixture')
        return target
    monkeypatch.setattr(lifecycle, 'download_verified', download)
    monkeypatch.setattr(lifecycle, 'authenticode_presence', lambda path: 'NotSigned')
    def run(command, **kwargs):
        assert ('--official' in command) is official
        output = Path(command[command.index('--output') + 1])
        output.write_text('{"result":"passed"}', encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(lifecycle.subprocess, 'run', run)
    result = lifecycle.run_historical_upgrade(tmp_path / 'new.exe', 'new-source', '1.1.13',
                                              tmp_path, official=official)
    assert result['result'] == 'passed'


def test_only_known_unsigned_p212_cp1252_startup_is_reported_blocked():
    evidence = (
        "UnicodeEncodeError: 'charmap' codec can't encode characters "
        "(cp1252) in brain/server_launch.py"
    )
    assert is_known_unsigned_p212_startup_block('NotSigned', evidence)
    assert not is_known_unsigned_p212_startup_block('SignedPresent', evidence)
    assert not is_known_unsigned_p212_startup_block('NotSigned', 'Brain startup timed out')
