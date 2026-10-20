# AIB-5.5 Linux verification

Status: `AWAITING_LINUX_RUN`. The workflow and local evidence verifier are
prepared and tested; no Linux run has been executed or fabricated.

## What AIB-5.5 verifies

`packages/adapter-utils/src/acpx-engine/execute.test.ts` contains two groups
that are skipped on Windows:

- `posixOnlyIt`, defined as `it.skip` on `win32` and `it` elsewhere (80 cases).
- `it.skipIf(process.platform === "win32")` cases for ACPX skill symlinks
  (5 cases).

On Linux both groups must run. The acceptance signal is a targeted Vitest run
of that file with `failed: 0` and `skipped: 0`, plus a passing full
`pnpm test:run` on the same commit.

The suite is not assumed to be Linux-compatible as a whole: the workflow runs
the targeted file first, then the repository's full suite, and records both
results separately. If the full suite fails for an unrelated reason, the
evidence reports `failed` and AIB-5.5 stays open.

## Workflow

`.github/workflows/aib55-linux-verify.yml`:

- `on: workflow_dispatch` only.
- `permissions: contents: read`.
- `runs-on: ubuntu-latest`, job timeout 60 minutes.
- Node 24.11.0, pnpm 9.15.4, Python 3.12 for the evidence collector.
- `pnpm install --frozen-lockfile` and
  `pnpm --filter @paperclipai/plugin-sdk ensure-build-deps`.
- No secrets, AI providers, Paperclip API, deployments, or external writes.
- Checkout with `persist-credentials: false`.

Commands executed on Ubuntu:

```bash
pnpm install --frozen-lockfile
pnpm --filter @paperclipai/plugin-sdk ensure-build-deps
pnpm exec vitest run packages/adapter-utils/src/acpx-engine/execute.test.ts \
  --reporter=default --reporter=json \
  --outputFile=.aib-ci/aib55-remote-lane.json
pnpm test:run
```

The workflow writes `.aib-ci/aib55-evidence.json`, appends a summary to the job
summary, uploads the `aib55-linux-evidence` artifact for 30 days, and fails the
job unless both commands exit 0 and the targeted file records zero skipped and
zero failed tests.

## Evidence schema

```json
{
  "version": 1,
  "aib_task": "AIB-5.5",
  "status": "passed",
  "runner": {
    "os": "Linux",
    "os_name": "Ubuntu ...",
    "platform": "linux",
    "python_version": "3.12.x",
    "node_version": "v24.11.0",
    "pnpm_version": "9.15.4",
    "runner_name": "...",
    "runner_os": "Linux",
    "runner_arch": "X64"
  },
  "repository": {
    "commit_sha": "...",
    "ref": "refs/heads/...",
    "workflow": "AIB-5.5 Linux Verification",
    "job": "linux-verify",
    "run_id": "...",
    "run_attempt": "..."
  },
  "tests": {
    "targeted": {
      "file": "packages/adapter-utils/src/acpx-engine/execute.test.ts",
      "exit_code": "0",
      "total": 0,
      "passed": 0,
      "failed": 0,
      "skipped": 0,
      "results": [{"name": "...", "status": "passed"}]
    },
    "full": {
      "command": "pnpm test:run",
      "exit_code": "0",
      "log_sha256": "...",
      "log_bytes": 0
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

## Local verification

Download the artifact, then run:

```powershell
python tools\aib_supervisor\verify_linux.py `
  --evidence .\aib55-evidence.json `
  --expected-commit <full-sha>
```

Exit code 0 writes `aib55-verification.json` next to the evidence. Windows
evidence, skipped tests, failed tests, nonzero exits, a commit mismatch,
provider calls, Paperclip requests, external writes, or secret usage are
rejected with exit code 2.

## Completion rule

AIB-5.5 may be marked complete only after:

1. The workflow ran on a real Ubuntu runner from the exact commit.
2. The artifact downloads and `verify_linux.py` exits 0.
3. The roadmap records the run id, commit, and artifact hash.

Creating or testing the workflow is not completion.
