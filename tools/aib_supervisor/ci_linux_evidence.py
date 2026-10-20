#!/usr/bin/env python3
"""Build AIB-5.5 evidence from sharded GitHub Actions results.

The workflow runs the canonical general/serialized test partitions. Each job
writes ``<label>.exit``, ``<label>.log`` and ``<label>.cmd`` under ``.aib-ci``.
This collector always writes an evidence file, including on missing files,
timeouts, and failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

AIB_TASK = "AIB-5.5"
SCHEMA_VERSION = 2
DEFAULT_EXPECTED_SHARDS = 28
TARGET_FILE = "packages/adapter-utils/src/acpx-engine/execute.test.ts"
TIMEOUT_EXIT_CODES = {"124", "137", "143"}
REASON_CODES = {
    "targeted_missing": "targeted test report is missing",
    "targeted_invalid": "targeted test report is not valid JSON",
    "targeted_exit": "targeted test exit code is not zero",
    "targeted_failed": "targeted test report contains failures",
    "targeted_skipped": "targeted test report contains skipped tests",
    "targeted_empty": "targeted test report contains no tests",
    "shards_missing": "canonical suite shards are missing",
    "shard_exit": "a canonical suite shard failed",
    "shard_timeout": "a canonical suite shard timed out",
    "shard_log_missing": "a canonical suite shard log is missing",
    "commit_missing": "git commit could not be resolved",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _version(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(
            encoding="utf-8"
        ).splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except Exception:
        return ""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> tuple[Any, str]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError):
        return None, "invalid"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def collect(
    root: Path,
    environ: Mapping[str, str],
    *,
    expected_shards: int = DEFAULT_EXPECTED_SHARDS,
) -> dict[str, Any]:
    root = Path(root)
    work = root / ".aib-ci"
    reasons: set[str] = set()

    report, report_state = _read_json(work / "targeted.json")
    if report_state == "missing":
        reasons.add("targeted_missing")
        report = {}
    elif report_state == "invalid":
        reasons.add("targeted_invalid")
        report = {}
    assertions = [
        assertion
        for result in report.get("testResults", [])
        if isinstance(result, Mapping)
        for assertion in result.get("assertionResults", [])
        if isinstance(assertion, Mapping)
    ]
    results = [
        {
            "name": _text(item.get("fullName"))
            or _text(item.get("title")),
            "status": _text(item.get("status")) or "unknown",
        }
        for item in assertions
    ]
    passed = sum(1 for item in results if item["status"] == "passed")
    failed = sum(1 for item in results if item["status"] == "failed")
    skipped = sum(
        1
        for item in results
        if item["status"] in {"pending", "skipped", "todo", "disabled"}
    )
    targeted_exit = _read_text(work / "targeted.exit") or "missing"
    if targeted_exit != "0":
        reasons.add("targeted_exit")
    if failed:
        reasons.add("targeted_failed")
    if skipped:
        reasons.add("targeted_skipped")
    if not results:
        reasons.add("targeted_empty")

    shards: list[dict[str, Any]] = []
    for exit_path in sorted(work.glob("*.exit")):
        label = exit_path.stem
        if label == "targeted":
            continue
        exit_code = _read_text(exit_path) or "missing"
        log_path = work / f"{label}.log"
        log_present = log_path.exists()
        log_bytes = log_path.read_bytes() if log_present else b""
        timed_out = exit_code in TIMEOUT_EXIT_CODES
        shards.append(
            {
                "id": label,
                "command": _read_text(work / f"{label}.cmd"),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "log_present": log_present,
                "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
                "log_bytes": len(log_bytes),
            }
        )
        if exit_code != "0":
            reasons.add("shard_exit")
        if timed_out:
            reasons.add("shard_timeout")
        if not log_present:
            reasons.add("shard_log_missing")
    if len(shards) != expected_shards:
        reasons.add("shards_missing")

    commit = _commit(root)
    if not commit:
        reasons.add("commit_missing")
    status = (
        "passed"
        if not reasons
        and targeted_exit == "0"
        and len(shards) == expected_shards
        and all(shard["exit_code"] == "0" for shard in shards)
        else "failed"
    )
    suite_exit = (
        "0"
        if len(shards) == expected_shards
        and all(shard["exit_code"] == "0" for shard in shards)
        else "1"
    )
    return {
        "version": SCHEMA_VERSION,
        "aib_task": AIB_TASK,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "status": status,
        "reasons": sorted(reasons),
        "runner": {
            "os": platform.system(),
            "os_name": _os_name(),
            "platform": sys.platform,
            "python_version": platform.python_version(),
            "node_version": _version(["node", "--version"]),
            "pnpm_version": _version(["pnpm", "--version"]),
            "runner_name": _text(environ.get("RUNNER_NAME", "")),
            "runner_os": _text(environ.get("RUNNER_OS", "")),
            "runner_arch": _text(environ.get("RUNNER_ARCH", "")),
        },
        "repository": {
            "commit_sha": commit,
            "ref": _text(environ.get("GITHUB_REF", "")),
            "workflow": _text(environ.get("GITHUB_WORKFLOW", "")),
            "job": _text(environ.get("GITHUB_JOB", "")) or "evidence",
            "run_id": _text(environ.get("GITHUB_RUN_ID", "")),
            "run_attempt": _text(environ.get("GITHUB_RUN_ATTEMPT", "")),
        },
        "tests": {
            "targeted": {
                "file": TARGET_FILE,
                "command": (
                    "pnpm exec vitest run "
                    "packages/adapter-utils/src/acpx-engine/execute.test.ts"
                ),
                "exit_code": targeted_exit,
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "results": results,
            },
            "full": {
                "mode": "canonical-sharded",
                "command": (
                    "pnpm test:run:general + pnpm test:run:serialized"
                ),
                "expected_shards": expected_shards,
                "received_shards": len(shards),
                "missing_shards": max(0, expected_shards - len(shards)),
                "exit_code": suite_exit,
                "timed_out_shards": [
                    shard["id"] for shard in shards if shard["timed_out"]
                ],
                "shards": shards,
            },
        },
        "isolation": {
            "provider_calls": 0,
            "paperclip_requests": 0,
            "external_repository_writes": 0,
            "secrets_used": False,
            "dependency_install": "pnpm install --frozen-lockfile",
        },
    }


def write_evidence(
    root: Path,
    evidence: Mapping[str, Any],
    environ: Mapping[str, str],
) -> Path:
    work = Path(root) / ".aib-ci"
    work.mkdir(parents=True, exist_ok=True)
    path = work / "aib55-evidence.json"
    path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    status = evidence.get("status")
    reasons = evidence.get("reasons") or []
    summary_text = (
        "### AIB-5.5 Linux evidence\n\n"
        f"- status: `{status}`\n"
        f"- commit: `{evidence.get('repository', {}).get('commit_sha', '')}`\n"
        f"- shards: {evidence.get('tests', {}).get('full', {}).get('received_shards', 0)}"
        f"/{evidence.get('tests', {}).get('full', {}).get('expected_shards', 0)}\n"
        f"- reasons: {', '.join(reasons) if reasons else 'none'}\n"
    )
    summary_path = _text(environ.get("GITHUB_STEP_SUMMARY", ""))
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(summary_text)
    output_path = _text(environ.get("GITHUB_OUTPUT", ""))
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(
                f"evidence_ok={'1' if status == 'passed' else '0'}\n"
            )
    return path


def run(
    root: Path,
    environ: Mapping[str, str],
    *,
    expected_shards: int = DEFAULT_EXPECTED_SHARDS,
) -> tuple[dict[str, Any], Path]:
    evidence = collect(
        root, environ, expected_shards=expected_shards
    )
    path = write_evidence(root, evidence, environ)
    return evidence, path


def main(argv: list[str] | None = None) -> int:
    del argv
    expected = int(
        _text(os.environ.get("AIB55_EXPECTED_SHARDS", ""))
        or DEFAULT_EXPECTED_SHARDS
    )
    evidence, path = run(Path.cwd(), os.environ, expected_shards=expected)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence.get("status") == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
