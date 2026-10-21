"""Opt-in network acceptance: real market + ZIP, isolated installation directory.

Run from the repository root: python client/tools/test-plugin-market-live.py
Exports verified frontend assets for test-plugin-market-electron.js
--wallpaper-assets=<absolute path to reports/p11-wallpaper-assets.json>.
No user installation, feature configuration or pending-restart state is changed.
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'brain'))
import features
import plugin_host
import plugin_ops
import restart_state
from plugins import plugin_market as market


def main():
    expected = json.loads((ROOT / 'market.json').read_text(encoding='utf-8'))['plugins'][0]
    index = market._read_remote_market()
    assert index['state'] == 'ready', index
    meta = next(p for p in index['plugins'] if p['name'] == 'wallpaper')
    assert meta['artifact'] == expected['artifact'], meta
    package = market._fetch_bytes(meta['artifact']['url'], market.REMOTE_PACKAGE_LIMIT,
                                  expected_sha=meta['artifact']['sha256'])
    print('PASS remote index and downloaded SHA-256:', hashlib.sha256(package).hexdigest(), flush=True)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assets = {key: archive.read('wallpaper/frontend.' + ext).decode('utf-8')
                  for key, ext in [('js', 'js'), ('css', 'css')]}
    (ROOT / 'reports').mkdir(exist_ok=True)
    (ROOT / 'reports/p11-wallpaper-assets.json').write_text(json.dumps(assets), encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='vigil-p11-live-') as home:
        target = Path(home) / 'installed'
        target.mkdir()
        with patch.object(plugin_ops, '_PLUG_DIR', str(target)), \
             patch.object(plugin_ops, 'REGISTRY', str(target / '_registry.json')), \
             patch.object(market, '_plugins_dir', return_value=str(target)), \
             patch.object(market, '_repo_path', return_value=''), \
             patch.object(restart_state, 'note_change'), \
             patch.object(features, 'drop', return_value=0):
            for operation in ['install', 'refresh', 'uninstall', 'install']:
                if operation == 'refresh':
                    assert market.api_list({}, {})['plugins'][0]['installed']
                else:
                    result = getattr(market, 'api_' + operation)({'name': 'wallpaper'}, {})
                    assert result['ok'], result
                    if operation == 'install':
                        plugin = plugin_host._load_one(str(target / 'wallpaper/__init__.py'))
                        assert plugin and not plugin.err
                        assert plugin.frontend_js == assets['js'] and plugin.frontend_css == assets['css']
                        assert market.api_list({}, {})['plugins'][0]['installed']
                    else:
                        assert not (target / 'wallpaper').exists()
                        assert not market.api_list({}, {})['plugins'][0]['installed']
                assert not list(target.glob('_tmp*'))
                print('PASS real market:', operation, flush=True)
    print('PASS real plugin entry and frontend resources; temporary installation removed', flush=True)


if __name__ == '__main__':
    main()
