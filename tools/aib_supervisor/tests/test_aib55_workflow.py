"""Structural regression tests for the AIB-5.5 Linux workflow."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "aib55-linux-verify.yml"


class WorkflowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_only_and_read_only_permissions(self) -> None:
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s{2}push:")
        self.assertNotRegex(self.text, r"(?m)^\s{2}pull_request:")
        self.assertNotRegex(self.text, r"(?m)^\s{2}schedule:")
        self.assertIn("permissions:", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("secrets[", self.text)

    def test_canonical_partition_labels(self) -> None:
        expected = (
            [f"server-{index:02d}" for index in range(1, 13)]
            + [f"chat-{index:02d}" for index in range(1, 4)]
            + ["workspaces-a-01", "workspaces-a-02"]
            + ["workspaces-b", "workspaces-c"]
            + [f"ser-{index:02d}" for index in range(1, 10)]
        )
        self.assertEqual(len(expected), 28)
        labels = re.findall(r"label:\s*([A-Za-z0-9._-]+)", self.text)
        for label in expected:
            self.assertIn(label, labels)
        self.assertEqual(
            len([label for label in labels if label.startswith("server-")]),
            12,
        )
        self.assertEqual(
            len([label for label in labels if label.startswith("chat-")]),
            3,
        )
        self.assertEqual(
            len([label for label in labels if label.startswith("ser-")]),
            9,
        )

    def test_hidden_evidence_files_are_uploaded(self) -> None:
        self.assertEqual(
            self.text.count("include-hidden-files: true"),
            4,
        )
        self.assertNotIn("include-hidden-files: false", self.text)
        self.assertIn("path: .aib-ci/", self.text)
        self.assertIn("path: .aib-ci/aib55-evidence.json", self.text)

    def test_test_failures_propagate_to_job_status(self) -> None:
        self.assertEqual(self.text.count('exit "$status"'), 3)
        self.assertNotRegex(
            self.text,
            r"(?m)^\s{10}exit 0\s*$",
        )

    def test_aggregation_runs_after_partial_failures(self) -> None:
        self.assertGreaterEqual(self.text.count("if: always()"), 6)
        self.assertIn("needs: [posix-lane, general-tests, serialized-tests]", self.text)
        self.assertIn("name: aib55-targeted", self.text)
        self.assertIn("name: aib55-shard-${{ matrix.label }}", self.text)
        self.assertIn("name: aib55-linux-evidence", self.text)
        self.assertIn("pattern: aib55-targeted", self.text)
        self.assertIn("pattern: aib55-shard-*", self.text)
        self.assertIn("merge-multiple: true", self.text)

    def test_no_private_paths_or_credentials(self) -> None:
        self.assertNotRegex(
            self.text,
            r"(?i)C:\\\\Users\\\\|/home/[A-Za-z0-9._-]+/",
        )
        self.assertNotRegex(
            self.text,
            r"sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{10,}|"
            r"AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY",
        )


if __name__ == "__main__":
    unittest.main()
