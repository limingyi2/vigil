# Vigil 大文件模块化 R4 交付记录

基线：`eb1e22402cb74b8348c0cce0ce1e75384d8c4f4b`（`dev`）

范围：数据页面迁移；配置、模型和工作区 API 边界审计。R4 是本轮最后阶段，不启动 R5/R6。

## 迁移对应关系

| 迁移前 | 迁移后 | 处理 |
|---|---|---|
| `pages.js` 中的 `renderData(ctx)`，含概览、Token、Trace、历史、日志五个子视图 | `pages/data.js` 中唯一的 `VigilRenderData(ctx, helpers)` | 页面主体直接移动；`pages.js` 只保留传入既有共享 helper 的 renderer 转发和原 PAGES 项。 |
| `pages.js` 共享 `li`、`topbar`、`tag`、`toMs`、`bindLi`、`confirmDialog` | 保留在 `pages.js`，通过显式 `helpers` 参数提供给数据页 | 不复制共享 UI 逻辑，不引入页面框架或全局状态。 |

数据页的图表调用仍使用 `U.lineChart`，API 调用路径、参数、超时、页面 HTML、CSS class、统计和图表计算代码均保持原样。新增的 `ctx.onDispose()` 只递增页面请求代次，确保切页后尚未返回的请求不再更新已销毁页面。

## 后端边界审计

本轮没有从 `server.py` 迁出后端函数，也没有创建候选 Python 模块。审计结论如下：

| 候选 | Handler 直接职责及依赖 | 结论 |
|---|---|---|
| `brain/api/config.py` | 检索偏好写入在 Handler 内完成管理员检查、类型校验、`_chat_lock()` 临界区、配置文件读取、临时文件写入与 `fsync`、原子替换，以及共享 `api_config` 的原地更新。 | 保留原位。把这段事务移出会改变配置竞争、写盘时机或并发可见性；没有可独立迁出的格式化/组装函数。 |
| `brain/api/models.py` | 模型列表、探测、密钥删除、运行模式切换、保存/应用/快速切换/遗忘均位于嵌套 Handler。逻辑依赖请求体、HTTP 响应、密钥脱敏、`model_hub`、完整 `api_config`、`_refresh_api_cfg`、运行任务闸门和本地模型卸载顺序。 | 保留原位。不能把配置事务、密钥处理或 P19 模型/运行模式选择抽成薄 API 模块。模型数据展示结构只是局部响应组装，没有形成更安全或更清楚的独立边界。 |
| `brain/api/workspaces.py` | GET/POST 路由依赖 Handler 管理员校验、请求身份、owner/persona/session 上下文、旧工作区归属兼容、会话统计、`_json` 响应和既有 `workspaces`/`conversations` 领域函数。 | 保留原位。授权和身份绑定与 HTTP 路由一起执行；没有可独立迁出的无状态组装逻辑，不能将授权信息改为调用方自报参数。 |

`server.py` 仍是唯一 HTTP 装配入口；本次没有新增 `api.* -> server` 或其他 Python 导入边，也没有更改配置、工作区、模型或运行模式实现。

## 最终模块依赖

```text
index.html: ui.js / chart.js / api.js / agent state-view
        └── pages/data.js ──> U、API、chart.js 的 U.lineChart
                 ↑
pages.js ────────┘  传入现有共享 UI helper，并保留唯一 PAGES 注册
        └── app.js 的既有 go()、导航和 dispose 生命周期
```

正式 Electron Builder 配置的 `files` 已包含 `src/**`，因此新 `client/src/js/pages/data.js` 会随页面源码进入 `app.asar`。`index.html` 已保证 `chart.js` 在 `data.js` 前，`data.js` 在 `pages.js` 前；所有页面仍在 `app.js` 前装配。没有新增 Python 文件；正式 PyInstaller 入口和 spec 无需改动。

## 回归测试与检查

新增 `client/tools/test-data-page-lifecycle.js`，接入 Node CI 入口。测试实际加载迁移后的 `data.js`、`pages.js` 的生产 PAGES 表，并抽取生产 `app.js` 的 `go()`；覆盖唯一 renderer 注册、脚本顺序、Electron `src/**` 打包规则、首次渲染、Token 图表、原有 API 路径/查询参数、切页返回、dispose 后挂起请求失效。

R4 最终验证结果：

| 检查 | 结果 |
|---|---|
| Python 全仓 `pytest -q` | 1,908 项已收集；完整测试命令退出码 0，无失败。使用项目 Python 运行时并将其加入 `PATH`，保证 Coding Harness 子进程调用同一 Python。 |
| Node CI：生命周期、统一运行时、公开更新 | 38 passed，0 failed；包含 R2 视频、R3 插件市场和 R4 数据页面生命周期。 |
| `compileall brain pet` | 通过。 |
| JS `node --check` | `client/src/js/` 全部 JS 文件及新增测试通过。 |
| `git diff --check` | 通过。 |
| GitHub Actions | 对 R4 代码 head 的 push run #518 和 PR run #519 均完成；Python 单测、全仓语法、真实 FAISS 检索三个作业全部成功。最终报告的文档更新只改本文件，更新后的 head 会重新触发同一 CI。 |

## Windows 候选安装包

状态：**`BLOCKED_ENVIRONMENT`**。当前 Windows 主机构建工具检查未发现 Visual Studio 2022 Build Tools、`vswhere.exe`、`cl.exe`、npm，也没有 `client/native-window-host/build/Release/native-window-host.exe` 或安装依赖中的 Electron 可执行文件。缺少 MSVC C++ 编译器和原生 helper，无法构建正式入口要求的候选安装包；因此本阶段没有产出或检查 `app.asar`、安装后启动或原生窗口托管，也不声称 Windows 安装包通过。

复验需要 Windows 2022 Build Tools（含 MSVC C++ x64 工具和 Windows SDK）、Node/npm 与项目锁定依赖、项目 Python 构建依赖和 PyInstaller。安装所需工具后，从干净的 R4 提交复验：

```powershell
.\client\native-window-host\build.cmd
$env:VIGIL_BUILD_PYTHON = (Get-Command python.exe).Source
$env:VIGIL_BUILD_CANDIDATE = '1'
python .\scripts\build_release.py --candidate
```

成功后还需检查安装包内 `resources/app.asar` 的 `src/js/pages/video-workbench.js`、`src/js/pages/plugin-market.js`、`src/js/pages/data.js`，安装并启动客户端，进入/切换/退出三个页面，并验证 native window host 生命周期。

## PR 与提交

分支：`codex/server-pages-r4`，目标分支：`dev`。

提交 SHA：以 PR 当前 head commit 为准。Draft PR：[limingyi2/vigil#39](https://github.com/limingyi2/vigil/pull/39)，保持 OPEN、未合并。
