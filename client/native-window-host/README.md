# P15 Windows Native Window Host

This module hosts a user-selected Windows EXE by discovering its top-level HWND, changing it to a child window, and placing it inside a native child container owned by Electron. A separate C++ helper owns the Win32 calls; Electron's renderer communicates through a narrow preload API.

## Build and run

From `client` in PowerShell:

```powershell
.\native-window-host\build.cmd
npm start
```

Open **设置 → 视频软件** to save one or more named configurations for local EXEs. Then select a saved app in **视频工作台** and choose **启动并托管**. The workbench keeps the native window in its main area and offers **解除托管** to restore its original styles and placement. The page intentionally keeps app arguments and configuration management in Settings.

The standalone Electron PoC is useful for checking a target before using the Vigil page:

```powershell
$env:P15_TEST_ROOT = Join-Path $env:LOCALAPPDATA 'Vigil\P15-WindowHost-PoC\manual'
& .\node_modules\.bin\electron.cmd native-window-host\poc-electron\main.js
```

The PoC has its own Electron `userData` directory and appends helper events to `native-window-host-events.jsonl` under `P15_TEST_ROOT`. Use the native **选择 EXE** button to choose any local `.exe`.

## Protocol overview

The C++ helper receives the Electron HWND, creates a clipped child container, starts the selected executable with `CreateProcessW`, and polls its process descendants for visible top-level windows. It saves the original window styles, extended styles, owner/parent, bounds, and `WINDOWPLACEMENT` before `SetParent` and `SetWindowPos`. Secondary top-level windows in the same process tree are placed into the same host area. If embedding fails, saved state is restored and the host reports independent-window fallback.

Commands use newline-delimited tab fields on stdin: `START`, `BOUNDS`, `SHOW`, `HIDE`, `FOCUS`, `ATTACH`, `DETACH`, `CLOSE_APP`, and `QUIT`. Events are newline-delimited UTF-8 JSON on stdout. The helper is per-monitor-v2 DPI aware. Container visibility is controlled by the video page, which hides it when the page is disposed.

See [COMPATIBILITY.md](COMPATIBILITY.md) for the tested software and current limitations.
