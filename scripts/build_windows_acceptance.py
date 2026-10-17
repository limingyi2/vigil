"""Build an unpublished 1.1.0 installer with production identity for isolated upgrades."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    # The disposable Windows runner supplies the official 1.0.2 installer as
    # the baseline. Do not mutate package versions or synthesize a fake baseline.
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_release.py'), '--candidate'],
                   cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
