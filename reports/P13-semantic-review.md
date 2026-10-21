# P13 implementation semantic review

Date: 2026-10-21  
Base: `e2c47407c6e5e1125ee3b65896138b6759acf89b`  
Scope: changed P13 harness, Provider replay, repair/review, event recovery, evaluation, and composer throughput paths.

## Coverage

| Area | Reviewed source | Evidence |
|---|---|---|
| Repair verification and local command receipts | `brain/project_repair.py`, `client/electron/localtool.js` | `tests/test_p10_harness.py`, `client/tools/test-local-exec-exit.js`, `tests/test_p13_project_review.py` |
| Shared context budget and overflow recovery | `brain/agent_context.py`, `brain/conversation_context.py`, `brain/agent_runs.py`, `brain/brain.py` | `tests/test_agent_context.py`, `tests/test_p13_event_recovery.py`, `tests/test_cloud_context_tiers.py` |
| Provider-specific assistant replay | `brain/model_providers/base.py`, `brain/model_providers/cloud.py`, `brain/model_providers/execution.py` | `tests/test_agent_context.py`, `tests/test_p12_protocol.py` |
| Durable run events and recovery | `brain/agent_runs.py`, `brain/server.py`, `client/src/js/pages.js` | `tests/test_p13_event_recovery.py`, `tests/test_p12_state.py`, `tests/test_p12_protocol.py`; isolated Electron P12 E2E |
| Project review and evaluation | `brain/project_review.py`, `brain/tools.py`, `vigil-eval/p13_metrics.py` | `tests/test_p13_project_review.py`, `vigil-eval/tests/test_p13_metrics.py`; deterministic fixture run |
| Output throughput display | `brain/server.py`, `brain/agent_runs.py`, `client/src/js/pages.js` | isolated Electron screenshot and `reports/P13-token-rate-electron.png` |

## Findings

1. **Resolved before PR — streamed request timer was not initialized in `stream_chat`.** Full-suite testing exposed that an early provider exception could turn into a generic connection error before the context-overflow retry. The timer is now initialized with the other request counters at `brain/brain.py:1505`. The provider overflow retry, ordinary chat embedding fallback, and P8.4 acceptance cases pass on rerun. No open finding remains for this issue.
2. **No current confirmed defect found in the reviewed P13 paths.** Findings generated from static markers remain candidates until backed by current-source evidence and a real failing Agent `local_exec` receipt; none were promoted solely from a model assertion.
3. **Provider semantic review using a live second model: NOT RUN.** No isolated model-run configuration or before/after records were supplied. The report does not load production credentials or claim model accuracy results.

## Verification summary

- `python -m pytest -q`: full suite passed after the timer fix; expected skips remain skipped.
- `python vigil-eval/runner.py --stub-all`: 48/48 deterministic fixture runs passed. This checks the isolated evaluation harness and does not measure model quality.
- Two Provider protocol contracts, Cloud and Ollama: passed in `tests/test_p12_protocol.py`.
- The independent evaluation status and metric definitions are in `reports/P13-evaluation-status.md`.
