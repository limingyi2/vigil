# -*- coding: utf-8 -*-
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestSkillHostTemplate(unittest.TestCase):
    def test_template_loads_and_calls_in_real_node_host(self):
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        with tempfile.TemporaryDirectory(prefix="vigil-skill-host-") as td:
            skills = pathlib.Path(td) / "skills"
            shutil.copytree(ROOT / "templates" / "skill-basic", skills / "skill-basic")
            port = free_port()
            env = os.environ.copy()
            env.update({"ZX_SKILL_DIR": str(skills), "ZX_SKILL_PORT": str(port),
                        "ZX_SKILL_HOST": "127.0.0.1"})
            proc = subprocess.Popen(
                ["node", str(ROOT / "brain" / "skill_host" / "server.js")],
                cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
            try:
                base = "http://127.0.0.1:%d" % port
                health = listing = None
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(base + "/health", timeout=1) as response:
                            health = json.loads(response.read())
                        with urllib.request.urlopen(base + "/skills", timeout=1) as response:
                            listing = json.loads(response.read())
                        if health.get("ok"):
                            break
                    except Exception:
                        time.sleep(0.05)
                self.assertTrue(health and health.get("ok"), health)
                self.assertEqual(health.get("skills"), 1)
                tools = listing["skills"][0]["tools"]
                self.assertIn("skill-basic__echo", [t["name"] for t in tools])
                body = json.dumps({"tool": "skill-basic__echo",
                                   "args": {"text": "template-ok"}, "ctx": {}}).encode()
                req = urllib.request.Request(base + "/skills/call", data=body,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as response:
                    called = json.loads(response.read())
                self.assertTrue(called.get("ok"), called)
                self.assertEqual(called.get("content"), "template-ok")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
