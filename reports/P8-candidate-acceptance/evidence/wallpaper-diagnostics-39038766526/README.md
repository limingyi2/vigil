# P8.2 Wallpaper diagnostics: run 39038766526

- Tested runtime source: `193f297234a36a91ef61a4dedd63d4564df41c87`.
- Diagnostic-only checkout: `cac6f0b20ac76ee65cb2e550ae37686a88a56a12`.
- GitHub Actions: <https://github.com/limingyi2/vigil/actions/runs/39038766526>.
- Immutable raw artifact: <https://github.com/limingyi2/vigil/actions/runs/39038766526/artifacts/12103092295>.
- Runner: GitHub `windows-latest`, Electron 28.3.3 / Chromium 120.0.6099.291, ANGLE SwiftShader software WebGL.
- Product runtime comparison: `client/src`, `client/electron`, and `client/vendor` matched the tested source SHA.

## Results

| Trace/result | Outcome |
| --- | --- |
| Full integrated run with tracing | Wallpaper first-frame check passed; whole test later timed out at unrelated `#retryHistory`. |
| Full integrated run without tracing | Timed out at Wallpaper first frame. |
| Wallpaper-only chat harness | Passed first frame. |
| Resize-only then Wallpaper | Passed first frame. |
| Wallpaper-layout-only then Wallpaper | Passed first frame. |
| Standalone Wallpaper Electron test | Passed first frame; two unrelated paint checks were bypassed to reach this assertion. |

The runner's WebGL implementation was software SwiftShader, despite Electron hardware acceleration being enabled in the standalone test environment. Do not interpret this as a hardware GPU run.

## Local raw files

- `raw/p8-wallpaper-full.jsonl`, `.log`, `-result.json`
- `raw/p8-wallpaper-isolated.jsonl`, `.log`, `-result.json`
- `raw/p8-wallpaper-resize-only.jsonl`, `.log`, `-result.json`
- `raw/p8-wallpaper-layout-only.jsonl`, `.log`, `-result.json`
- `raw/p8-wallpaper-full-control.log`, `-result.json`
- `raw/p8-wallpaper-standalone.jsonl`, `.log`, `-result.json`
- `raw/run-metadata.json`

The artifact also contains the screenshot bundle and exact runner outputs. These local files are verbatim copies of the Actions artifact's result/log/trace files.
