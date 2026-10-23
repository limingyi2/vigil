# Repository audit fixes — 2026-10-24

Base: cc3e143ec132cd6c568b6611fdac2aead5320005 (dev).
Branch: codex/audit-cleanup. Changes are local and have not been published.

## Implemented

- Model UI tests now inspect the running client; removed the obsolete vigil-client source copy. Updated assertions for the current POST model-list API (credentials stay out of URLs).
- CI collection and Windows candidate workflow alignment were prepared but are held out of this push because GitHub rejected workflow-file updates with the current credential (missing `workflow` scope).
- Settings POST dispatch serializes read/modify/write cycles for models, personas, voice, run-mode and retrieval. Model apply no longer clears the shared config before updating it.
- Agent tasks and individual Ollama requests hold model leases, including failure cleanup. Idle unloading checks leases while holding the lifecycle lock.
- Hybrid mode requires cloud credentials before probing Ollama or applying changes.
- Hub inventory reuses build_identity; both spec entry points use the canonical release spec, which includes the optional websocket module.
- KB tools use the engine as their only retrieval/fallback entry, retaining incremental KB sync.
- Windows acceptance follows dev and reads installer names from the manifest; current and legacy executable names are supported in runtime/rollback checks. Fresh-install checks use the unified database path. Fixed duplicated npm command.
- Corrected README development branch/recovery status and contradictory historical gate installation instructions. Removed tracked test caches and host marker; ignored future markers.

## Validation

- Full Python run reached completion: only three Coding Harness tests failed because subprocess `python` resolved to the Windows Store alias. All three passed when rerunning the complete Coding Harness module with the bundled Python directory first on PATH.
- Focused HTTP/retrieval/regression group: 52 passed.
- Additional lifecycle/hygiene/bridge group: 45 passed.
- Model UI, run-mode, KB, Hub and Agent Run regression group: 140 passed.
- Final Node regression group: 25 passed, including Kestrel rollback checkpoint.
- Python syntax, workflow YAML parsing and git diff --check passed.

## Remaining architecture work / verification limits

The default-off legacy Planner remains compatible. This patch does not remove it or replace the existing execution engines. Large server.py/pages.js modules still need a separate responsibility-by-responsibility extraction with renderer and execution integration coverage; their size alone is not a safe basis for moving code mechanically.

Real installer upgrade/rollback and fresh-install lifecycle must run in the disposable Windows CI environment. This session did not install software, build an installer, or publish artifacts. The workflow/script fixes are not a claim that real installation acceptance has passed.
