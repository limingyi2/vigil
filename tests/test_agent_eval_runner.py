"""Unit tests for the local Agent evaluation infrastructure."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_eval


class AgentEvalManifestTests(unittest.TestCase):
    def test_manifest_cases_and_references_are_valid(self) -> None:
        manifest = agent_eval.load_manifest()
        ids = {case["id"] for case in manifest["tests"]}
        self.assertEqual(len(ids), len(manifest["tests"]))
        for group in (*manifest["suites"].values(), *manifest["test_sets"].values()):
            self.assertTrue(set(group) <= ids)
        for case in manifest["tests"]:
            self.assertEqual(case["argv"][:3], ["{python}", "-m", "pytest"])
            self.assertGreater(case["timeout_seconds"], 0)
            self.assertTrue(case["acceptance"])

    def test_unknown_or_non_pytest_commands_are_rejected(self) -> None:
        manifest = agent_eval.load_manifest()
        self.assertNotIn("not-a-declared-case", {case["id"] for case in manifest["tests"]})
        with patch.object(agent_eval, "MANIFEST") as manifest_path:
            manifest_path.read_text.return_value = json.dumps({
                "schema_version": 1,
                "suites": {"smoke": ["bad"]},
                "tests": [{"id": "bad", "argv": ["powershell", "-Command", "Write-Host x"],
                           "timeout_seconds": 1, "acceptance": ["x"], "environments": ["local"],
                           "suite": "smoke"}],
            })
            with self.assertRaisesRegex(ValueError, "Python pytest argv"):
                agent_eval.load_manifest()

    def test_case_working_directory_must_stay_inside_repository(self) -> None:
        with patch.object(agent_eval, "MANIFEST") as manifest_path:
            manifest_path.read_text.return_value = json.dumps({
                "schema_version": 1,
                "suites": {"smoke": ["bad"]},
                "tests": [{"id": "bad", "argv": ["{python}", "-m", "pytest", "test.py"],
                           "timeout_seconds": 1, "acceptance": ["x"], "environments": ["local"],
                           "suite": "smoke", "cwd": "../outside"}],
            })
            with self.assertRaisesRegex(ValueError, "cwd escapes"):
                agent_eval.load_manifest()


class AgentEvalExecutionStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = {"id": "unit", "title": "unit case", "argv": ["{python}", "-m", "pytest", "tests"],
                     "timeout_seconds": 10}

    def test_all_skipped_is_not_run_and_skips_are_not_passes(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "1 skipped in 0.01s\n", "")
        with patch.object(agent_eval.subprocess, "run", return_value=completed):
            result = agent_eval.run_case(self.case, None)
        self.assertEqual(result["status"], "NOT RUN")
        self.assertEqual(result["test_counts"], {"skipped": 1})

    def test_pass_and_partial_skip_counts_are_separate(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "2 passed, 1 skipped in 0.01s\n", "")
        with patch.object(agent_eval.subprocess, "run", return_value=completed):
            result = agent_eval.run_case(self.case, None)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["test_counts"], {"passed": 2, "skipped": 1})

    def test_timeout_and_unlaunchable_command_are_distinct(self) -> None:
        with patch.object(agent_eval.subprocess, "run", side_effect=subprocess.TimeoutExpired(["pytest"], 10)):
            timed_out = agent_eval.run_case(self.case, None)
        with patch.object(agent_eval.subprocess, "run", side_effect=FileNotFoundError("pytest missing")):
            blocked = agent_eval.run_case(self.case, None)
        self.assertEqual(timed_out["status"], "TIMEOUT")
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("pytest missing", blocked["exception"])

    def test_collection_error_diagnostics_are_not_double_counted(self) -> None:
        output = "2 ERROR collecting broken_test.py\nModuleNotFoundError\n2 errors in 0.10s\n"
        completed = subprocess.CompletedProcess([], 2, output, "")
        with patch.object(agent_eval.subprocess, "run", return_value=completed):
            result = agent_eval.run_case(self.case, None)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["test_counts"], {"error": 2})


class AgentEvalComparisonAndStorageTests(unittest.TestCase):
    def test_comparison_finds_fixed_regression_and_not_run(self) -> None:
        common = {"selected_case_ids": ["fixed", "regressed"],
                  "environment": {"python": "3.x", "platform": "test"},
                  "results": [
                      {"id": "fixed", "argv": ["python", "-m", "pytest", "a"], "timeout_seconds": 10},
                      {"id": "regressed", "argv": ["python", "-m", "pytest", "b"], "timeout_seconds": 10},
                      {"id": "untouched", "argv": [], "timeout_seconds": 10},
                  ]}
        before = dict(common, results=[dict(common["results"][0], status="FAIL"),
                                       dict(common["results"][1], status="PASS"),
                                       dict(common["results"][2], status="NOT RUN")])
        after = dict(common, results=[dict(common["results"][0], status="PASS"),
                                      dict(common["results"][1], status="FAIL"),
                                      dict(common["results"][2], status="NOT RUN")])
        with tempfile.TemporaryDirectory() as tmp:
            before_path, after_path = Path(tmp) / "before.json", Path(tmp) / "after.json"
            before_path.write_text(json.dumps(before), encoding="utf-8")
            after_path.write_text(json.dumps(after), encoding="utf-8")
            result = agent_eval.compare_reports(before_path, after_path)
        outcomes = {row["id"]: row["classification"] for row in result["rows"]}
        self.assertTrue(result["same_conditions"])
        self.assertEqual(outcomes, {"fixed": "FIXED", "regressed": "REGRESSION", "untouched": "NOT RUN"})

    def test_comparison_flags_different_commands_as_different_conditions(self) -> None:
        base = {"selected_case_ids": ["case"], "environment": {"python": "3.x"},
                "results": [{"id": "case", "status": "PASS", "argv": ["pytest", "a"], "timeout_seconds": 10}]}
        changed = json.loads(json.dumps(base))
        changed["results"][0]["argv"] = ["pytest", "b"]
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text(json.dumps(base), encoding="utf-8")
            b.write_text(json.dumps(changed), encoding="utf-8")
            result = agent_eval.compare_reports(a, b)
        self.assertFalse(result["same_conditions"])
        changed["results"][0]["argv"] = ["pytest", "a"]
        changed["results"][0]["working_directory"] = "different-cwd"
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_text(json.dumps(base), encoding="utf-8")
            b.write_text(json.dumps(changed), encoding="utf-8")
            result = agent_eval.compare_reports(a, b)
        self.assertFalse(result["same_conditions"])

    def test_state_and_report_writes_cannot_target_the_checkout(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            agent_eval.outside_checkout(agent_eval.ROOT / "private-report.json", "report path")


if __name__ == "__main__":
    unittest.main()
