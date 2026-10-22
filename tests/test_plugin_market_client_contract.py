from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "client" / "src" / "js" / "pages.js"
STUB = ROOT / "client" / "src" / "js" / "_devstub.js"


def test_market_client_uses_unified_lifecycle_routes_and_state_fields():
    source = PAGES.read_text(encoding="utf-8")
    assert "/api/market/list" in source
    assert "/api/market/" in source
    for action in ("install", "update", "uninstall", "enable", "disable", "reload"):
        assert action in source
    assert "'/api/market/lifecycle'" in source
    for field in ("market_version", "update_state", "runtime_state", "diagnostic", "enabled"):
        assert field in source
    assert "确认卸载“" in source
    assert "marketPending" in source


def test_preview_stub_matches_install_update_and_lifecycle_contract():
    source = STUB.read_text(encoding="utf-8")
    for route in ("/api/market/list", "/api/market/install", "/api/market/update",
                  "/api/market/uninstall", "/api/market/lifecycle"):
        assert route in source
    assert "user_data: 'preserved'" in source
