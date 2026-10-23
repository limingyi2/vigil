# Vigil R3：插件市场页面及 API 适配层拆分

基线：R2 合并提交 `ccd075700804deb8ed6a1a4cbee0a779a6e1d324`（`dev`）  
交付分支：`codex/server-pages-r3` → `dev`

Draft PR：[#38](https://github.com/limingyi2/vigil/pull/38)，保持 OPEN、未合并。

## R2 Windows 发布验证

| 检查 | 结果 |
|---|---|
| 按 `client/package-lock.json` 固定版本恢复 Electron 依赖 | PASS；Electron 28.3.3、electron-builder 24.13.3。pnpm 从 npm 锁文件导入后安装，未升级项目版本；临时生成的 pnpm 文件已移除。 |
| 正式 Windows 候选构建 | BLOCKED；`client/package.json` 的 Electron Builder 配置要求额外资源 `client/native-window-host/build/Release/native-window-host.exe`。该隔离工作树中没有此文件；`client/native-window-host/build.cmd` 实际运行后因缺少 Visual Studio 2022 Build Tools 退出，无法生成资源。没有把静态检查描述为安装包验收。 |
| `app.asar` 检查 | BLOCKED；候选包未产出，因此没有可检查的归档。正式 `files` 配置包含 `src/**`，覆盖 `src/js/pages/video-workbench.js`。 |
| 页面脚本顺序 | PASS；`ui.js`、`api.js`、`pages/video-workbench.js`、`pages/plugin-market.js`、`pages.js`、`app.js` 依次加载。 |
| 视频工作台首次进入及切页 | PASS；R2 的真实 renderer 生命周期测试 3 项通过，覆盖首次进入、从聊天进入、返回及重进时的监听器/观察器清理。 |

## 页面迁移

| 迁移前 | 迁移后 | 处理 |
|---|---|---|
| `pages.js` 中 `marketPending` | `pages/plugin-market.js` | 移动市场页独占的待处理状态 Map。 |
| `pages.js` 中 `renderPluginMarket(ctx)` | `pages/plugin-market.js` 中的 `renderPluginMarket(ctx, confirm)` | HTML、class、按钮和 `/api/market/*` 请求代码原样搬迁；共享确认对话框由 `pages.js` 的薄 renderer 引用传入，未复制辅助实现。 |
| `PAGES` 的 market 项 | `render: renderPluginMarket` | 保留 `PAGES` 和 `app.go()` 页面机制；只有一处 renderer 注册和一份实现。 |

新脚本在 `pages.js` 前加载，按 classic-script 模式读取既有全局 `U`、`API`，并注册 `window` 上的市场 renderer。`vigil-market-changed` 监听、`ctx.onDispose()` 解绑、操作按钮请求及 chat → market → chat 暂存恢复均保留。

## 后端边界及调用链

只迁出已有明确领域边界的合并逻辑：`server.py` 内嵌的 `_management_items()` 对应 `brain/api/plugins.py::management_items(plugin_ops, channels)`。该适配器继续读取现有 `plugin_ops.overview()`、`plugin_host.listing()` 和 `plugin_manager.list_plugins()`，没有复制插件注册表、运行状态或配置缓存；它不导入 `server.py`。模块导入在首次构造管理视图时才发生。

管理接口调用链保持为：已认证 GET/POST → `Handler._plugin_admin()`（POST 的管理员检查仍在此）→ 插件管理器 → `_management_items()` → `api.plugins.management_items()` → `Handler._json()`。Handler 仍解析请求、校验身份/权限、选择路由、设置状态码并写响应；插件管理、安装/卸载事务和热加载时机未移动。

插件提供的 HTTP 路由保持原路径：`do_GET` / `do_POST` 完成通用请求处理后调用 `Handler._plugin_route()`；它仍从 `plugin_host.route_table()` 精确匹配 method/path，检查插件启用状态和设备权限，注入 principal，调用 `(body, query)` 插件函数，并由 Handler 处理文件或 JSON 响应。路由优先级与禁用后的拒绝行为没有改变。`/api/market/*` 仍由现有 `plugin_market` 插件路由提供。

## 测试和检查

- 全仓 Python：`pytest tests -q`，1,622 项，1,609 通过、13 跳过、0 失败。Windows 子进程需要 `python` 命令；测试时将已配置 Python 运行时目录临时加入 PATH。
- 插件权限和生命周期定向测试：`test_plugin_market_client_contract.py`、`test_api_permissions.py`、`test_restart_prompt_wiring.py`、`test_pc_core_context.py` 全部通过。集成用例通过真实 Handler 路径验证禁用、启用、渠道权限、工具和 HTTP 路由访问、管理/市场视图一致、配置持久化及卸载。
- Node CI 集合：31 项通过，0 失败；包含新增页面生命周期测试和 R2 视频页面测试。
- 新市场 renderer 测试：4 项通过，覆盖首次渲染、列表刷新事件、`/api/market/refresh` 请求、销毁解绑、重复进入，以及实际 `app.go()` 的 chat → market → chat 暂存恢复。
- Python `compileall -q brain pet`、`pages.js` / `pages/plugin-market.js` / `pages/video-workbench.js` / 新测试的 `node --check`，以及 `git diff --check` 均通过。
- GitHub Actions PR run #512 的单元测试、JS/客户端回归、全仓语法检查及真实 FAISS RAG 验收全部通过。
- 本地因缺少 FAISS 未运行真实 FAISS 验收；已由上述 GitHub Actions job 覆盖。本地 Windows 候选安装包未完成，因此没有宣称真实安装包场景通过。

## 未验证事项

- 当前机器无法构建 native window host，故 R2 和本次包含 R3 代码的 Windows 候选安装包、`app.asar` 实际条目及安装后 Electron 运行均未验证。需要在安装 Visual Studio 2022 C++ Build Tools 的 Windows 构建机上重新生成 native helper，运行正式候选构建，再检查归档中的两个页面脚本并进行安装后冒烟测试。
- 插件市场的真实 Electron DOM、用户级安装/更新源以及重启后的运行场景未在本机真实安装包中验收；相关服务端路径已由 Python 集成测试覆盖。
