"""Release-time guard against accidentally shipping plugin source trees."""
from pathlib import Path


def assert_no_bundled_plugin_tree(root):
    """Fail closed if the Brain sidecar contains the legacy plugin source root."""
    root = Path(root).resolve()
    for relative in ("plugins", "_internal/plugins"):
        candidate = root / relative
        if candidate.exists():
            raise RuntimeError("Release contains bundled plugin tree: " + relative)
    return True
