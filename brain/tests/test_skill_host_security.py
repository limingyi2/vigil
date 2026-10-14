# -*- coding: utf-8 -*-
"""Security contract tests for the real Node Skill Host using temporary skills."""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOKEN = "host-security-test-token-0123456789abcdef"

sys.path.insert(0, str(ROOT / "brain"))
from plugins import skill as skill_bridge


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_skill(parent, folder, *, entry="index.js", manifest=True, source=None):
    root = parent / folder
    root.mkdir(parents=True, exist_ok=True)
    (root / entry).parent.mkdir(parents=True, exist_ok=True)
    (root / entry).write_text(source or """
export function setup(api) {
  api.registerTool({id: 'echo', name: 'Echo', async execute(_ctx, args) {
    return {content: String(args.text || '')}
  }})
}
""", encoding="utf-8")
    if manifest:
        (root / "skill.json").write_text(json.dumps({
            "id": folder, "name": folder, "entry": entry,
        }), encoding="utf-8")
    return root


class TestSkillHostSecurity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vigil-skill-security-")
        self.root = pathlib.Path(self.temp.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        write_skill(self.skills, "legacy-no-manifest", manifest=False)
        write_skill(self.skills, "register-skill", entry="index.mjs", source="""
export function register(api) {
  api.registerTool({id: 'echo', name: 'Echo', async execute(_ctx, args) {
    return {content: String(args.text || '')}
  }})
}
""")
        self.token = TOKEN
        self.proc, self.base = self.start_host(self.skills)

    def tearDown(self):
        self.stop_host(self.proc)
        self.temp.cleanup()

    def start_host(self, skills, *, token=TOKEN, host="127.0.0.1"):
        port = free_port()
        env = os.environ.copy()
        env.update({"ZX_SKILL_DIR": str(skills), "ZX_SKILL_PORT": str(port),
                    "ZX_SKILL_HOST": host})
        if token is None:
            env.pop("ZX_SKILL_AUTH_TOKEN", None)
        else:
            env["ZX_SKILL_AUTH_TOKEN"] = token
        proc = subprocess.Popen(
            ["node", str(ROOT / "brain" / "skill_host" / "server.js")],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        base = "http://127.0.0.1:%d" % port
        if token is not None and host == "127.0.0.1":
            for _ in range(60):
                if proc.poll() is not None:
                    break
                try:
                    self.request(base, "/health", token=token)
                    return proc, base
                except Exception:
                    time.sleep(0.05)
        return proc, base

    @staticmethod
    def stop_host(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    @staticmethod
    def request(base, path, *, token=None, method="GET", payload=None):
        headers = {}
        data = None
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method)
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def expect_401(self, base, path, *, method="GET", payload=None, token=None):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(base, path, token=token, method=method, payload=payload)
        self.assertEqual(caught.exception.code, 401)

    def test_all_routes_require_bearer_auth_and_ctx_cannot_replace_it(self):
        self.expect_401(self.base, "/health")
        self.expect_401(self.base, "/skills", token="wrong")
        self.expect_401(self.base, "/skills/call", method="POST",
                        payload={"tool": "legacy-no-manifest__echo", "ctx": {"token": TOKEN}})
        self.expect_401(self.base, "/reload", method="POST", payload={}, token="wrong")

    def test_correct_token_lists_reloads_and_calls_legacy_and_register_skills(self):
        code, health = self.request(self.base, "/health", token=TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(health["skills"], 2)
        _, listing = self.request(self.base, "/skills", token=TOKEN)
        names = {tool["name"] for skill in listing["skills"] for tool in skill["tools"]}
        self.assertIn("legacy-no-manifest__echo", names)
        self.assertIn("register-skill__echo", names)
        _, called = self.request(self.base, "/skills/call", token=TOKEN, method="POST",
                                 payload={"tool": "register-skill__echo",
                                          "args": {"text": "authenticated"},
                                          "ctx": {"src": "untrusted", "tier": "private"}})
        self.assertTrue(called["ok"])
        self.assertEqual(called["content"], "authenticated")
        _, reloaded = self.request(self.base, "/reload", token=TOKEN, method="POST", payload={})
        self.assertEqual(reloaded["reloaded"], 2)

    def test_python_bridge_sends_runtime_bearer_token(self):
        with mock.patch.dict(os.environ, {"ZX_SKILL_AUTH_TOKEN": TOKEN}):
            with mock.patch.object(skill_bridge, "_host", return_value=self.base):
                result = skill_bridge._get_json("/health", 3)
                self.assertTrue(result["ok"])
                called = skill_bridge._post_json("/skills/call", {
                    "tool": "legacy-no-manifest__echo", "args": {"text": "bridge"},
                    "ctx": {"src": "pc"},
                }, 3)
                self.assertEqual(called["content"], "bridge")

    def test_python_bridge_fails_closed_without_token_and_rejects_remote_host(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZX_SKILL_AUTH_TOKEN", None)
            with self.assertRaisesRegex(RuntimeError, "ZX_SKILL_AUTH_TOKEN"):
                skill_bridge._host_token()
        with mock.patch.object(skill_bridge, "_cfg", return_value={"host": "http://192.0.2.1:8790"}):
            with self.assertRaisesRegex(RuntimeError, "回环地址"):
                skill_bridge._host()

    def test_missing_token_and_public_bind_fail_before_listening(self):
        self.stop_host(self.proc)
        self.proc, _ = self.start_host(self.skills, token=None)
        self.proc.wait(timeout=5)
        self.assertNotEqual(self.proc.returncode, 0)
        self.proc, _ = self.start_host(self.skills, host="0.0.0.0")
        self.proc.wait(timeout=5)
        self.assertNotEqual(self.proc.returncode, 0)

    def test_manifest_path_escape_is_rejected_on_startup_and_reload(self):
        unsafe_root = self.root / "unsafe-skills"
        unsafe_root.mkdir()
        marker = self.root / "executed.txt"
        package = write_skill(unsafe_root, "escape", entry="index.js")
        (package / "skill.json").write_text(json.dumps({"id": "escape", "entry": "../escape.js"}),
                                             encoding="utf-8")
        (package / "index.js").write_text(
            "import fs from 'node:fs'; fs.writeFileSync(%r, 'executed')" % str(marker),
            encoding="utf-8")
        escape = unsafe_root / "escape.js"
        escape.write_text("export function setup() {}", encoding="utf-8")
        proc, base = self.start_host(unsafe_root)
        try:
            _, initial = self.request(base, "/health", token=TOKEN)
            self.assertEqual(initial["skills"], 0)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(base, "/reload", token=TOKEN, method="POST", payload={})
            self.assertEqual(caught.exception.code, 409)
            self.assertFalse(marker.exists())
        finally:
            self.stop_host(proc)

    def test_symlink_skill_entry_is_rejected(self):
        package = self.skills / "linked-skill"
        package.mkdir()
        outside = self.root / "outside.js"
        outside.write_text("export function setup() {}", encoding="utf-8")
        (package / "skill.json").write_text(json.dumps({"id": "linked-skill", "entry": "index.js"}),
                                             encoding="utf-8")
        try:
            (package / "index.js").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("symlink creation unavailable: %s" % type(exc).__name__)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(self.base, "/reload", token=TOKEN, method="POST", payload={})
        self.assertEqual(caught.exception.code, 409)
        _, health = self.request(self.base, "/health", token=TOKEN)
        self.assertEqual(health["skills"], 2)
        _, listing = self.request(self.base, "/skills", token=TOKEN)
        self.assertNotIn("linked-skill", {item["id"] for item in listing["skills"]})

    def test_windows_reserved_skill_name_is_rejected(self):
        package = self.skills / "CON"
        package.mkdir()
        (package / "index.js").write_text("export function setup() {}", encoding="utf-8")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(self.base, "/reload", token=TOKEN, method="POST", payload={})
        self.assertEqual(caught.exception.code, 409)
        _, health = self.request(self.base, "/health", token=TOKEN)
        self.assertEqual(health["skills"], 2)
        _, listing = self.request(self.base, "/skills", token=TOKEN)
        self.assertNotIn("CON", {item["id"] for item in listing["skills"]})

    def test_reload_failure_preserves_previous_runtime_registry(self):
        write_skill(self.skills, "invalid-new", entry="index.js")
        (self.skills / "invalid-new" / "skill.json").write_text(
            json.dumps({"id": "invalid-new", "entry": "../outside.js"}), encoding="utf-8")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(self.base, "/reload", token=TOKEN, method="POST", payload={})
        self.assertEqual(caught.exception.code, 409)
        failure = json.loads(caught.exception.read().decode("utf-8"))
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["reloaded"], 2)
        _, listing = self.request(self.base, "/skills", token=TOKEN)
        names = {tool["name"] for skill in listing["skills"] for tool in skill["tools"]}
        self.assertIn("legacy-no-manifest__echo", names)
        self.assertIn("register-skill__echo", names)


if __name__ == "__main__":
    unittest.main()
