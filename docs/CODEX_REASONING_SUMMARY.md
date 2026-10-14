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
- Agent Center details display “思考摘要” collapsed by default. Live polling preserves the user's expansion state; switching tasks collapses the panel. Summary text is escaped and displayed as plain text. Observed sessions do not display owned-task summaries.
- Models may emit no summary. The page then displays “此任务暂无思考摘要”; it does not substitute raw reasoning or generated placeholder text.

Approval request parsing, eligibility, expiry, identity binding and response handling are unchanged. Ordinary Chat streaming and the installed production client are unchanged by this feature branch.

## Validation

Run from the repository root with `brain` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'brain'
python -m unittest tests.test_codex_reasoning_summary tests.test_codex_adapter tests.test_codex_adapter_http -v
python -m unittest tests.test_codex_adapter_native -v
node client/tools/test-agent-center-state.js
node client/tools/test-agent-center-notifications.js
```

Run the Electron regression from `client`, setting `VIGIL_TEST_OUTPUT` to a temporary directory to keep tracked evidence unchanged:

```powershell
$env:VIGIL_TEST_OUTPUT = Join-Path $env:TEMP 'vigil-reasoning-summary-results'
$testProcess = Start-Process -FilePath node_modules/electron/dist/electron.exe -ArgumentList 'tools/test-agent-center-electron.js' -Wait -PassThru
Get-Content (Join-Path $env:VIGIL_TEST_OUTPUT 'electron-results.json')
```

Validated on Windows with Python 3.13 and Electron 28.3.3:

- 43 backend summary/adapter/HTTP tests passed.
- 4 native Codex App Server tests passed, including a summary stream from an authenticated loopback model fixture through the actual stdio transport. No hosted-model calls.
- 15 frontend state/notification tests passed.
- 21 Electron regression checks passed, including collapsed display, incremental refresh, preserved expansion, HTML escaping and full-chain exclusion.

Native tests skip when Codex CLI is absent. They use an isolated Codex home and a local model fixture, not the user's production sessions.
