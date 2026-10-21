# P14 Wallpaper Market report

Date: 2026-10-21 (Asia/Shanghai)  
Branch: `codex/p14-wallpaper-market`  
Base: `e2c47407c6e5e1125ee3b65896138b6759acf89b`

## Summary

This change aligns Vigil's checked-in market manifest with the live Vigil plugin repository and adds a timestamped, validated offline listing cache. It does **not** complete the requested Wallpaper Engine refactor. In particular, the published Wallpaper 3.0.1 package and the branch's 3.0.0 source both render Scene wallpapers as preview images; neither contains WebWallGL. Scene live rendering, user-imported media, and full lifecycle/performance acceptance remain blocked and are not represented as passing.

## Root cause and changed files

- `market.json` was stale: it advertised Wallpaper 3.0.0 and an older artifact. The live repository returned 3.0.1 at commit `c1c9b63703c6d733688640784a0be55421c67909` with SHA-256 `4e551ae17783870872be7c2635d1a396fe5269fa032b514aff7e536940b22616`.
- `brain/plugins/plugin_market.py` already contained HTTPS GitHub Raw/mirror fetching, request timeouts, strict source allowlisting, SHA-256 validation, manifest/file hash checks, permission checks and transactional installation. It had no verified offline cache. It now stores a validated listing with a UTC fetch timestamp, validates it again before display, and labels it `cached_stale` when the online read fails. The cache path is under the runtime config directory.
- `brain/tests/test_plugin_market_state.py` now covers timestamped stale-cache use and rejection of malformed cache data.
- Navigation review: `client/src/js/pages.js` has the market as a built-in page and no hard-coded Wallpaper page. Wallpaper registers its page from its dynamically loaded plugin frontend. `client/src/js/app.js` builds the nav from `PAGES`, and plugin-enabled state is returned by `plugin_host.frontend_assets()`. The existing polling reloads the renderer when the frontend fingerprint changes. No navigation code was changed.

## DSH comparison

Reference reviewed: `elysia395/dsh-wallpaper-engine` README, `docs/HOW-IT-WORKS.md`, `docs/CODE-STRUCTURE.md`, and `docs/TROUBLESHOOTING.md`; renderer: `oneincase/webwallgl` README and package metadata. DSH is MIT licensed; WebWallGL package metadata declares MIT. No upstream code was copied into Vigil, so no upstream license file was added.

| Capability | DSH / WebWallGL reference | Vigil at P14 checkout | P14 result |
|---|---|---|---|
| Scene real-time render | WebWallGL, live scene package parser, shaders, particles, scripts, properties and interaction | Scene branch displays `preview` image | BLOCKED; no renderer integrated |
| Web wallpaper | WebWallGL mount with Wallpaper Engine API compatibility and sandbox | Sandboxed local iframe, relative local media URL proxy; no WE shim | Partial; existing path retained, no shim added |
| Video wallpaper | Direct video layer with controls and fallbacks | Local video loop/autoplay/mute | Partial; no visible pause/volume controls |
| Custom image/video | Import and manage user media | No import endpoint or custom-media library found | BLOCKED |
| Workshop detection | Steam library discovery and workshop inventory | Windows registry, `libraryfolders.vdf`, common paths | Implemented in existing plugin; not tested on a real Steam installation |
| Library, previews, search | Inventory, filtering, previews and switching | Inventory cards and previews; no search UI | Partial |
| Scene audio/interaction | Packaged audio, scripts and mouse interaction | Not present | BLOCKED |
| Glass/appearance | Multiple glass and visual controls | Opacity, scrim, blur | Partial |
| Rotation / playlist | Rotation and playback queue | Not found | BLOCKED |
| First frame / fallback | Live frame readiness and explicit fallback chain | Preview-first behavior; no live frame state | BLOCKED |
| Enable, disable, uninstall, cleanup | Owned disposable contributions and resources | Feature gating and frontend injection exist; removal relies on reload/restart; no explicit disposer for wallpaper DOM/listeners/media | Partial; unload cleanup not validated |

Advanced appearance presets, mascot, music linking, 3D parallax and theme presets were not ported. They are out of the completed implementation and have no acceptance evidence.

## Verification evidence

### Plugin market

- **Real remote read: PASS.** `brain/plugins/plugin_market.py::_read_remote_market()` returned `ready`, version `1.1`, updated `2026-10-21`, with Wallpaper `3.0.1` and the SHA-256 above.
- **Real artifact install transaction: PASS.** `_install_remote_directory()` fetched the live 3.0.1 ZIP, validated archive and manifest/file hashes, and committed it to a temporary isolated install directory. It returned `phase=committed`, `rollback=not_needed`, `static_validated=true`, and `restart_needed=true`. No user plugin directory was modified.
- **Cache behavior: PASS in unit coverage.** A valid timestamped cache is shown as `cached_stale`; an invalid cached listing is rejected. Online freshness is not inferred from cached data.
- **Install failure rollback, update/uninstall against a running Brain: BLOCKED.** The temporary artifact install succeeded; no production app/Brain session was attached to this worktree for fault injection or lifecycle verification.

### Lifecycle and wallpaper

- The existing plugin test suite covers structure and transaction behavior. No real Electron window or configured Brain was available in this worktree. The Electron wallpaper and market tests were not run because `client/node_modules` / Electron runtime are absent.
- No Wallpaper Engine workshop was available to validate actual workshop content, video playback, web rendering, DPI/resize transitions, minimization, CPU/GPU/memory use, or performance.
- **Scene live render: BLOCKED.** Inspected the live Vigil 3.0.1 artifact: its frontend contains no `WebWallGL` or `scene.pkg` loading path and still selects a static preview for Scene wallpapers. Therefore no live animation evidence exists.
- **Windows candidate package: NOT BUILT.** Client dependencies and `dist/brain-release/vigil-brain` are absent from this checkout. Candidate `.exe` path, size, and SHA-256: N/A.

## Test results

Executed from the P14 worktree's `brain` directory:

```text
python -m pytest tests/test_plugin_market_state.py tests/test_plugin_manifest.py tests/test_plugin_transactions.py tests/test_plugin_structure.py -q
37 passed, 3 skipped
```

The same test command from repository root failed collection because the existing tests import Brain modules by bare module name (`paths`, `plugin_ops`, etc.); rerunning from `brain` passed. No test assertions were weakened.

| Acceptance item | Status | Evidence / reason |
|---|---|---|
| Real market read | PASS | Live GitHub response read and validated |
| Remote install/hash/manifest transaction | PASS | Live 3.0.1 artifact installed in temporary isolation |
| Offline cache state and timestamp | PASS | New unit tests, included in 37 passed |
| Cache rejected when malformed | PASS | New unit test |
| Online failure diagnostics | PARTIAL | Existing structured error plus timestamped cache label; no network fault UI run |
| Failed install rollback | BLOCKED | No fault-injection/live Brain integration run |
| Install/update/uninstall state synchronization | BLOCKED | No Electron/Brain runtime present |
| Not-installed/disabled navigation | PARTIAL | Source path is dynamic; no live Electron state matrix run |
| Wallpaper image/video/web | BLOCKED | No Electron runtime or real media inventory |
| Scene live rendering | BLOCKED | Upstream package lacks WebWallGL integration; Vigil uses preview image |
| Custom import, playlist, search | BLOCKED | Not implemented |
| First-frame / resize / minimize / performance | BLOCKED | No integrated renderer or hardware run |
| Windows installer | BLOCKED | Build prerequisites absent |

## Git and delivery

Implementation commit SHA: `86f2c8f796af40d413f81c86e641bf4792d4eb4a`. The final report-only commit is the branch tip.
Windows candidate package: none built.  
This work is limited to `codex/p14-wallpaper-market`; no merge was performed.
