# -*- coding: utf-8 -*-
"""Targeted Creator MVP-2 approval/transaction tests.

The fixture uses an isolated temporary data/plugins root.  Skill installation is
stubbed at the existing ``plugins.skill._install_zip`` boundary because the
Node Skill Host is an external process; the tests still assert that the exact
approved archive is delegated to that boundary.
"""
import json
import os
import threading
import time

import pytest

import creator
import creator_install as ci
import paths


ADMIN = {"is_admin": True, "token_kind": "device", "device_id": "test-admin"}
GUEST = {"is_admin": False, "token_kind": "device", "device_id": "test-viewer"}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    drafts = data / "creator_drafts"
    plugins = tmp_path / "plugins"
    drafts.mkdir(parents=True)
    plugins.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", str(data))
    monkeypatch.setattr(creator, "drafts_root", lambda: str(drafts))
    monkeypatch.setattr(ci, "_root", lambda: str(data / "creator_approvals"))
    monkeypatch.setattr(ci.paths, "DATA_DIR", str(data))
    monkeypatch.setattr(ci, "_LOCK", threading.RLock())
    import plugin_ops
    monkeypatch.setattr(plugin_ops, "_PLUG_DIR", str(plugins))
    monkeypatch.setattr(plugin_ops, "REGISTRY", str(plugins / "_registry.json"))
    return tmp_path, drafts, plugins


def _draft(drafts, kind="plugin", name="creator_probe", valid=True):
    did = "cr_test_%s" % name
    root = drafts / did
    root.mkdir()
    if kind == "plugin":
        (root / (name + ".py")).write_text(
            ('NAME = %r\nDESC = "safe test"\n'
             'def run(arg):\n    return "ok"\n'
             'TOOLS = {"probe": run}\n'
             'TOOL_SPECS = {"probe": {"desc": "safe test", "params": [], "arg_param": None}}\n') % name
            if valid else ('NAME = %r\nDESC = "safe test"\ndef broken(:\n' % name),
            encoding="utf-8")
        (root / "zxplugin.json").write_text(json.dumps({"name": name, "version": "0.0.1", "desc": "safe test"}), encoding="utf-8")
    else:
        (root / "index.js").write_text("export function setup(api) { api.registerTool({id:'probe',name:'probe',description:'safe',parameters:{},async execute(){return {content:'ok'}}}); }\n", encoding="utf-8")
        (root / "skill.json").write_text(json.dumps({"id": name, "name": "Safe test", "version": "0.0.1", "entry": "index.js"}), encoding="utf-8")
    meta = {"id": did, "kind": kind, "version": 1, "files": [p.name for p in root.iterdir()], "validation": {}}
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return did


def _approve(did):
    return ci.approve(did, ADMIN)


def test_approval_requires_admin_and_invalid_draft_is_rejected(isolated):
    _, drafts, _ = isolated
    did = _draft(drafts)
    with pytest.raises(PermissionError):
        ci.approve(did, GUEST)
    bad = _draft(drafts, name="bad", valid=False)
    with pytest.raises(ValueError):
        ci.approve(bad, ADMIN)



def test_creator_install_rejects_plugin_without_chat_tools(isolated):
    _, drafts, _ = isolated
    did = _draft(drafts, name="chat_missing", valid=False)
    # Replace the syntax-invalid fixture with syntactically valid legacy-style
    # code that only declares on_message, which the current host never calls.
    root = drafts / did
    (root / "chat_missing.py").write_text(
        'NAME = "chat_missing"\nDESC = "legacy callback only"\n'
        'def on_message(text, ctx):\n    return "ok"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        ci.approve(did, ADMIN)


def test_changed_draft_invalidates_old_approval(isolated):
    _, drafts, _ = isolated
    did = _draft(drafts)
    approval = _approve(did)
    (drafts / did / "creator_probe.py").write_text('NAME = "creator_probe"\nDESC = "changed"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="改变|失效"):
        ci.install(approval["approval_id"], ADMIN)


def test_expired_and_repeated_approvals_are_rejected(isolated, monkeypatch):
    _, drafts, _ = isolated
    did = _draft(drafts)
    approval = _approve(did)
    rec = ci._read(approval["approval_id"])
    rec["expires_at_epoch"] = time.time() - 1
    ci._write(rec)
    with pytest.raises(ValueError, match="过期"):
        ci.install(approval["approval_id"], ADMIN)

    approval = _approve(did)
    import plugin_ops
    monkeypatch.setattr(plugin_ops, "install", lambda path, note="": (True, {"ok": True, "restart_needed": True}))
    assert ci.install(approval["approval_id"], ADMIN)["ok"]
    with pytest.raises(ValueError, match="使用|失效"):
        ci.install(approval["approval_id"], ADMIN)


def test_install_requires_admin_and_delegates_once_under_concurrency(isolated, monkeypatch):
    _, drafts, _ = isolated
    did = _draft(drafts)
    approval = _approve(did)
    import plugin_ops
    calls = []
    def fake_install(path, note=""):
        time.sleep(0.03)
        calls.append(path)
        return True, {"ok": True, "restart_needed": False}
    monkeypatch.setattr(plugin_ops, "install", fake_install)
    with pytest.raises(PermissionError):
        ci.install(approval["approval_id"], GUEST)
    with pytest.raises(PermissionError, match="身份不一致"):
        ci.install(approval["approval_id"], {"is_admin": True, "token_kind": "brain", "device_id": ""})
    results = []
    def consume():
        try:
            results.append(ci.install(approval["approval_id"], ADMIN))
        except ValueError as exc:
            results.append({"ok": False, "err": str(exc)})
    threads = [threading.Thread(target=consume) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(bool(r.get("ok")) for r in results) == 1
    assert len(calls) == 1


def test_failed_transaction_is_recorded_and_not_replayed(isolated, monkeypatch):
    _, drafts, _ = isolated
    did = _draft(drafts)
    approval = _approve(did)
    import plugin_ops
    monkeypatch.setattr(plugin_ops, "install", lambda path, note="": (False, {"ok": False, "rollback": "restored", "err": "probe"}))
    result = ci.install(approval["approval_id"], ADMIN)
    assert result["ok"] is False
    assert result["install"]["rollback"] == "restored"
    with pytest.raises(ValueError):
        ci.install(approval["approval_id"], ADMIN)


def test_valid_plugin_is_installed_by_existing_transaction_without_import(isolated, monkeypatch):
    _, drafts, plugins = isolated
    did = _draft(drafts, name="no_exec")
    marker = plugins / "executed.txt"
    (drafts / did / "no_exec.py").write_text(
        'from pathlib import Path\nPath(%r).write_text("bad")\n'
        'NAME="no_exec"\nDESC="safe"\n'
        'def no_exec_status(_arg): return "safe"\n'
        'TOOLS={"no_exec_status": no_exec_status}\n'
        'TOOL_SPECS={"no_exec_status":{"desc":"safe","params":[],"arg_param":None}}\n'
        % str(marker), encoding="utf-8")
    import plugin_ops
    approval = _approve(did)
    result = ci.install(approval["approval_id"], ADMIN)
    assert result["ok"] is True
    assert result["install"]["static_validated"] is True
    assert (plugins / "no_exec.py").is_file()
    assert not marker.exists(), "approval install must not import generated code"


def test_creator_directory_plugin_installs_then_loads_tools_and_frontend(isolated, monkeypatch):
    _, drafts, plugins = isolated
    did = "cr_directory_frontend"
    root = drafts / did
    package = root / "creator_panel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        'NAME="creator_panel"\nDESC="approved directory fixture"\nFEATURE="creator_panel_fixture"\n'
        'def panel_status(arg): return "PANEL_TOOL_OK:" + str(arg)\n'
        'TOOLS={"panel_status": panel_status}\n'
        'TOOL_SPECS={"panel_status":{"desc":"panel status","params":[],"arg_param":None}}\n',
        encoding="utf-8")
    (package / "frontend.js").write_text(
        "window.VigilPlugin.registerPage({id:'creator_panel'});\n", encoding="utf-8")
    (package / "frontend.css").write_text(".creator-panel { color: #fff; }\n", encoding="utf-8")
    (package / "zxplugin.json").write_text(json.dumps({
        "name": "creator_panel", "version": "0.1.0", "desc": "approved directory fixture"}),
        encoding="utf-8")
    (root / "meta.json").write_text(json.dumps({
        "id": did, "kind": "plugin", "version": 1, "files": [], "validation": {}}),
        encoding="utf-8")

    report, validation_error = creator.validate_draft(did)
    assert not validation_error, validation_error
    assert report["ok"], report
    approval = _approve(did)
    result = ci.install(approval["approval_id"], ADMIN)
    assert result["ok"] is True, result
    assert result["install"]["layout"] == "directory"
    assert result["install"]["frontend_assets"] is True
    assert result["install"]["restart_needed"] is True
    assert (plugins / "creator_panel" / "frontend.js").is_file()
    assert (plugins / "creator_panel" / "frontend.css").is_file()

    import plugin_host
    monkeypatch.setattr(plugin_host, "_DIR", str(plugins))
    monkeypatch.setattr(plugin_host, "_registry", {})
    monkeypatch.setattr(plugin_host, "_TOOL_INDEX", {})
    plugin_host.load(force=True)
    import features
    monkeypatch.setattr(features, "on", lambda name, src=None: src == "pc")
    assert "panel_status" in plugin_host.tool_specs("pc")
    assert "panel_status" not in plugin_host.tool_specs("wechat")
    assert plugin_host.run_tool("panel_status", "ready", src="pc") == "PANEL_TOOL_OK:ready"
    assert plugin_host.run_tool("panel_status", "ready", src="wechat") is None
    assets = plugin_host.frontend_assets()
    installed = next(item for item in assets["plugins"] if item["name"] == "creator_panel")
    assert "registerPage" in installed["js"]
    assert ".creator-panel" in installed["css"]


def test_skill_archive_uses_existing_skill_transaction_boundary(isolated, monkeypatch):
    root, drafts, _ = isolated
    did = _draft(drafts, kind="skill", name="creator_skill")
    from plugins import skill as skill_ops
    skill_root = root / "skills"
    config_path = root / "config.json"
    monkeypatch.setattr(skill_ops, "_skill_dir", lambda: str(skill_root))
    monkeypatch.setattr(skill_ops, "_skill_config_path", lambda: str(config_path))
    monkeypatch.setattr(skill_ops, "_skill_config_snapshot", lambda: (str(config_path), None))
    monkeypatch.setattr(skill_ops, "_host_reload_checked", lambda: {"ok": True, "reloaded": True})
    monkeypatch.setattr(skill_ops, "list_skills", lambda timeout=8: (
        [{"id": "creator_skill"}] if (skill_root / "creator_skill").is_dir() else []))
    approval = _approve(did)
    result = ci.install(approval["approval_id"], ADMIN)
    assert result["ok"] is True
    assert result["install"]["files_committed"] is True
    assert (skill_root / "creator_skill" / "index.js").is_file()
