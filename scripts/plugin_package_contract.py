"""Release-time guard: optional plugins must be independently installed."""
import json
import os
from pathlib import Path


def _entry_name_and_sources(entry):
    if isinstance(entry, dict):
        name = entry.get("name") or entry.get("module") or ""
        source = entry.get("source") or entry.get("origin") or entry.get("path") or ""
        return str(name), [str(source)] if source else []
    if isinstance(entry, (tuple, list)):
        name = str(entry[0]) if entry else ""
        return name, [str(value) for value in entry[1:] if value]
    return str(entry), []


def assert_no_optional_plugin_modules(entries, plugin_source_root=None):
    """Reject optional plugin modules in PyInstaller analysis/archive inventories.

    Analysis entries include source origins; archive-viewer entries include
    embedded import names. Either is sufficient to fail the release closed.
    """
    root = None
    if plugin_source_root:
        root = os.path.normcase(str(Path(plugin_source_root).resolve())).replace("\\", "/").rstrip("/")
    for entry in entries or ():
        name, sources = _entry_name_and_sources(entry)
        module_parts = [part.casefold() for part in name.replace("\\", "/").replace(".", "/").split("/")]
        if "plugins" in module_parts:
            raise RuntimeError("Release embeds an optional plugin module: " + name)
        if root:
            for source in sources:
                try:
                    normalized = os.path.normcase(str(Path(source).resolve())).replace("\\", "/")
                except (OSError, ValueError):
                    normalized = os.path.normcase(source).replace("\\", "/")
                if normalized == root or normalized.startswith(root + "/"):
                    raise RuntimeError("Release embeds a module from optional plugin source: " +
                                       (name or source))
    return True


def assert_no_bundled_plugin_tree(root, embedded_modules=None, plugin_source_root=None):
    """Fail closed on any plugin source tree or runtime install tree in an artifact."""
    root = Path(root).resolve()
    if embedded_modules is not None:
        assert_no_optional_plugin_modules(embedded_modules,
                                          plugin_source_root=plugin_source_root)
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
