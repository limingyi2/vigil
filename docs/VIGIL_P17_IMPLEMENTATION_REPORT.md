# Vigil P17 Coding Agent

## Architecture

P17 adds `brain/coding_harness.py` as the project execution layer behind
`agent_runs.py`.

```text
PLAN → IMPLEMENT → VERIFY → DIAGNOSE → REPAIR → VERIFY → COMPLETE
```

The harness owns the project root, index, plan, patch receipts, command
results, diagnostics, repair count and final diff. `agent_runs` remains the
durable lifecycle owner, so pause, resume, cancel, interject, event cursors and
context recovery continue to use the existing ledger.

### Main modules

- `WorkspaceGuard`: real path containment, stale lock recovery and concurrent
  Coding Agent protection.
- `ProjectIndexer`: files, manifests, dependency hints, symbols and bounded
  source snapshots.
- `PatchEngine`: atomic UTF-8 writes, strict unified diffs, expected hash checks,
  bounded backups and restore records.
- `TestRunner`: workspace cwd, real stdout/stderr/exit code, timeout and a
  development command safety gate.
- `CodingHarness`: complexity gate, structured plan, implementation operations,
  real verification, failure diagnosis, bounded repair and optional dynamic
  replanning.

## Starting a coding run

The existing `POST /api/agent/runs` endpoint accepts the normal run fields plus:

```json
{
  "kind": "coding",
  "workspace_id": "<selected workspace id>",
  "coding": {
    "max_repairs": 2,
    "verify_commands": ["python -m pytest -q"]
  }
}
```

The server resolves `workspace_id` to the user-owned workspace record. Direct
`workspace_root` is supported for local integrations and tests, but the normal
client path sends a selected workspace id. Development shaped requests in a
workspace conversation are automatically routed to `kind=coding`.

Simple requests skip the extra planner model call. Moderate and complex tasks
receive a structured plan; complex tasks include project analysis before
implementation. Model reasoning still uses the existing `fast`, `balanced` and
`deep` strategy mapping.

## Evidence exposed to the client

The ledger stores `coding.plan`, `coding.phase`, `coding.tests`,
`coding.diagnostics`, `coding.repair_count`, `coding.changed_files`, and the
final `coding.diff`. SSE events named `coding_*` are projected into the current
chat card. The card shows the plan, files, test exit results, repair count and a
collapsible diff without changing the surrounding chat layout.

## Verification

The P17 test includes a real two-file change where the implementation writes an
incorrect operator, `pytest` fails, the diagnostic callback identifies the
failure, the repair writes the correct operator, and `pytest` passes on the
second run. A separate case proves failed verification cannot be reported as
success. Workspace escape, atomic unified patches and restore records are also
covered.
