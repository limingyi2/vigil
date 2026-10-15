"""Discover native Codex and ask its official CLI about auth; never read auth files."""
import os
from pathlib import Path
import re
import shutil
import subprocess


def find_cli():
    candidate = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if candidate:
        return candidate
    if os.name != "nt":
        return None
    # GUI launches may inherit an old PATH. Prefer native binaries to shell shims.
    roots = []
    if os.environ.get("LOCALAPPDATA"):
        roots.append((Path(os.environ["LOCALAPPDATA"]) / "OpenAI/Codex/bin", "*/codex.exe"))
    npm_bases = []
    shim = shutil.which("codex")
    if shim:
        npm_bases.append(Path(shim).parent / "node_modules/@openai")
    if os.environ.get("APPDATA"):
        npm_bases.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@openai")
    for base in npm_bases:
        for root in (base, base / "codex/node_modules/@openai"):
            roots.append((root, "codex-win32-*/vendor/*/codex/codex.exe"))
        roots.append((base / "codex", "vendor/*/codex/codex.exe"))
    candidates = []
    for root, pattern in roots:
        try:
            candidates.extend(p for p in root.glob(pattern) if p.is_file())
        except OSError:
            continue
    try:
        return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
    except OSError:
        return None


class CodexRuntime:
    def __init__(self, cli=None, home=None, environment=None):
        self.explicit_cli = cli
        self.cli = cli
        self.home = home
        self.environment = dict(environment or {})
        self.info = {"installed": None, "version": None, "auth_status": "unchecked",
                     "error": None}

    def env(self):
        env = os.environ.copy()
        env.update(self.environment)
        if self.home:
            env["CODEX_HOME"] = str(self.home)
        return env

    def inspect(self):
        self.cli = self.explicit_cli or find_cli()
        info = {"installed": bool(self.cli), "version": None,
                "auth_status": "unknown", "error": None}
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            if not self.cli:
                info["error"] = "codex_cli_missing"
                return info
            version = subprocess.run([self.cli, "--version"], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=self.env(), timeout=6, check=False, **flags)
            match = re.fullmatch(rb"codex-cli ([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*)\s*",
                                 version.stdout.strip())
            if version.returncode or not match:
                info["error"] = "codex_version_failed"
                return info
            info["version"] = match[1].decode("ascii")[:100]
            # Login output may contain identity or a masked API key. Discard both streams.
            login = subprocess.run([self.cli, "login", "status"], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=self.env(), timeout=6, check=False, **flags)
            info["auth_status"] = "logged_in" if login.returncode == 0 else "logged_out" if login.returncode == 1 else "unknown"
            if login.returncode:
                info["error"] = "codex_login_required" if login.returncode == 1 else "codex_auth_check_failed"
        except subprocess.TimeoutExpired:
            info["error"] = "codex_detection_timeout"
        except OSError:
            info["error"] = "codex_cli_unavailable"
        finally:
            self.info = info
        return info
