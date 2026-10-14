# Codex reasoning summary

Base: master v1.0.0 (`ac1938e86eeed50313e6c0cff6ee408d0bc3c63e`).

## Behavior

- Owned Codex turns request `summary: "auto"` while keeping existing sandbox and approval settings.
- Retain `item/reasoning/summaryPartAdded` and `item/reasoning/summaryTextDelta`, including item identity and summary index. Repeated identical text deltas are legitimate and are appended.
- Store the latest turn's published summary as `task.reasoning_summary`. The snapshot and task APIs expose this field. Its `items[].parts[]` hold `summary_index` and `text`; `turn_id` and `generation` identify the source turn.
- Persist the field atomically in the existing `owned.json` registry on summary updates. The rotating event journal retains normalized summary deltas and boundaries. Starting a new turn clears the previous snapshot summary; restarting the adapter preserves the saved summary while marking the task as history/unknown.
- Summary text is limited to 65,536 characters per task turn, with at most 16 reasoning items and 32 part indices per item. Truncated text is labelled in the UI.
- Ignore full `item/reasoning/textDelta` events and never copy `reasoning.content`, raw event payloads, or full reasoning text into summary storage or the page.
- Only accept summary updates for the current connected owned turn and generation. Late, foreign, malformed, and terminal-turn updates cannot alter the summary.
- Agent Center displays summaries as collapsed entries in Agent Timeline, alongside connection/task status transitions and tool start/completion events. Live polling preserves expansion and keyboard focus; switching tasks collapses the entries. Summary text is escaped and displayed as plain text. Observed sessions do not display owned-task summaries.
- Models may emit no summary. The Timeline then displays “暂无思考摘要”; it does not substitute raw reasoning or generated placeholder text.

## Agent Timeline

- The existing stdio protocol, RPC requests, task creation and approval handling remain unchanged. The Brain event API adds optional normalized metadata: `item_key`, `duration_ms`, `previous_state`, and `state_transition`. Disconnects also record an internal `task/status/changed` event to represent the existing transition to unknown; it is not an outbound Codex protocol method.
- `agent-timeline.js` projects existing events into connection, state, tool and summary entries. Per-connection sequence order is preserved; duplicate delivery sequences are ignored. Identical summary chunks with different sequence numbers remain legitimate content.
- Tool start and completion are correlated by connection, generation, task, turn and item identity. Show type, result, exit code and duration only. Commands, tool arguments, file diffs and raw tool output are not displayed in Timeline.
- Native `durationMs` is preferred; otherwise duration is derived only from a matching start. Missing start events are explicitly labelled. Missing completion status is not interpreted as success.
- Repeated summary deltas and part boundaries are grouped into one collapsible entry per reasoning item. The latest saved `reasoning_summary` snapshot supplies full text when the event window is incomplete, without duplicating a matching entry. A restored snapshot has no invented timestamp and is labelled “摘要快照”.
- Task transitions use the recorded before/after state. Older event metadata without the optional fields remains readable. Historical replay is labelled, old turn completion does not become a current state transition, and disconnected/stale tasks retain unknown status.

Approval request parsing, eligibility, expiry, identity binding and response handling are unchanged. Ordinary Chat streaming and the installed production client are unchanged by this feature branch.

## Validation

Run from the repository root with `brain` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'brain'
python -m unittest tests.test_codex_timeline tests.test_codex_reasoning_summary tests.test_codex_adapter tests.test_codex_adapter_http -v
python -m unittest tests.test_codex_adapter_native -v
node client/tools/test-agent-center-state.js
node client/tools/test-agent-center-notifications.js
node client/tools/test-agent-timeline.js
```

Run the Electron regression from `client`, setting `VIGIL_TEST_OUTPUT` to a temporary directory to keep tracked evidence unchanged:

```powershell
$env:VIGIL_TEST_OUTPUT = Join-Path $env:TEMP 'vigil-reasoning-summary-results'
$testProcess = Start-Process -FilePath node_modules/electron/dist/electron.exe -ArgumentList 'tools/test-agent-center-electron.js' -Wait -PassThru
Get-Content (Join-Path $env:VIGIL_TEST_OUTPUT 'electron-results.json')
```

Validated on Windows with Python 3.13 and Electron 28.3.3:

- 47 backend summary/timeline/adapter/HTTP tests passed.
- 4 native Codex App Server tests passed, including a summary stream from an authenticated loopback model fixture through the actual stdio transport. No hosted-model calls.
- 27 frontend Timeline/state/notification tests passed.
- 21 Electron regression checks passed, including summary integration, status transitions, tool start/completion, collapsed display, incremental refresh, preserved expansion and focus, HTML escaping and full-chain exclusion.

Native tests skip when Codex CLI is absent. They use an isolated Codex home and a local model fixture, not the user's production sessions.

Electron regression windows are hidden by default to keep desktop interactions from disturbing fixtures. Set `VIGIL_TEST_SHOW_WINDOW=1` when manually inspecting the test window. Layout checks explicitly reopen Codex details if an external notification returned the page to the overview.
