"""Release-time guard: bundled plugins ship, external plugins must not."""
import json
from pathlib import Path

# 只有这些插件是"外部"的：由插件市场安装到 data/installed_plugins。
# 其余（kb/gate/react/websearch/meme/reminder/sticker_pack/branch-atlas）随包内置。
EXTERNAL_PLUGIN_NAMES = ("wallpaper",)


def assert_no_bundled_plugin_tree(root):
    """Fail closed if the release carries an external plugin or a runtime install tree.

    Bundled plugins under plugins/ are expected. wallpaper and any Market package
    (Manifest v1) are not: they have their own download, digest and install checks,
    and shipping a copy would shadow the installed version.
    """
    root = Path(root).resolve()
    if not root.exists():
        return True
    for candidate in root.rglob("*"):
        parts = candidate.relative_to(root).parts
        rel = candidate.relative_to(root).as_posix()
        lowered = [part.lower() for part in parts]
        if "installed_plugins" in lowered:
            raise RuntimeError("Release contains an installed plugin tree: " + rel)
        if any(part in EXTERNAL_PLUGIN_NAMES for part in lowered):
            raise RuntimeError("Release contains an external plugin payload: " + rel)
        if candidate.is_file() and candidate.name.lower() == "manifest.json":
            try:
                manifest = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if manifest.get("schema") == "vigil.brain-plugin/v1":
                raise RuntimeError("Release contains an extension plugin manifest: " + rel)
    return True
