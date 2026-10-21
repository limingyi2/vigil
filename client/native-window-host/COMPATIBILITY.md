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
- Artifact: `Vigil P15 Preview 1.1.8-p9.3.20261021.1.exe` (70,092,537 bytes).
- SHA-256: `04CF9C2CA19619A02418CCEBD8314B41E4738FBB39C6E517EF10EE15639A14DD`.
- The preview packages `resources/native-window-host/native-window-host.exe`. It does not package the Vigil Brain sidecar because this checkout has no Brain release build input; this is a P15 client preview, not a complete Vigil release.

## Limits observed

- These results cover only the listed applications and this Windows machine. They do not establish support for arbitrary Windows software.
- All recorded HWND attachments were at 144 DPI. Moving a hosted app between monitors with different DPI values remains unverified.
- Popup discovery and in-host placement are implemented for visible secondary top-level windows in the target process tree, but application-specific modal dialogs were not exercised in this run.
- The compatibility runs checked HWND parentage, the focus command, restoration, and PyCharm window recreation. They did not validate full editing workflows inside each target.
- MSTSC closed its main window after the graceful `WM_CLOSE` request but kept a background process alive during the isolated test. The host does not force-terminate it. Applications that stay resident may need to be exited using their own UI.
- Cross-integrity and incompatible DPI-awareness combinations can cause Windows to reject `SetParent`; in that case the helper restores state and reports standalone-window fallback. No elevated target app was included in this run.
- Window discovery times out after 30 seconds and falls back to the app's independent window. Long-starting applications may therefore need a future configurable timeout.
- The local portable preview omits Vigil Brain resources because this checkout has no `dist/brain-release/vigil-brain` build input. The P15 workbench and native host are included and were verified from the packaged app.
