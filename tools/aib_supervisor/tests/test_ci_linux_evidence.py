"""Hermetic tests for the AIB-5.5 sharded evidence collector."""

from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aib_supervisor import ci_linux_evidence as collector  # noqa: E402
from aib_supervisor.verify_linux import verify_evidence  # noqa: E402

COMMIT = "a" * 40


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / ".aib-ci"
        self.work.mkdir()
        self.commit = mock.patch.object(
            collector, "_commit", return_value=COMMIT
        )
        self.commit.start()
        self.addCleanup(self.commit.stop)

    def write_targeted(
        self,
        *,
        exit_code: str = "0",
        statuses: tuple[str, ...] = ("passed", "passed"),
        payload: dict | None = None,
    ) -> None:
        (self.work / "targeted.exit").write_text(
            exit_code + "\n", encoding="utf-8"
        )
        report = payload or {
            "testResults": [
                {
                    "assertionResults": [
                        {"fullName": f"test {index}", "status": status}
                        for index, status in enumerate(statuses)
                    ]
                }
            ]
        }
        (self.work / "targeted.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    def write_shards(self, count: int = 28) -> None:
        for index in range(count):
            label = f"shard-{index:02d}"
            (self.work / f"{label}.exit").write_text("0\n", encoding="utf-8")
            (self.work / f"{label}.log").write_text(
                f"log {index}\n", encoding="utf-8"
            )
            (self.work / f"{label}.cmd").write_text(
                "pnpm test:run:general\n", encoding="utf-8"
            )

    def test_success_evidence(self) -> None:
        self.write_targeted()
        self.write_shards()
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["reasons"], [])
        self.assertEqual(evidence["tests"]["targeted"]["passed"], 2)
        self.assertEqual(evidence["tests"]["full"]["received_shards"], 28)
        self.assertEqual(evidence["tests"]["full"]["missing_shards"], 0)
        self.assertEqual(evidence["tests"]["full"]["exit_code"], "0")
        self.assertEqual(evidence["repository"]["commit_sha"], COMMIT)

    def test_missing_targeted_report_still_builds_failure_evidence(self) -> None:
        self.write_shards()
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("targeted_missing", evidence["reasons"])
        self.assertEqual(evidence["tests"]["targeted"]["total"], 0)

    def test_invalid_targeted_json_is_recorded(self) -> None:
        (self.work / "targeted.exit").write_text("0\n", encoding="utf-8")
        (self.work / "targeted.json").write_text("{bad", encoding="utf-8")
        self.write_shards()
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("targeted_invalid", evidence["reasons"])

    def test_targeted_failures_and_skips_are_recorded(self) -> None:
        self.write_targeted(statuses=("failed", "pending"))
        self.write_shards()
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("targeted_failed", evidence["reasons"])
        self.assertIn("targeted_skipped", evidence["reasons"])
        self.assertEqual(evidence["tests"]["targeted"]["failed"], 1)
        self.assertEqual(evidence["tests"]["targeted"]["skipped"], 1)

    def test_missing_shard_is_recorded(self) -> None:
        self.write_targeted()
        self.write_shards(27)
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("shards_missing", evidence["reasons"])
        self.assertEqual(evidence["tests"]["full"]["received_shards"], 27)
        self.assertEqual(evidence["tests"]["full"]["missing_shards"], 1)
        self.assertEqual(evidence["tests"]["full"]["exit_code"], "1")

    def test_timeout_shard_is_recorded(self) -> None:
        self.write_targeted()
        self.write_shards()
        (self.work / "shard-03.exit").write_text("124\n", encoding="utf-8")
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("shard_exit", evidence["reasons"])
        self.assertIn("shard_timeout", evidence["reasons"])
        self.assertIn("shard-03", evidence["tests"]["full"]["timed_out_shards"])
        shard = next(
            item
            for item in evidence["tests"]["full"]["shards"]
            if item["id"] == "shard-03"
        )
        self.assertTrue(shard["timed_out"])
        self.assertEqual(shard["exit_code"], "124")

    def test_missing_shard_log_is_recorded(self) -> None:
        self.write_targeted()
        self.write_shards()
        (self.work / "shard-05.log").unlink()
        evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("shard_log_missing", evidence["reasons"])
        shard = next(
            item
            for item in evidence["tests"]["full"]["shards"]
            if item["id"] == "shard-05"
        )
        self.assertFalse(shard["log_present"])
        self.assertEqual(shard["log_bytes"], 0)

    def test_missing_commit_is_recorded(self) -> None:
        self.write_targeted()
        self.write_shards()
        with mock.patch.object(collector, "_commit", return_value=""):
            evidence = collector.collect(self.root, {})
        self.assertEqual(evidence["status"], "failed")
        self.assertIn("commit_missing", evidence["reasons"])

    def test_write_evidence_survives_missing_output_env(self) -> None:
        evidence = collector.collect(self.root, {})
        path = collector.write_evidence(self.root, evidence, {})
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")

    def test_write_evidence_sets_github_output(self) -> None:
        self.write_targeted()
        self.write_shards()
        output = self.work / "gh_output.txt"
        summary = self.work / "summary.md"
        environ = {
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        evidence, path = collector.run(self.root, environ)
        self.assertEqual(evidence["status"], "passed")
        self.assertTrue(path.exists())
        self.assertIn("evidence_ok=1", output.read_text(encoding="utf-8"))
        self.assertIn("status: `passed`", summary.read_text(encoding="utf-8"))

    def test_failed_run_sets_zero_output_and_writes_evidence(self) -> None:
        self.write_targeted()
        self.write_shards(27)
        output = self.work / "gh_output.txt"
        evidence, path = collector.run(
            self.root, {"GITHUB_OUTPUT": str(output)}
        )
        self.assertEqual(evidence["status"], "failed")
        self.assertTrue(path.exists())
        self.assertIn("evidence_ok=0", output.read_text(encoding="utf-8"))

    def test_main_returns_status_codes(self) -> None:
        self.write_targeted()
        self.write_shards()
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main([]), 0)
            (self.work / "shard-00.exit").write_text("1\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main([]), 2)
        finally:
            os.chdir(previous)

    def test_simulated_28_shard_success_and_failure(self) -> None:
        self.write_targeted()
        self.write_shards()
        environ = {
            "GITHUB_RUN_ID": "1",
            "GITHUB_WORKFLOW": "AIB-5.5 Linux Verification",
            "GITHUB_JOB": "evidence",
        }
        with mock.patch.object(
            collector.platform, "system", return_value="Linux"
        ), mock.patch.object(collector.sys, "platform", "linux"), mock.patch.object(
            collector, "_os_name", return_value="Ubuntu 24.04.5 LTS"
        ):
            passed = collector.collect(self.root, environ)
        passed_check = verify_evidence(passed, expected_commit=COMMIT)
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(passed["tests"]["full"]["received_shards"], 28)
        self.assertTrue(passed_check["ok"], passed_check["problems"])

        (self.work / "shard-12.exit").write_text("1\n", encoding="utf-8")
        with mock.patch.object(
            collector.platform, "system", return_value="Linux"
        ), mock.patch.object(collector.sys, "platform", "linux"), mock.patch.object(
            collector, "_os_name", return_value="Ubuntu 24.04.5 LTS"
        ):
            failed = collector.collect(self.root, environ)
        failed_check = verify_evidence(failed, expected_commit=COMMIT)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("shard_exit", failed["reasons"])
        self.assertEqual(failed["tests"]["full"]["exit_code"], "1")
        self.assertFalse(failed_check["ok"])
        self.assertFalse(failed_check["checks"]["status_passed"])
        self.assertFalse(failed_check["checks"]["full_exit"])
        self.assertFalse(failed_check["checks"]["full_shards_ok"])


if __name__ == "__main__":
    unittest.main()
