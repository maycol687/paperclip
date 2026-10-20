"""Hermetic tests for the AIB-5.5 Linux evidence verifier."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aib_supervisor.verify_linux import (  # noqa: E402
    LinuxEvidenceError,
    TARGET_FILE,
    main,
    verify_evidence,
    verify_file,
)

COMMIT = "a" * 40


def valid_evidence() -> dict:
    return {
        "version": 1,
        "aib_task": "AIB-5.5",
        "status": "passed",
        "runner": {
            "os": "Linux",
            "os_name": "Ubuntu 24.04.1 LTS",
            "platform": "linux",
            "python_version": "3.12.8",
            "node_version": "v24.11.0",
            "pnpm_version": "9.15.4",
            "runner_name": "GitHub Actions 1",
            "runner_os": "Linux",
            "runner_arch": "X64",
        },
        "repository": {
            "commit_sha": COMMIT,
            "ref": "refs/heads/aib55-verify",
            "workflow": "AIB-5.5 Linux Verification",
            "job": "linux-verify",
            "run_id": "123456789",
            "run_attempt": "1",
        },
        "tests": {
            "targeted": {
                "file": TARGET_FILE,
                "command": (
                    "pnpm exec vitest run "
                    "packages/adapter-utils/src/acpx-engine/execute.test.ts"
                ),
                "exit_code": "0",
                "total": 3,
                "passed": 3,
                "failed": 0,
                "skipped": 0,
                "results": [
                    {"name": "remote cwd", "status": "passed"},
                    {"name": "session fingerprint", "status": "passed"},
                    {"name": "teardown", "status": "passed"},
                ],
            },
            "full": {
                "command": "pnpm test:run",
                "exit_code": "0",
                "log_sha256": "b" * 64,
                "log_bytes": 4096,
            },
        },
        "isolation": {
            "provider_calls": 0,
            "paperclip_requests": 0,
            "external_repository_writes": 0,
            "secrets_used": False,
        },
    }


class LinuxEvidenceTests(unittest.TestCase):
    def test_valid_linux_evidence_passes(self) -> None:
        result = verify_evidence(valid_evidence(), expected_commit=COMMIT)
        self.assertTrue(result["ok"], result["problems"])
        self.assertTrue(all(result["checks"].values()))

    def test_short_expected_commit_is_accepted(self) -> None:
        result = verify_evidence(
            valid_evidence(), expected_commit=COMMIT[:12]
        )
        self.assertTrue(result["ok"], result["problems"])

    def test_commit_mismatch_is_rejected(self) -> None:
        result = verify_evidence(
            valid_evidence(), expected_commit="c" * 40
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["commit_matches"])

    def test_short_commit_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["repository"]["commit_sha"] = "abc1234"
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["commit_sha"])

    def test_windows_evidence_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["runner"]["os"] = "Windows"
        payload["runner"]["platform"] = "win32"
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["runner_linux"])
        self.assertFalse(result["checks"]["runner_platform"])

    def test_skipped_remote_lane_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["tests"]["targeted"]["skipped"] = 80
        payload["tests"]["targeted"]["total"] = 83
        payload["tests"]["targeted"]["passed"] = 3
        payload["tests"]["targeted"]["results"].extend(
            {"name": f"posix-{index}", "status": "pending"}
            for index in range(80)
        )
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["targeted_skipped"])
        self.assertFalse(result["checks"]["targeted_results"])

    def test_failed_targeted_test_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["tests"]["targeted"]["failed"] = 1
        payload["tests"]["targeted"]["passed"] = 2
        payload["tests"]["targeted"]["results"][0]["status"] = "failed"
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["targeted_failed"])
        self.assertFalse(result["checks"]["targeted_results"])

    def test_targeted_exit_code_must_be_zero(self) -> None:
        payload = valid_evidence()
        payload["tests"]["targeted"]["exit_code"] = "1"
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["targeted_exit"])

    def test_full_suite_failure_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["tests"]["full"]["exit_code"] = "1"
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["full_exit"])

    def test_missing_full_log_hash_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["tests"]["full"]["log_sha256"] = ""
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["full_log_hash"])

    def test_provider_calls_are_rejected(self) -> None:
        payload = valid_evidence()
        payload["isolation"]["provider_calls"] = 1
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["isolation_provider_calls"])

    def test_paperclip_and_external_writes_are_rejected(self) -> None:
        payload = valid_evidence()
        payload["isolation"]["paperclip_requests"] = 1
        payload["isolation"]["external_repository_writes"] = 1
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["isolation_paperclip"])
        self.assertFalse(result["checks"]["isolation_external_writes"])

    def test_secrets_usage_is_rejected(self) -> None:
        payload = valid_evidence()
        payload["isolation"]["secrets_used"] = True
        result = verify_evidence(payload)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["isolation_secrets"])

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LinuxEvidenceError):
                verify_file(Path(tmp) / "missing.json")

    def test_malformed_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(LinuxEvidenceError):
                verify_file(path)

    def test_cli_writes_verification_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aib55-evidence.json"
            path.write_text(
                json.dumps(valid_evidence(), indent=2), encoding="utf-8"
            )
            self.assertEqual(
                main(
                    [
                        "--evidence",
                        str(path),
                        "--expected-commit",
                        COMMIT,
                    ]
                ),
                0,
            )
            verification = Path(tmp) / "aib55-verification.json"
            self.assertTrue(verification.exists())
            payload = json.loads(verification.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["commit_sha"], COMMIT)

    def test_cli_rejects_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = copy.deepcopy(valid_evidence())
            payload["status"] = "failed"
            path = Path(tmp) / "aib55-evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(main(["--evidence", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
