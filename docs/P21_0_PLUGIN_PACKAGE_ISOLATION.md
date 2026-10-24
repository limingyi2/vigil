# P21.0 — Plugin package isolation

Status: implementation in progress; this phase is limited to the package boundary and backward-compatible startup migration.

## Problem confirmed at the merged P20.2 baseline

- \`deploy/brain/vigil-brain.spec\` enumerated tracked files under \`brain/plugins\` and packaged them into the Brain sidecar.
- \`brain/server_launch.py\` copied the bundled \`plugins/\` tree into the application home on first launch.
- Consequently, excluding plugin files from Electron's \`app.asar\` alone was insufficient: the Brain payload could still ship built-in extension code and seed it onto a clean installation.
- The Electron package source list currently contains only the client application directories, but it lacked a general package-time assertion against extension payloads.

## P21.0 boundary

The release package ships core capability adapters under \`core_features/\`; optional extensions are loaded from the user-managed \`data/installed_plugins/\` directory. The release build must reject a Brain payload containing either a root \`plugins/\` tree or \`_internal/plugins/\`, and Electron packaging must reject extension package directories under \`src/\`, \`electron/\`, or \`assets/\`.

Core runtime adapters shipped with Brain:

- \`plugin_market\`: plugin discovery and lifecycle management infrastructure.
- \`remote\`: P20.2 authorized Agent/device execution integration.
- \`skill\`: Skill Host management and tool integration; installed skills remain user data.
- \`calc\`: a read-only arithmetic tool implemented in Brain's core tool registry, outside Plugin Host and marketplace extension packaging.

The following are optional user features. Their source under \`brain/plugins\` is development/legacy source only and is never copied into \`core_features\` or the Brain sidecar:

- \`kb\`: document ingestion, search, automatic per-turn context injection, and the Knowledge Base UI/API. Existing knowledge documents/configuration remain user data; clean installs enable the feature by installing its separately published plugin.
- \`gate\`: optional conversation turn gate.
- \`react\`: optional ReAct reply processing.
- \`websearch\`: optional web search tool.
- \`meme\`: meme lookup and current trends from external services.
- \`reminder\`: persistent reminders and background notifications.
- \`sticker_pack\`: WeChat-only sticker collection/context and reply hints; requires the separate bridge sticker API.
- \`branch-atlas/\`: local read-only Git branch/topology viewer and its UI route.
- Creator-generated plugins: user-authored packages installed under \`data/installed_plugins\`; never included in a client build or seeded from Creator templates.
- \`wallpaper\`: independent desktop appearance/Wallpaper Engine integration, published as \`wallpaper\` 4.1.7 in the v1 market.

Existing installs preserve legacy modules through copy-only migration. A clean install does not contain these features unless a Manifest v1 market artifact is installed. This is a visible change: Knowledge Base, turn gating, ReAct formatting, web search, meme lookup, reminders, sticker hints, and Branch Atlas are opt-in. Chat, arithmetic, Agent authorization, Plugin Manager/Host, and Skill Host infrastructure remain available. The marketplace's management UI and generic resource bridge remain core client code; plugin packages do not.

## Legacy feature inventory and independent publication plan

This inventory covers every runtime module/package currently under \`brain/plugins\` (excluding the \`_test_react.py\` test helper). “Core” means infrastructure required to operate the client/Brain or preserve the named platform contract; it does not mean a user-facing extension is renamed or reclassified to bypass package isolation.

| Source | User-visible function and dependency | P21.0 treatment | Independent release plan |
| --- | --- | --- | --- |
| \`calc.py\` | Basic arithmetic tool; no external service | Small deterministic arithmetic stays in Brain's native core tool registry; not loaded by Plugin Host or market | No plugin artifact; keep parity covered by core tool tests. |
| \`plugin_market.py\` | Market listing, install/update/uninstall lifecycle, and API routes | Core management infrastructure only; market packages still install into user data | No market artifact; keep manager/host lifecycle regression coverage. |
| \`skill.py\` | Bridge from Skill Host JS tools into Brain tool discovery; skills themselves are user-installed data | Core Skill Host integration infrastructure; no skill package is bundled | No plugin artifact; continue to publish/install each skill independently through Skill Host. |
| \`remote.py\` | P20.2 authorized local Agent/device tools, filtered by channel and authorization policy | Core Agent execution adapter; existing authorization/clearance checks remain mandatory | No plugin artifact; do not weaken P20.2 authorization to satisfy packaging. |
| \`kb.py\` | Document ingest/search, per-turn retrieval, Knowledge Base UI/API; uses core \`brain/kb\` storage/index libraries | Optional feature; adapter is legacy-only and is excluded from the package allowlist; documents/config remain user data | Publish \`kb\` as a SHA-256 hash-checked Manifest v1 package with its existing routes/tools; verify install, update, disable, uninstall-with-data-retention, and re-enable against existing KB stores. |
| \`gate.py\` | Conversation turn gate and stats route; channel/source policy is sensitive | Optional; retain source and migrate existing copies | First runtime policy artifact after comparing this module with \`wechat-bridge/brain-plugin/gate.py\`; test channel matrix, group-safe policy, and disabled state. |
| \`react.py\` | ReAct reply post-processing, trace capture/replay and route | Optional; retain source and migrate existing copies | Manifest v1 package after hook/lifecycle tests prove trace data stays in user data and reply output is unchanged; do not expose hidden reasoning in replies. |
| \`websearch.py\` | Search tool, context hook and route; calls external providers | Optional; retain source and migrate existing copies | Manifest v1 package with provider/network behavior documented; verify tool calling and no network request while disabled. |
| \`meme.py\` | Meme lookup/current trends; external Bilibili/Baidu-compatible endpoints | Optional; retain source and migrate existing copies | Manifest v1 package after endpoint/cache review; document network dependency and cache location. |
| \`reminder.py\` | Persistent alarms, tool routes, timer thread and proactive Brain callback | Optional; retain source and migrate existing copies | Manifest v1 package after start/stop/restart verifies one timer worker and retention under \`data/plugins/reminder\`. |
| \`sticker_pack.py\` | WeChat sticker collection and reply-context hints; relies on bridge-owned media/API | Optional channel extension; retain source and migrate existing copies | Publish only with the bridge API contract and deployment target documented; test graceful disable without a bridge. No desktop default. |
| \`branch-atlas/\` | Read-only local Git branch/commit viewer, UI route and frontend assets | Optional directory plugin; retain package source for legacy migration | Publish as a directory-shaped Manifest v1 artifact after market install supports the existing package shape; test fixed Git commands are read-only and unavailable Git/repository fails closed. |
| Legacy \`wallpaper/\` | Wallpaper Engine renderer, appearance UI and asset bridge | Optional, never part of client payload | Already independently published as \`wallpaper\` 4.1.7. Keep migration non-destructive and market hash checks enabled. |
| Creator output | Arbitrary user-generated plugin files/packages | Always user data; never a build input | Creator may write drafts only. A generated package enters runtime only through explicit validation and the user install lifecycle; no automatic market publication. |

Publication order: Wallpaper is available now; next publish lower-risk \`meme\` and \`websearch\`, then \`kb\` and \`reminder\` with data/lifecycle acceptance, then \`branch-atlas\` after directory-artifact support is verified, and finally \`gate\`, \`react\`, and WeChat-only \`sticker_pack\` after their channel/hook contracts are tested. Use existing Manifest v1 and market hash verification. Do not bundle them while waiting, introduce Manifest v2, or claim a plan is equivalent to publication. Until each release is available, new installations show the feature as unavailable/not installed, while legacy installations preserve their installed copy and data.

## Upgrade compatibility

- The new installer seeds only the \`core_features/\` payload.
- Existing \`plugins/\` files and previous-install \`_internal/plugins/\` trees are not deleted. Legacy optional modules previously stored in \`_internal/core_features/\` are copied to \`data/installed_plugins/\`; unchanged core management/Agent/Skill adapters are not installed as extensions.
- A persistent migration receipt records the source revision after a successful copy or when an installed destination already exists. This prevents a later explicit uninstall from re-importing the retained recovery source on the next launch/upgrade. Failed copies do not receive a receipt and are retried; errors are logged while Brain startup continues.
- User data and original legacy plugin files are retained for recovery. The installed copy is classified as a user extension, so it can be updated or uninstalled through the existing lifecycle manager.
- No cleanup of existing plugin data, custom files, or old directories is performed automatically.

## Guards and verification

- PyInstaller spec enumerates only tracked \`brain/core_features\` files, plus the existing Skill Host and build identity.
- \`scripts/build_release.py\` rejects a Brain output tree containing bundled plugin source.
- Electron's post-pack hook rejects plugin package directories in \`app.asar\` and in the Brain sidecar.
- Python regression tests cover build guards, core-feature seeding, legacy-plugin migration, and the Node package-path checker.

## Explicitly not completed by P21.0

- Optional legacy plugins have not all been republished as independent market artifacts yet.
- The optional modules listed above still need independent Manifest v1 artifacts and market entries; do not add them to the core allowlist to restore them.
- Manifest v2, arbitrary nested multi-entry extension packages, market source precedence/override UX, rollback improvements, and automated artifact publishing remain for P21.1–P21.3.
- The final Windows candidate build and isolated install/upgrade/restart acceptance must pass before P21.0 can be marked complete.
