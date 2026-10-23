# Vigil 大文件模块化 R2 迁移记录

目标分支：`dev`。R2 分支从 R1 合并提交 `084b64e33df0b5acc814871bb84526358ae58219` 建立。范围仅含 `_query_param` 请求参数解析和视频工作台页面迁移；没有修改任务分类、模型路由、Agent/Todo、Coding Harness、RAG、插件生命周期、聊天 SSE、工作区权限或配置持久化逻辑。

## 迁移前后函数对应

| 基线 | 目标 | 处理 |
|---|---|---|
| `brain/server.py::_query_param(path, key)`（原约 1006 行） | `brain/api/request.py::query_param(path, key)` | 将原函数体和局部 `urllib.parse` 导入原样移到新模块。`server.py` 顶层导入 `query_param`，保留同签名 `_query_param` 轻量转发导出，原 HTTP Handler 调用点和老导入方继续工作。 |
| `client/src/js/pages.js::renderVideoWorkbench(ctx)`（R1 基线约 5938–6076 行） | `client/src/js/pages/video-workbench.js::renderVideoWorkbench(ctx)` | 页面 renderer 完整搬移；与基线函数体逐字比较一致。`pages.js` 只在原 `PAGES` 项引用 `global.VigilRenderVideoWorkbench`，真实实现只有一份。页面没有独占的外部 helper 需要迁移。 |

## 依赖方向和运行时顺序

```text
brain/server.py ──> brain/api/request.py
      └─ _query_param(path, key) 兼容转发 ──> query_param(path, key)

index.html: ui.js / api.js / agent state & view
      └─ pages/video-workbench.js ──> window.API.state + window.vigil.videoWindow
             └─ pages.js: 原 PAGES 列表绑定 global.VigilRenderVideoWorkbench
                    └─ agent-center scripts ──> app.js 导航、ctx.onDispose()
```

`brain/api/request.py` 不导入 `server`、Handler、数据库或 runtime。服务端兼容函数只转发，不保留第二套解析逻辑。页面脚本遵循现有 classic-script 模式，位于 UI/API/Agent 页面依赖后、`pages.js` 和 `app.js` 前；仍由原 `PAGES` 和 `app.go()` 注册、切页、清理。

## 变更文件

- `brain/api/__init__.py`、`brain/api/request.py`：增加轻量 API 包和纯请求参数解析。
- `brain/server.py`：导入新 helper，保留兼容 wrapper。
- `tests/test_server_helpers.py`：补 URL 编码、重复参数、空/缺失/坏输入及兼容导出测试。
- `tests/test_p2_agent_http.py`、`tests/test_p16_stream_image.py`、`tests/test_p161_image_workspace.py`、`tests/test_cloud_context_tiers.py`：为 AST 隔离 Handler 测试显式注入新 helper，继续走实际 server wrapper。
- `client/src/js/pages/video-workbench.js`：唯一视频工作台 renderer。
- `client/src/js/pages.js`：移除原 renderer 函数体，保持原页面 id、名称、图标和顺序，绑定新全局 renderer。
- `client/src/index.html`：按依赖顺序加载新页面脚本。
- `client/tools/test-video-workbench-lifecycle.js`：新增真实 `app.go()` 源函数 harness 与页面资源/生命周期/请求合同测试。
- `client/tools/test-p2-stream-lifecycle.js`：载入视频页面 harness，复用仓库现有 CI Node 测试入口。

## 测试和 GitHub Actions

- 全仓 Python（`pytest` 全部 `tests` 与 `brain`）：**1887 passed、21 skipped、54 subtests passed**。先前一次运行中 3 个 Coding Harness 子进程测试命中 Windows 的 Microsoft Store `python` 别名；把随附 Python 加入 `PATH` 后，3 个针对项 **3/3 passed**，全仓复跑通过。
- 后端迁移专项：**112 passed、7 subtests passed**，包含 `test_server_helpers`、真实 Agent HTTP、图片/工作区、P19.2 和云端上下文测试。
- Node CI 对应测试（共享流生命周期、统一运行时、公开更新、新增视频页面 harness）：**27 passed、0 failed**。
- 新页面生命周期测试单独运行：**3 passed、0 failed**；覆盖 PAGES 唯一注册、脚本加载顺序、聊天页到视频页的真实 `app.go()` 清理顺序、视频页返回其他页、重复进入、Resize/IntersectionObserver 与窗口/API 监听销毁、能力事件 URL/body。
- Python `compileall -q brain pet`、`node --check`（`pages.js`、新页面脚本、`app.js`）和 `git diff --check` 均通过。
- 额外尝试直接运行整个 `client/tools/test-*.js` 目录时，15 个脚本因工作树没有安装 `client/node_modules` 依赖而无法启动；仓库 CI 实际使用的 Node 测试集合通过。
- GitHub Actions run **507** on implementation commit `8a00cc6cfe757c38ef6a82affe32febb3236dfb2` passed all jobs: Python 3.12 unit tests, full `brain/` + `pet/` syntax check, existing Node CI command (including the video lifecycle harness), and real FAISS RAG acceptance.

## 页面生命周期验证记录

新测试抽取并执行生产 `app.js` 中的 `go()` 函数体，先从 chat 状态导航到 `PAGES` 中的视频 renderer，再导航到其他页。断言 app 先调用上一页 dispose，再 render 新页；视频 renderer 通过真实 ctx 注册 cleanup。退出时 ResizeObserver、IntersectionObserver、window resize、原生窗口可见性和事件监听器均注销，托管窗口接收 `visible(false)`；重复进出后每类活动监听仍只有一个，不累积重复注册。测试还锁住页面 HTML 主要结构、Profiles/STATUS/ATTACH/start bounds 行为和 `/api/capabilities/events` POST 参数。

## Python 与 Electron 打包检查

- 正式 Brain spec：`deploy/brain/vigil-brain.spec` 从 `brain/server_launch.py` 分析依赖。使用临时 `brain/build-info.json`（由正常 release 脚本生成，本仓库快照不含该生成文件）在系统临时目录执行 PyInstaller 6.22.3 onedir 构建，生成 `vigil-brain.exe`；PyInstaller 交叉引用图确认 `api.request` 在冻结模块图内。临时身份文件在构建脚本结束时删除，未写入提交。
- 循环依赖：新模块只使用标准库 `urllib.parse`；`server -> api.request` 单向，没有 `api.request -> server`。
- Electron 静态打包：`client/package.json` 的 `files` 包含 `src/**`，故新 JS 位于 asar 收集范围；自动化检查确认 `index.html` 中 UI/API/Agent 依赖、新脚本、`pages.js`、`app.js` 顺序正确。
- 未运行 Electron Builder / Windows 安装器构建，也未直接检查最终 `app.asar`；工作树没有 `client/node_modules`。因此 Electron 资源实际进入安装包仍待 Windows 构建 CI 产物验收，不能据此宣称完整安装包通过。

## 未验证事项与 PR 状态

完整 Windows Electron 安装器和最终 asar 内容未在本地构建。该提交仅实现 R2，不开始 R3。

Draft PR：[limingyi2/vigil#37](https://github.com/limingyi2/vigil/pull/37)，目标 `dev`，OPEN、未合并。

代码与测试迁移提交 SHA：`8a00cc6cfe757c38ef6a82affe32febb3236dfb2`。报告更新后的最新 PR head SHA 由 PR #37 显示。
