"""Static contract checks for the P9.0 documents and example manifests.

Uses only the standard library; it never starts Brain, Electron, WeChat or a model.
The small validator below implements the JSON Schema keywords used by our schema so
CI can check the contract without adding a runtime dependency.
"""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/schemas/extension-manifest.schema.json"
WALLPAPER_PATH = ROOT / "docs/examples/extensions/wallpaper.manifest.example.json"
BRIDGE_PATH = ROOT / "docs/examples/extensions/wechat-bridge.manifest.example.json"


def _resolve_ref(root_schema, ref):
    if not ref.startswith("#/"):
        raise AssertionError(f"external schema reference is not allowed in this test: {ref}")
    value = root_schema
    for part in ref[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def _matches(instance, schema, root_schema):
    if "$ref" in schema:
        return _matches(instance, _resolve_ref(root_schema, schema["$ref"]), root_schema)
    if "oneOf" in schema:
        return sum(_matches(instance, option, root_schema) for option in schema["oneOf"]) == 1
    if "allOf" in schema and not all(_matches(instance, option, root_schema) for option in schema["allOf"]):
        return False
    if "type" in schema:
        expected = schema["type"]
        checks = {
            "object": lambda x: isinstance(x, dict),
            "array": lambda x: isinstance(x, list),
            "string": lambda x: isinstance(x, str),
            "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
        }
        if expected in checks and not checks[expected](instance):
            return False
    if "const" in schema and instance != schema["const"]:
        return False
    if "enum" in schema and instance not in schema["enum"]:
        return False
    if isinstance(instance, dict):
        if not all(key in instance for key in schema.get("required", [])):
            return False
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties and not _matches(value, properties[key], root_schema):
                return False
            if key not in properties and schema.get("additionalProperties") is False:
                return False
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0) or len(instance) > schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in instance}) != len(instance):
            return False
        if "items" in schema and any(not _matches(item, schema["items"], root_schema) for item in instance):
            return False
    if isinstance(instance, str):
        if len(instance) > schema.get("maxLength", float("inf")):
            return False
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            return False
        if schema.get("format") == "uri":
            parsed = urlparse(instance)
            if not parsed.scheme or not parsed.netloc:
                return False
    if isinstance(instance, int) and not isinstance(instance, bool):
        if instance < schema.get("minimum", float("-inf")):
            return False
    return True


def _semver_tuple(value):
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", value)
    if not match:
        raise ValueError(value)
    return tuple(map(int, match.groups()))


def _compatible(manifest, core_version, protocol_name, major, minor):
    return (
        _semver_tuple(core_version) >= _semver_tuple(manifest["min_core"])
        and manifest["protocol"]["name"] == protocol_name
        and manifest["protocol"]["major"] == major
        and minor >= manifest["protocol"]["min_minor"]
    )


class P9ContractArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.examples = {
            "desktop-extension": json.loads(WALLPAPER_PATH.read_text(encoding="utf-8")),
            "channel-bridge": json.loads(BRIDGE_PATH.read_text(encoding="utf-8")),
        }

    def test_both_fake_manifests_are_valid(self):
        for manifest in self.examples.values():
            self.assertTrue(_matches(manifest, self.schema, self.schema))

    def test_unknown_permission_kind_and_missing_required_field_are_rejected(self):
        wallpaper = self.examples["desktop-extension"]
        for mutation in (
            {**wallpaper, "permissions": ["filesystem.anything"]},
            {**wallpaper, "kind": "arbitrary-code"},
            {key: value for key, value in wallpaper.items() if key != "protocol"},
        ):
            self.assertFalse(_matches(mutation, self.schema, self.schema))

    def test_unknown_fields_and_path_traversal_are_rejected(self):
        base = self.examples["desktop-extension"]
        self.assertFalse(_matches({**base, "extra": True}, self.schema, self.schema))
        self.assertFalse(_matches({**base, "entry": "../outside.js"}, self.schema, self.schema))
        self.assertFalse(_matches({**base, "entry": "./main.js"}, self.schema, self.schema))
        artifact = {**base["artifacts"][0], "sha256": "../not-a-hash"}
        self.assertFalse(_matches({**base, "artifacts": [artifact]}, self.schema, self.schema))

    def test_type_specific_fields_and_permissions_are_separated(self):
        desktop = self.examples["desktop-extension"]
        bridge = self.examples["channel-bridge"]
        self.assertFalse(_matches({**desktop, "permissions": ["brain.chat.invoke"]}, self.schema, self.schema))
        self.assertFalse(_matches({**bridge, "permissions": ["desktop.wallpaper.control"]}, self.schema, self.schema))
        self.assertFalse(_matches({**desktop, "protocol": {**desktop["protocol"], "name": "vigil-channel-bridge"}}, self.schema, self.schema))

    def test_minimum_core_and_protocol_compatibility(self):
        desktop = self.examples["desktop-extension"]
        self.assertTrue(_compatible(desktop, "1.1.7", "vigil-desktop-extension", 1, 0))
        self.assertFalse(_compatible(desktop, "1.1.6", "vigil-desktop-extension", 1, 0))
        self.assertFalse(_compatible(desktop, "1.1.7", "vigil-desktop-extension", 2, 0))
        self.assertFalse(_compatible(desktop, "1.1.7", "vigil-desktop-extension", 1, -1))

    def test_documents_reference_real_paths_and_required_safety_contracts(self):
        protocol = (ROOT / "docs/P9_extension_protocol.md").read_text(encoding="utf-8")
        migration = (ROOT / "docs/P9_data_migration.md").read_text(encoding="utf-8")
        p82 = (ROOT / "docs/05-微信/P8.2-微信插件化迁移清单.md").read_text(encoding="utf-8")
        readme = (ROOT / "wechat-bridge/README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs/USER_GUIDE.md").read_text(encoding="utf-8")
        self.assertTrue(SCHEMA_PATH.is_file())
        self.assertIn("`brain/ws_forward.py`", protocol)
        self.assertIn("`client/electron/forward.js`", protocol)
        self.assertIn("userData/wallpaper-engine", migration)
        self.assertIn("`.vigil/wallpaper/config.json`", migration)
        self.assertIn("msg/ack/result", migration)
        self.assertIn("Turn.veto()", p82)
        self.assertIn("`brain/wechat_bot/`", readme)
        self.assertIn("P9_extension_protocol.md", guide)
        self.assertFalse((ROOT / "brain/wechat_bot/wxauto_bridge.py").exists())

    def test_examples_contain_only_fake_artifact_values(self):
        for path in (WALLPAPER_PATH, BRIDGE_PATH):
            raw = path.read_text(encoding="utf-8")
            obj = json.loads(raw)
            self.assertTrue(all(a["url"].startswith("https://example.invalid/") for a in obj["artifacts"]))
            self.assertNotRegex(raw, r"(?i)(?:bearer\s+[a-z0-9._-]{12,}|gh[pousr]_[a-z0-9]{20,}|sk-[a-z0-9]{20,})")
            self.assertNotRegex(raw, r"(?i)(?:brain_token|wxid_[a-z0-9]+|password|secret|credential)")


if __name__ == "__main__":
    unittest.main()
