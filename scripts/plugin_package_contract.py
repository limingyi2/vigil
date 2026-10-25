"""Release-time guard: optional plugins must be independently installed."""
import json
from pathlib import Path

def assert_no_bundled_plugin_tree(root):
    """Fail closed on any plugin source tree or runtime install tree in an artifact."""
    root = Path(root).resolve()
    if not root.exists():
        return True
    for candidate in root.rglob("*"):
        parts = candidate.relative_to(root).parts
        rel = candidate.relative_to(root).as_posix()
        lowered = [part.lower() for part in parts]
        if "installed_plugins" in lowered:
            raise RuntimeError("Release contains an installed plugin tree: " + rel)
        if "plugins" in lowered:
            raise RuntimeError("Release contains a plugin source tree: " + rel)
        if candidate.is_file() and candidate.name.lower() == "manifest.json":
            try:
                manifest = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if manifest.get("schema") == "vigil.brain-plugin/v1":
                raise RuntimeError("Release contains an extension plugin manifest: " + rel)
        if candidate.is_file() and candidate.name.lower() == "zxplugin.json":
            raise RuntimeError("Release contains a legacy plugin manifest: " + rel)
    return True
