"""Validate the GitHub Actions evidence required to close AIB-5.5.

This verifier never runs Linux itself. It checks a downloaded evidence file
from the manual ``AIB-5.5 Linux Verification`` workflow and writes a local
verification record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TARGET_FILE = "packages/adapter-utils/src/acpx-engine/execute.test.ts"
FULL_COMMAND = "pnpm test:run"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class LinuxEvidenceError(RuntimeError):
    """Raised when the evidence file cannot be read or parsed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _exit_zero(value: Any) -> bool:
    return str(value).strip() == "0"


def verify_evidence(
    payload: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    details: list[str] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks[name] = bool(condition)
        if not condition:
            details.append(detail)

    runner = payload.get("runner") or {}
    repository = payload.get("repository") or {}
    tests = payload.get("tests") or {}
    targeted = tests.get("targeted") or {}
    full = tests.get("full") or {}
    isolation = payload.get("isolation") or {}

    check(
        "schema_version",
        payload.get("version") in {1, 2},
        "version must be 1 or 2",
    )
    check(
        "aib_task",
        payload.get("aib_task") == "AIB-5.5",
        "aib_task must be AIB-5.5",
    )
    check(
        "status_passed",
        payload.get("status") == "passed",
        "evidence status must be passed",
    )
    check(
        "runner_linux",
        _text(runner.get("os")) == "Linux",
        "runner.os must be Linux",
    )
    check(
        "runner_platform",
        _text(runner.get("platform")) == "linux",
        "runner.platform must be linux",
    )
    check(
        "runner_os_name",
        bool(_text(runner.get("os_name"))),
        "runner.os_name must name the Ubuntu image",
    )
    check(
        "runner_python",
        _text(runner.get("python_version")) not in {"", "unknown"},
        "runner.python_version must be recorded",
    )

    commit = _text(repository.get("commit_sha")).lower()
    check(
        "commit_sha",
        bool(_COMMIT_RE.fullmatch(commit)),
        "repository.commit_sha must be a full git SHA",
    )
    if expected_commit:
        expected = expected_commit.strip().lower()
        check(
            "commit_matches",
            bool(expected)
            and len(expected) >= 7
            and (commit.startswith(expected) or expected.startswith(commit)),
            "commit does not match the expected commit",
        )
    check(
        "run_identity",
        bool(_text(repository.get("run_id")))
        and bool(_text(repository.get("workflow")))
        and bool(_text(repository.get("job"))),
        "run_id, workflow, and job must be recorded",
    )

    results = targeted.get("results") or []
    check(
        "targeted_file",
        _text(targeted.get("file")) == TARGET_FILE,
        "targeted.file must be the ACPX execute test",
    )
    check(
        "targeted_exit",
        _exit_zero(targeted.get("exit_code")),
        "targeted.exit_code must be 0",
    )
    check(
        "targeted_total",
        isinstance(targeted.get("total"), int)
        and int(targeted.get("total")) == len(results)
        and len(results) > 0,
        "targeted.total must match a non-empty result list",
    )
    check(
        "targeted_failed",
        targeted.get("failed") == 0,
        "targeted.failed must be 0",
    )
    check(
        "targeted_skipped",
        targeted.get("skipped") == 0,
        "targeted.skipped must be 0 on Linux",
    )
    check(
        "targeted_results",
        isinstance(results, list)
        and all(
            isinstance(item, Mapping) and item.get("status") == "passed"
            for item in results
        ),
        "every targeted test result must be passed",
    )

    if full.get("mode") == "canonical-sharded":
        shards = full.get("shards") or []
        expected = full.get("expected_shards")
        check(
            "full_sharded_mode",
            _text(full.get("command"))
            == "pnpm test:run:general + pnpm test:run:serialized",
            "full.command must name the canonical sharded suites",
        )
        check(
            "full_expected_shards",
            expected == 28,
            "full.expected_shards must be 28",
        )
        check(
            "full_received_shards",
            isinstance(shards, list)
            and expected == 28
            and len(shards) == expected,
            "full.shards must contain all 28 canonical shards",
        )
        check(
            "full_exit",
            _exit_zero(full.get("exit_code")),
            "full.exit_code must be 0",
        )
        check(
            "full_no_missing_shards",
            full.get("missing_shards") == 0,
            "full.missing_shards must be 0",
        )
        check(
            "full_shards_ok",
            isinstance(shards, list)
            and len(shards) > 0
            and all(
                isinstance(shard, Mapping)
                and _exit_zero(shard.get("exit_code"))
                and shard.get("timed_out") is False
                and shard.get("log_present") is True
                and bool(_HASH_RE.fullmatch(_text(shard.get("log_sha256"))))
                and isinstance(shard.get("log_bytes"), int)
                and int(shard.get("log_bytes")) > 0
                for shard in shards
            ),
            "every shard must exit 0 with a logged, timed-out-free result",
        )
        check(
            "full_no_timed_out_shards",
            full.get("timed_out_shards") == [],
            "full.timed_out_shards must be empty",
        )
    else:
        check(
            "full_command",
            _text(full.get("command")) == FULL_COMMAND,
            f"full.command must be {FULL_COMMAND}",
        )
        check(
            "full_exit",
            _exit_zero(full.get("exit_code")),
            "full.exit_code must be 0",
        )
        check(
            "full_log_hash",
            bool(_HASH_RE.fullmatch(_text(full.get("log_sha256")))),
            "full.log_sha256 must be a SHA-256 hex digest",
        )
        check(
            "full_log_bytes",
            isinstance(full.get("log_bytes"), int)
            and int(full.get("log_bytes")) > 0,
            "full.log_bytes must be positive",
        )

    check(
        "isolation_provider_calls",
        isolation.get("provider_calls") == 0,
        "no AI provider may be called",
    )
    check(
        "isolation_paperclip",
        isolation.get("paperclip_requests") == 0,
        "no Paperclip request may be made",
    )
    check(
        "isolation_external_writes",
        isolation.get("external_repository_writes") == 0,
        "no external repository write may occur",
    )
    check(
        "isolation_secrets",
        isolation.get("secrets_used") is False,
        "no secret may be used",
    )

    return {
        "ok": all(checks.values()),
        "aib_task": "AIB-5.5",
        "commit_sha": commit,
        "run_id": repository.get("run_id"),
        "status": payload.get("status"),
        "tests": {
            "total": targeted.get("total"),
            "passed": targeted.get("passed"),
            "failed": targeted.get("failed"),
            "skipped": targeted.get("skipped"),
            "suite_mode": full.get("mode") or "single",
            "suite_shards": (
                len(full.get("shards") or [])
                if full.get("mode") == "canonical-sharded"
                else None
            ),
        },
        "checks": checks,
        "problems": details,
    }


def verify_file(
    path: Path,
    *,
    expected_commit: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LinuxEvidenceError(f"evidence is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise LinuxEvidenceError("evidence must be a JSON object")
    result = verify_evidence(payload, expected_commit=expected_commit)
    result["evidence_path"] = str(Path(path).resolve())
    result["evidence_sha256"] = hashlib.sha256(
        Path(path).read_bytes()
    ).hexdigest()
    result["verified_at"] = _now_iso()
    if write:
        target = Path(path).with_name("aib55-verification.json")
        target.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["verification_path"] = str(target.resolve())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify downloaded AIB-5.5 Linux evidence"
    )
    parser.add_argument("--evidence", required=True, help="aib55-evidence.json")
    parser.add_argument("--expected-commit", default=None)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_file(
            Path(args.evidence),
            expected_commit=args.expected_commit,
            write=not args.no_write,
        )
    except LinuxEvidenceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
