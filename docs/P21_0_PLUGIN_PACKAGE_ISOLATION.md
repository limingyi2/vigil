# P21.0 — Plugin package isolation

Status: implementation in progress; this phase is limited to the package boundary and backward-compatible startup migration.

## Problem confirmed at the merged P20.2 baseline

- \`deploy/brain/vigil-brain.spec\` enumerated tracked files under \`brain/plugins\` and packaged them into the Brain sidecar.
- \`brain/server_launch.py\` copied the bundled \`plugins/\` tree into the application home on first launch.
- Consequently, excluding plugin files from Electron's \`app.asar\` alone was insufficient: the Brain payload could still ship built-in extension code and seed it onto a clean installation.
- The Electron package source list currently contains only the client application directories, but it lacked a general package-time assertion against extension payloads.

## P21.0 boundary

The release package ships core capability adapters under \`core_features/\`; optional extensions are loaded from the user-managed \`data/installed_plugins/\` directory. The release build must reject a Brain payload containing either a root \`plugins/\` tree or \`_internal/plugins/\`, and Electron packaging must reject extension package directories under \`src/\`, \`electron/\`, or \`assets/\`.

Core capability adapters moved out of \`brain/plugins\` in this phase:

- \`calc\`, \`gate\`, \`kb\`, \`plugin_market\`
- \`react\`, \`remote\`, \`skill\`, \`websearch\`

These are core runtime capabilities/integration adapters, not marketplace extension artifacts. Optional modules remain in the repository as migration/source material but are not part of the new packaged runtime. The marketplace's own management UI and generic resource bridge remain core client code; the plugin packages themselves do not.

## Upgrade compatibility

- The new installer seeds only the \`core_features/\` payload.
- Existing \`plugins/\` files are not deleted. Unchanged copies of core modules are skipped; legacy optional packages and user-modified plugin files are copied to \`data/installed_plugins/\` only when no destination already exists.
- User data and original legacy plugin files are retained for recovery. The installed copy is classified as a user extension, so it can be updated or uninstalled through the existing lifecycle manager.
- No cleanup of existing plugin data, custom files, or old directories is performed automatically.

## Guards and verification

- PyInstaller spec enumerates only tracked \`brain/core_features\` files, plus the existing Skill Host and build identity.
- \`scripts/build_release.py\` rejects a Brain output tree containing bundled plugin source.
- Electron's post-pack hook rejects plugin package directories in \`app.asar\` and in the Brain sidecar.
- Python regression tests cover build guards, core-feature seeding, legacy-plugin migration, and the Node package-path checker.

## Explicitly not completed by P21.0

- Optional legacy plugins have not all been republished as independent market artifacts yet.
- Manifest v2, arbitrary nested multi-entry extension packages, market source precedence/override UX, rollback improvements, and automated artifact publishing remain for P21.1–P21.3.
- The final Windows candidate build and isolated install/upgrade/restart acceptance must pass before P21.0 can be marked complete.
