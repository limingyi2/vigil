"""Two explicitly unpublished versions for a real NSIS version upgrade/rollback."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / 'client' / n for n in ('package.json', 'package-lock.json', 'version.json')]


def main():
    originals = {p: p.read_bytes() for p in FILES}
    try:
        for p in FILES:
            d = json.loads(originals[p])
            if p.name == 'version.json':
                d.update(ver='1.0.99', code=20)
            else:
                d['version'] = '1.0.99'
                if 'packages' in d:
                    d['packages']['']['version'] = '1.0.99'
            p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'), '--candidate', '--installer-test'], cwd=ROOT, check=True)
    finally:
        for p, content in originals.items():
            p.write_bytes(content)
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'), '--candidate', '--installer-test'], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
