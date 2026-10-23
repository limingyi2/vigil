"""Thin adapters for plugin management API responses."""


def management_items(plugin_ops, channels):
    """Combine the existing plugin views for the legacy management endpoint."""
    items = plugin_ops.overview(channels)
    by_name = {str(item.get("name") or ""): item for item in items}
    try:
        import plugin_host
        runtime = {str(item.get("name") or ""): item
                   for item in plugin_host.listing()}
        import plugin_manager
        statuses = plugin_manager.list_plugins()
    except Exception:
        return items
    for status in statuses:
        name = str(status.get("name") or "")
        row = by_name.get(name)
        info = runtime.get(name) or {}
        if row is None:
            feature = str(info.get("feature") or
                          (status.get("manifest") or {}).get("feature") or "")
            row = {
                "name": name, "desc": info.get("desc", ""),
                "feature": feature, "tools": info.get("tools", []),
                "routes": info.get("routes", []), "hooks": info.get("hooks", []),
                "file": info.get("file", ""), "err": info.get("err", ""),
                "channels": plugin_ops.channels_of(feature, channels),
                "channelable": bool(feature), "meta": plugin_ops._meta_of(name),
            }
            items.append(row)
            by_name[name] = row
        for key in ("state", "enabled", "lifecycle_enabled",
                    "effective_by_src", "source_kind", "builtin",
                    "removable", "runtime_state", "version",
                    "update_state", "diagnostic"):
            row[key] = status.get(key)
    return sorted(items, key=lambda item: str(item.get("name") or ""))
