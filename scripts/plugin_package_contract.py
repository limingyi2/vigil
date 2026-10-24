"""Release-time guard against accidentally shipping plugin source trees."""
import json
from pathlib import Path


def assert_no_bundled_plugin_tree(root):
    """Fail closed on legacy plugin roots or a renamed Manifest v1 package."""
    root = Path(root).resolve()
    if not root.exists():
        return True
    for candidate in root.rglob("*"):
        if candidate.is_dir() and candidate.name.lower() in ("plugins", "installed_plugins"):
            raise RuntimeError("Release contains bundled plugin tree: " +
                               candidate.relative_to(root).as_posix())
        if candidate.is_file() and candidate.name.lower() == "manifest.json":
            try:
                manifest = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if manifest.get("schema") == "vigil.brain-plugin/v1":
                raise RuntimeError("Release contains bundled plugin manifest: " +
                                   candidate.relative_to(root).as_posix())
    return True
