# AIB-5.5 Linux verification

Status: `AWAITING_LINUX_RUN`. The sharded workflow and collector are fixed and
tested locally; they are not published or executed yet.

## What AIB-5.5 verifies

`packages/adapter-utils/src/acpx-engine/execute.test.ts` contains two groups
that are skipped on Windows:

- `posixOnlyIt`, defined as `it.skip` on `win32` and `it` elsewhere (80 cases).
- `it.skipIf(process.platform === "win32")` cases for ACPX skill symlinks
  (5 cases).

On Linux both groups must run. The acceptance signal is a targeted Vitest run
of that file with `failed: 0` and `skipped: 0`, plus the full repository suite
on the same commit.

## Why the first run failed

Run `39379947469` (commit `18dc7e1e`) failed for two independent reasons:

1. The unsharded `pnpm test:run` hit the 45-minute step timeout. The repo
   itself partitions this suite in CI; its own history records that an
   unsharded run also exceeded 60 minutes.
2. The inline Python collector used `os.environ` without `import os`, so it
   crashed with `NameError` and wrote no evidence. The artifact upload found
   an empty `.aib-ci/` and the gate failed.

## Workflow strategy

`.github/workflows/aib55-linux-verify.yml` now runs:

- `posix-lane`: the targeted ACPX test file.
- `general-tests`: the 19 canonical general CI partitions (12 server shards,
  3 chat shards, 2 workspaces-a shards, workspaces-b, workspaces-c).
- `serialized-tests`: the 9 canonical serialized server shards.
- `evidence`: aggregates every job's artifacts and writes the final evidence.

The 28 shards are the same partitions `pr-trusted.yml` uses for the complete
suite. No test group is removed; the single unsharded job is replaced by the
canonical partition set.

Every test job runs its command under GNU `timeout`, which writes the command's
exit code to `.aib-ci/<label>.exit` before the GitHub step timeout can kill the
runner. The log and command line are written next to it. Each job uploads its
`.aib-ci` directory with `if: always()`.

The `evidence` job downloads all artifacts with `if: always()` and runs
`tools/aib_supervisor/ci_linux_evidence.py`. The collector always writes
`aib55-evidence.json`, including when files are missing, JSON is invalid, a
shard times out, or a job fails. The final artifact `aib55-linux-evidence`
contains that JSON.

Triggers and permissions are unchanged: `workflow_dispatch` only,
`permissions: contents: read`, no secrets, no AI providers, no Paperclip, no
deployments, no external repository writes.

## Timeouts

| Scope | Job | Test step | Command wrapper |
| --- | --- | --- | --- |
| Targeted lane | 20 min | 13 min | 10 min |
| General shard | 30 min | 22 min | 20 min |
| Serialized shard | 25 min | 18 min | 15 min |
| Evidence | 15 min | - | - |

Typical wall-clock time is 15-20 minutes because the slowest shard and the
aggregation run in parallel/after the shards. Worst case is about 35-40 minutes;
each test command still has a hard 20-minute (general) or 15-minute
(serialized) wrapper.

## Evidence schema

```json
{
  "version": 2,
  "aib_task": "AIB-5.5",
  "status": "passed",
  "reasons": [],
  "runner": {"os": "Linux", "platform": "linux", "...": "..."},
  "repository": {"commit_sha": "...", "run_id": "...", "job": "evidence"},
  "tests": {
    "targeted": {
      "file": "packages/adapter-utils/src/acpx-engine/execute.test.ts",
      "exit_code": "0",
      "total": 246,
      "passed": 246,
      "failed": 0,
      "skipped": 0,
      "results": [{"name": "...", "status": "passed"}]
    },
    "full": {
      "mode": "canonical-sharded",
      "command": "pnpm test:run:general + pnpm test:run:serialized",
      "expected_shards": 28,
      "received_shards": 28,
      "missing_shards": 0,
      "exit_code": "0",
      "timed_out_shards": [],
      "shards": [
        {
          "id": "server-01",
          "command": "pnpm test:run:general -- --group general-server-without-chat --shard-index 0 --shard-count 12",
          "exit_code": "0",
          "timed_out": false,
          "log_present": true,
          "log_sha256": "...",
          "log_bytes": 12345
        }
      ]
    }
  },
  "isolation": {
    "provider_calls": 0,
    "paperclip_requests": 0,
    "external_repository_writes": 0,
    "secrets_used": false
  }
}
```

A failed run still produces this file with `status: failed`, a `reasons` list
(`targeted_exit`, `targeted_failed`, `targeted_skipped`, `shards_missing`,
`shard_exit`, `shard_timeout`, `shard_log_missing`, `commit_missing`), and the
per-shard exit codes. Those codes let the owner diagnose a failure without a
second run.

## Local verification

Download the artifact, then run:

```powershell
python tools\aib_supervisor\verify_linux.py `
  --evidence .\aib55-evidence.json `
  --expected-commit <full-sha>
```

Exit code 0 writes `aib55-verification.json` next to the evidence. Windows
evidence, skipped or failed targeted tests, missing/timed-out shards, a commit
mismatch, provider calls, Paperclip requests, external writes, or secret usage
are rejected with exit code 2.

## Completion rule

AIB-5.5 may be marked complete only after:

1. The sharded workflow ran on a real Ubuntu runner from the exact commit.
2. The `aib55-linux-evidence` artifact downloads.
3. `verify_linux.py` exits 0 with that commit.
4. The roadmap records the run id, commit, and evidence hash.

Creating or testing the workflow is not completion.
