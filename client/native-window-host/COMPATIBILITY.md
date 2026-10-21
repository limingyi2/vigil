# P15 Native Window Host Compatibility Record

Test date: 2026-10-21 (local Windows session, 144 DPI)

## Results

| Target | Result | Evidence |
| --- | --- | --- |
| 剪映专业版 11.4.1.14443 | HWND hosted in standalone Electron PoC | Process tree discovery selected child PID 18388 from launch PID 27540. HWND 2296882 reported the PoC container HWND 1313896 as its parent at 144 DPI. The first attach was verified; detach restoration was not separately verified for this run. |
| PyCharm (installed desktop build) | Hosted and window recreation detected in standalone Electron PoC | PID 23160 attached HWND 724106 to container HWND 855188; when the app recreated its window, helper reported `recreated` and attached HWND 789912 to the same container. |
| Remote Desktop Connection (`mstsc.exe`) | Hosted, focus command issued, detached, and restored in Vigil | Vigil reported HWND 1248090 parented to container HWND 1117580 at 144 DPI. `FOCUS` was issued. `DETACH` returned `restored: true`; follow-up Win32 inspection found the HWND valid, top-level (`GetParent == 0`), and no longer carrying `WS_CHILD`. Navigation to 插件市场 removed the video host slot. |
| Packaged Vigil P15 Preview + `mstsc.exe` | Hosted and restored | Portable build started the EXE from `resources/native-window-host/native-window-host.exe`; HWND 1181358 reported container parent 1445270 at 144 DPI and `DETACH` returned `restored: true`. |

The Windows host created a `WS_CHILD | WS_CLIPCHILDREN | WS_CLIPSIBLINGS` container. The Electron POC and Vigil integration both retained `sandbox: true`, `contextIsolation: true`, `nodeIntegration: false`, and default GPU settings.

## Repeatable commands

From `client` in PowerShell:

```powershell
.\native-window-host\build.cmd
$env:P15_TEST_ROOT = Join-Path $env:LOCALAPPDATA 'Vigil\P15-WindowHost-PoC\manual'
& .\node_modules\.bin\electron.cmd native-window-host\poc-electron\main.js
```

Choose an EXE using the PoC picker. The PoC writes newline-delimited JSON events to `native-window-host-events.jsonl` under `P15_TEST_ROOT`. For the Vigil integration, run `npm start`, add one or more EXEs under **设置 → 视频软件**, then select and launch a saved profile in **视频工作台**. Vigil writes events to `native-window-host-events.jsonl` under Electron's user data directory.

## Preview artifact

- Build: Windows x64 portable preview, Electron 28.3.3, using `electron-builder.p15.yml`.
- Artifact: `Vigil P15 Preview 1.1.8-p9.3.20261021.1.exe` (70,092,597 bytes; updated workbench layout with the hosted window in the upper area and a reserved lower AI workspace).
- SHA-256: `431EEFD81EBD31E5C2769643272D168EE5EBBD8E8178DBCA1D0E5AA7B090355A`.
- The preview packages `resources/native-window-host/native-window-host.exe`. It does not package the Vigil Brain sidecar because this checkout has no Brain release build input; this is a P15 client preview, not a complete Vigil release.
- Packaging issue found after delivery: the portable NSIS launcher displayed `Error launching installer` on this test machine. Running `win-unpacked/Vigil P15 Preview.exe` from that same build started and remained responsive. A ZIP of the unpacked files was prepared to bypass the failing launcher: `Vigil-P15-Preview-ZIP-20261021.zip` (111,798,198 bytes), SHA-256 `C68854EAA6771F40287EA19051749BD81267BF44876BFF1422D34614AC954597`. The archive listing was checked for the app executable, native helper, and run instructions. Extract the ZIP as-is and launch `Vigil P15 Preview.exe`.

## Limits observed

- These results cover only the listed applications and this Windows machine. They do not establish support for arbitrary Windows software.
- All recorded HWND attachments were at 144 DPI. Moving a hosted app between monitors with different DPI values remains unverified.
- Popup discovery and in-host placement are implemented for visible secondary top-level windows in the target process tree, but application-specific modal dialogs were not exercised in this run.
- The compatibility runs checked HWND parentage, the focus command, restoration, and PyCharm window recreation. They did not validate full editing workflows inside each target.
- MSTSC closed its main window after the graceful `WM_CLOSE` request but kept a background process alive during the isolated test. The host does not force-terminate it. Applications that stay resident may need to be exited using their own UI.
- Cross-integrity and incompatible DPI-awareness combinations can cause Windows to reject `SetParent`; in that case the helper restores state and reports standalone-window fallback. No elevated target app was included in this run.
- Window discovery times out after 30 seconds and falls back to the app's independent window. Long-starting applications may therefore need a future configurable timeout.
- The local portable preview omits Vigil Brain resources because this checkout has no `dist/brain-release/vigil-brain` build input. The P15 workbench and native host are included and were verified from the packaged app.

## Second-round repair and acceptance (2026-10-21, Windows x64)

This round was developed on `feat/p15-video-window` from remote HEAD `1d7e447d4ec66cdfe3a164d1969a3b1d4c7841f4`. No `dev` or `master` changes were merged. The upper software host and lower reserved workspace layout were retained.

### Findings and fixes

- **Lifecycle and profile switching:** the old Electron `currentExe` string was not evidence that a process or HWND was alive. Electron now reports the helper's current state, requests a fresh status on page load, and permits another profile when the prior process exits or its main window is closed while the process remains resident. The helper tracks the launched process family with a non-terminating Job Object (or observed descendant handles if assignment is unavailable), clears dead handles/HWNDs and discovery state, and never uses `TerminateProcess`. `CLOSE_APP` sends `WM_CLOSE` only. Launcher exit is not treated as target exit while job descendants remain.
- **Window rebuild and identity:** the helper associates discovered windows with the launched process family; it reports `window-lost` while searching and does not reuse a destroyed HWND. It reports `recreated` only after finding and attaching a replacement. An unrelated process window is not eligible for attachment.
- **Detach and dialogs:** original parent/owner, styles, rectangle, placement and visibility are saved and restored. Secondary top-level dialogs remain standalone instead of being resized into the host. `SetParent` is considered successful only when the observed parent HWND equals the container; return value and `GetLastError` are retained as diagnostics.
- **Portable launcher:** electron-builder 24.13.3 generated a fixed portable extraction directory for a given build, which can collide when that build is already running. `portable.unpackDirName: true` leaves extraction to the launch-specific NSIS plugin directory. The historical “Error launching installer” dialog was **not conclusively reproduced** on this machine. The corrected portable passed clean-directory launch and a repeated-launch check; this mitigates the identified temp-directory collision without claiming the original dialog was reproduced.
- **Security/runtime settings:** sandbox, context isolation and GPU acceleration remain enabled. No process is forcibly terminated.

### Windows test matrix

| Test | Result | Evidence / limitation |
| --- | --- | --- |
| Standalone native helper lifecycle: CharMap → Notepad → CharMap | PASS | `test-results/2026-10-21-round2/native-window-host-events.jsonl`; actual HWND/PID, 144 DPI, detach and relaunch events. CharMap detached with a valid, visible top-level HWND and matching original style. |
| Packaged Vigil + helper path + profile switching: CharMap → Notepad → CharMap | PASS | `test-results/2026-10-21-round2/vigil-portable-events.jsonl`; helper launched from the packaged resources path. |
| Latest SetParent/detach/page visibility/minimize/restore and A→B→A run | PASS | `test-results/2026-10-21-round2/vigil-portable-round2-final.jsonl`; SetParent parent HWND matched container, CharMap detach restored original parent/style and remained visible, same HWND reattached, page switch and minimize/restore retained attachment, then Notepad and CharMap launched. |
| Portable launch from clean copied directory | PASS | Ran the final portable executable from `test-results/2026-10-21-round2/portable-final-r4-clean`; package helper resolved from the extracted `resources/native-window-host/native-window-host.exe`. Repeated launch of the same build while active exited successfully without an NSIS dialog. The original historical dialog itself remains unreproduced. |
| Process family after launcher exit | PASS for Notepad smoke run | The helper attached Notepad's actual window PID even when the launch PID differed, and tracked the family through normal close. |
| User initiated focus | WAITING_USER_TEST | Background CDP automation issued `FOCUS`; Windows did not grant foreground activation (`foregroundRequested:false`, `focusSet:false`). Electron now focuses its window in response to the launch action, but a real user click/focus round trip was not automated. |
| Jianying portable EXE | WAITING_USER_TEST | The user's specific portable EXE was not available in the test environment. Installed Jianying Pro and other applications from the first-round record do not substitute for this requested portable-package test. |
| Jianying preview playback, editing, import dialog, menus/modals, project save/reopen | WAITING_USER_TEST | Not run without the user's actual portable EXE. |
| Mixed-DPI monitor movement, elevated app, GPU-heavy preview and application-specific modal dialogs | NOT TESTED | All round-two HWND checks ran at 144 DPI. |

The lifecycle harness was run with the isolated test user data and output location through `P15_TEST_ROOT`; the packaged acceptance used `VIGIL_USER_DATA` under `test-results/2026-10-21-round2`, `VIGIL_SIDECAR_DISABLE=1`, `VIGIL_TEST_NO_UPDATES=1`, and local CDP port 9358. The final acceptance driver is `test-results/2026-10-21-round2/portable-final-acceptance.cjs`; the PoC lifecycle driver is in `poc-electron/main.js`. The manual build commands were `.\native-window-host\build.cmd` and `npx electron-builder --config electron-builder.p15.yml --win portable` from `client`. For CDP acceptance, start a clean copied portable with those three environment variables and `--remote-debugging-port=9358`, then run `node .\native-window-host\test-results\2026-10-21-round2\portable-final-acceptance.cjs`. The event files record HWND/PID/state transitions and exact test bounds. This is Windows-native evidence and is independent of CI results.

### Current trial artifact

- Windows x64 portable P15 client preview: `%LOCALAPPDATA%\Vigil\P15-WindowHost-Artifact\Vigil P15 Preview 1.1.8-p9.3.20261021.1.exe`.
- SHA-256: `3FD4FFB4A7804E4166D07A7F35D5EAC3127CAE8446B390A257628E3B14E6E798` (70,104,873 bytes).
- Packaged helper SHA-256: `7B546127985766E1152207F29361906AE63E92304F3445096AC87AB9083E8668`.
- Run by launching the EXE. This is a **client preview**, not a complete Vigil installer: the build input did not include `dist/brain-release/vigil-brain`. To use software hosting, open **视频工作台**, add an EXE under **设置 → 视频软件**, select the saved configuration and start it. Validation used isolated test user data.
- This artifact was copied to and launched from a clean directory during validation. The original NSIS error dialog remains inconclusive as described above.

### Remaining compatibility limits

The successful CharMap and Notepad runs validate HWND lifecycle and profile switching, not broad software compatibility. Jianying's real editing operations, preview rendering, file picker, dialogs and save/reopen still require `WAITING_USER_TEST` with the user's portable EXE. Multi-monitor DPI changes, elevated targets and application-specific modal/GPU behavior also remain unverified. A target that rejects reparenting falls back to its normal standalone window.
