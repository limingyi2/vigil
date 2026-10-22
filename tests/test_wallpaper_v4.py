import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / 'market/plugins/wallpaper/__init__.py'
spec = importlib.util.spec_from_file_location('wallpaper_v4_test', PLUGIN)
wallpaper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wallpaper)


def _fixture(root: Path):
    item = root / '123'
    item.mkdir()
    (item / 'project.json').write_text(json.dumps({'type': 'Scene', 'title': 'Animated'}), encoding='utf-8')
    (item / 'scene.json').write_text('{}', encoding='utf-8')
    (item / 'preview.jpg').write_bytes(b'jpg')
    (item / 'secret.txt').write_text('secret', encoding='utf-8')
    return item


def test_scene_type_normalized_and_library_cache(tmp_path):
    _fixture(tmp_path)
    with patch.object(wallpaper, 'DATA_DIR', tmp_path / 'data'), patch.object(wallpaper, 'CACHE_FILE', tmp_path / 'data/library.json'), patch.object(wallpaper, '_find_workshop', return_value=tmp_path):
        rows, path = wallpaper._scan(force=True)
    assert path == str(tmp_path)
    assert rows[0]['type'] == 'scene'
    assert rows[0]['file'] == 'scene.json'


def test_resource_route_is_read_only_and_fenced(tmp_path):
    item = _fixture(tmp_path)
    with patch.object(wallpaper, '_find_workshop', return_value=tmp_path):
        out = wallpaper.api_resource({}, {'id': '123', 'file': 'scene.json'})
        assert Path(out['__file__']) == item / 'scene.json'
        assert wallpaper.api_resource({}, {'id': '123', 'file': '../secret.txt'})['_code'] == 404
        assert wallpaper.api_resource({}, {'id': '123', 'file': 'missing.bin'})['_code'] == 404
        assert wallpaper.api_resource({}, {'id': '../', 'file': 'scene.json'})['_code'] == 404
