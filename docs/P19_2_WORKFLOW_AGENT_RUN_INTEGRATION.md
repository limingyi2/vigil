# P19.2 Workflow / Agent Run integration

## Merge dependency

This branch is based on `dev` and intentionally does not include P19.1 commits.
PR #32 (`codex/p19-1-routing-contract`) must finish independent acceptance and
merge before this PR can be merged. P19.1 currently remains open; do not merge
this branch into `dev` ahead of that prerequisite.

## Scope and source of complexity

The P18 `task_workflows` service remains the Todo, attempt, run-link and
checkpoint persistence owner. `agent_runs` remains the only executor and
authoritative run lifecycle. P19.2 does not add an executor, scheduler, route
service, or database.

Only a validated `CodingHarness` plan whose existing structured result says
`complex` and contains at least two steps is projected into a P18 Workflow.
The projection rejects invalid steps/dependencies and requires a trusted,
conversation-bound workspace Scope. Simple CodingHarness plans, ordinary Agent
Runs, chat and retrieval do not create a multi-step Workflow. P19.2 does not
claim to classify complex non-coding work; P19.1 documented that the existing
rules do not support a reliable universal four-way classifier.

## Lifecycle and evidence

1. The authenticated server resolves `Scope` from its identity and stored
   conversation/workspace records. It never accepts Scope or ownership values
   from model output.
2. `agent_runs.create` writes the Run first. On the Harness `coding_plan`
   event, `task_workflows.create_linked` creates the plan, initial checkpoint,
   and Run link in the same main-DB transaction, then activates the Workflow.
3. CodingHarness step events update P18 Todo states. Its pre-operation and
   pre-test events record prepared and dispatched attempts before a mixed
   workspace operation batch or each real verification command. The batch is
   conservatively recorded as effect `other`; each verification is `command`.
   Result receipts and real verification results add hashed evidence. The
   Harness's early `verify=completed` plan event is ignored; only successful
   real verification completes the Todo step.
4. P18 checkpoint writes and their Agent Run event projections share the Run
   transaction. The existing task card renders the Todo state and progress from
   the Run snapshot/SSE stream.

Workflow state is observational and never grants tool permission. The Coding
Harness workspace guard remains authoritative, and P18 revalidates the trusted
Scope before each projected dispatch. If the scope becomes inaccessible, the
operation is blocked; Run stop actions remain available and the card reports
that Workflow synchronization is unavailable.

## Stop, retry, and recovery rules

- Pause/cancel is rechecked after model generation and before applying returned
  operations, so a late model response cannot begin a new workspace effect.
- A dispatched attempt whose result cannot be confirmed becomes `unknown`,
  puts P18 in `needs_review`, and blocks automatic resume. Prepared-only intents
  are settled as `not_dispatched`.
- Safe paused/failed work can resume through the existing Agent Run resume path.
  The child Run is linked to the same Workflow transactionally; only paused or
  failed Todo steps reopen. Completed steps remain complete.
- A late result cannot complete a stopped Workflow. Duplicate Harness receipt
  delivery is deduplicated by a per-run event id.
- Completed or cancelled Workflows are terminal. A cancelled/terminal linked
  Coding Run must be started as a new user task rather than silently replayed.

## P19.2 boundary

This is a persistence/event projection over the existing CodingHarness
execution. It does not schedule P18 Todo steps independently and does not
provide a general-purpose P18 HTTP API or a manual reconciliation UI. P18's
existing evidence-based reconciliation remains the operator boundary for
`needs_review`; the existing Run card clearly displays that state and does not
offer automatic resume through it. Future P19 work can pass the same trusted
Scope and checkpoint/attempt projection contract to a Planner-backed entry
path that still executes through Agent Run.
