# Vigil P5 插件安装与 Electron 聊天验收

**验收日期：** 2026-10-17

**工作区：** `F:\zhixia`，分支 `master`

**验收基线提交：** `5d78e6024276db581cfc1fdf5a0b495811168c12`
**本次提交：** 未创建；按要求保留在工作区等待验收，不自动提交或推送。

## 结论

- 插件市场安装按钮的现有主链路没有复现无响应。隔离 Electron 交互测试确认重复点击被拦截，失败展示真实后端错误并恢复可重试状态，成功后重新读取市场状态并显示已安装。
- 找到并修复一个聊天流传输故障：Electron 网络失败/超时原先只返回失败对象，没有稳定送达终止 SSE 错误事件；渲染层忽略返回值后可能留下 `running` 记录，或把部分输出显示成已完成。
- Brain 假模型测试确认插件 Schema、模型工具调用、插件宿主分发、工具结果回灌及最终回复的链路通过。Electron 渲染器隔离测试确认 `pc` 渠道、默认 `main` 会话及成功/错误流状态处理通过。
- 没有发起真实模型请求、没有安装或卸载真实插件、没有触碰用户聊天数据，也没有构建或发布安装包。

## 安装按钮调用链

1. `client/src/js/pages.js` 的 `renderPluginMarket()` 调用 `GET /api/market/list` 并渲染安装按钮。
2. 按钮点击处理器同步禁用按钮并显示“安装中…”，然后调用 `POST /api/market/install`，请求体为插件名。
3. `brain/server.py` 将 `/api/market/*` 路由到 `brain/plugins/plugin_market.py`。`api_install()` 校验名称及市场清单后调用 `plugin_ops.install_market_directory()`。
4. `brain/plugin_ops.py` 对源目录做静态校验，并用共享事务提交插件文件和注册表；事务失败由事务层回滚，不导入新插件代码。
5. 前端检查 HTTP/API 与响应 JSON 的 `ok`。失败时显示后端错误并恢复按钮；成功时重新请求 `/api/market/list` 并刷新已安装状态。

**激活边界：** 后端安装结果明确提示需重启 Brain 才能生效。市场列表会刷新，但已运行 Brain 的工具注册不会被前端伪装成即时刷新；需要按产品现有重启流程加载新工具。

### 安装交互测试

新增 `client/tools/test-plugin-market-electron.js`，运行真实 Electron renderer 与 sandbox preload，后端用隔离 IPC 替身：

- 成功：按钮进入禁用/安装中状态，服务端返回成功后列表刷新并展示已安装。
- 失败：保留后端错误消息，恢复安装按钮。
- 并发点击：安装请求未返回时再次点击不产生第二个请求。

尚未在真实市场仓库和真实 Brain 上执行安装；插件事务及失败回滚由现有 `brain/tests/test_plugin_transactions.py` 回归覆盖。

## Electron 聊天调用链与修复

1. `client/src/js/pages.js` 的聊天发送逻辑调用 `/api/chat/stream`，请求包含 `text`、`src`、唯一 `request_id`。客户端当前 `src=pc`；会话未显式指定时，Brain 的 `_display_owner()` 将它归入 `main`。本地展示记录键也包含连接地址、设备、凭据类型、渠道和 `main` 会话。
2. `client/electron/preload.js` 通过 `api:stream` IPC 把请求交给主进程；`client/electron/main.js` 选择已配置 Brain 地址、附带设备认证，并给请求补上配置渠道 query 参数，再转发 SSE 事件。
3. `brain/server.py` 的 `/api/chat/stream` 按请求渠道建立展示记录/Agent run，并在聊天锁内调用对应聊天流程。
4. 需要工具时，`brain/brain.py` 的 `draft_native()` 通过 `tools.all_specs(brain, src)` 生成工具 Schema；模型返回 `tool_calls` 后由 `tools.invoke_spec()` 分发，工具结果以对应的 `role=tool` 消息回灌同一模型对话，再生成最终答复。
5. Electron renderer 按 `request_id`、`run_id` 和当前渠道作用域过滤事件；`done` 保存为成功，`error` 保存为失败/中断。

**已确认故障：** `requestStream()` 遇到请求超时、连接错误、响应提前关闭时原先只 resolve `{ok:false}`，没有保证发出 SSE `error`。renderer 忽略 `apiStream()` 返回值，并在迟到的兜底计时器中将部分正文按时间戳展示，但本地记录仍可能处于 `running`。Electron 回归测试在修复前复现了传输失败用例。

**修复：** `client/electron/main.js` 在超时、连接错误、响应中止或未收到 `done/error` 的提前关闭时推送不含凭据的连接错误事件，并将缺少终止事件的响应视为失败。`client/src/js/pages.js` 处理 IPC `{ok:false}` 返回值；若流返回但没有终止事件，明确保存为 `interrupted` 并显示不完整结果，不再显示为成功。

新增 Brain 假模型回归测试使用内存中的 `vigil_smoke` 插件对象和真实 `plugin_host.run_tool()` 分发，确认模型请求含 `smoke_ping` Schema、模型确实返回结构化 tool call、宿主收到 `src=pc` 并执行插件函数、原始标记 `VIGIL_PLUGIN_CALL_OK` 作为 tool-role 结果回灌，最后成为最终回复。Electron 测试另外验证它发出的渠道、默认会话、部分输出与传输失败状态。

## 修改文件

本次新增或修改：

- `client/electron/main.js`：将流式传输错误和提前结束转成明确终止事件。
- `client/src/js/pages.js`：处理主进程失败返回；超时兜底以中断状态保存部分结果。
- `client/tools/test-p2-electron.js`：覆盖聊天传输失败、提前终止、渠道/会话作用域。
- `client/tools/test-plugin-market-electron.js`：新增安装按钮 Electron 交互回归。
- `client/tools/test-dev-integration-electron.js`：将 Creator 测试调用与当前 `/api/creator/jobs` 路由对齐，并等待实际请求而非过早检查。
- `brain/tests/test_settings_model_wechat.py`：初始化 `Brain.__new__` 渠道上下文；新增 Fake Provider 插件工具端到端 Brain 回归。
- `brain/tests/test_creator_install.py`：更新测试插件 fixture，使它满足当前 Creator 插件工具契约。
- `brain/tests/test_plugin_structure.py`：更新模板提示测试的过期预期文本。
- `reports/VIGIL_P5_PLUGIN_INSTALL_CHAT_ACCEPTANCE.md`：本报告。

工作区原有未提交的 `client/tools/test-p2-creator-electron.js` 修改和未跟踪文件均保留，未纳入本次修改，也未清理。

## 测试结果

- 全量 Brain pytest：`python -m pytest -q -ra tests`，全量通过；6 项因当前 Windows 环境不支持/未授权 symlink 或 FIFO 操作而按测试设计跳过。
- Fake Provider 插件工具链：包含在 `brain/tests/test_settings_model_wechat.py`，断言 Schema、tool call、宿主执行、回灌标记及最终答复。
- Electron 聊天流生命周期：`client/tools/test-p2-electron.js`，通过：流快照替换、传输失败、提前结束部分输出、取消、刷新恢复、无未捕获 renderer 错误。
- Electron 插件市场：`client/tools/test-plugin-market-electron.js`，通过：失败反馈/恢复、重复点击拦截、成功后刷新。
- Electron 开发集成：`client/tools/test-dev-integration-electron.js`，通过：聊天流、Creator jobs 路由、附件及错误路径。
- stream lifecycle Node 测试：`node --test client/tools/test-p2-stream-lifecycle.js`，15/15 通过。
- JS 语法检查：`client/electron/main.js`、`client/src/js/pages.js` 及新增/修改 Electron 测试通过 `node --check`。
- `git diff --check`：通过。
- `client/package.json` 没有前端 bundler/build 脚本；客户端直接加载 `src`。已执行脚本清单检查及相关源文件语法检查。没有运行 `pack`、`dist` 或生成安装包。

## 覆盖范围和限制

- 安装按钮通过隔离 Electron renderer 与 IPC fixture 进行真实 DOM 点击验收；后端共享安装事务/回滚由 Brain pytest 覆盖，未将一个真实市场文件复制到用户插件目录。
- Brain Fake Provider 测试完整覆盖插件工具 Schema 与执行回灌，但 Electron 客户端测试中的 Brain IPC 由隔离替身响应；没有将测试 Electron 主进程连接到一个独立 Fake Provider Brain 服务做单进程贯通测试。
- 因此建议后续另做一次隔离数据目录下的 Electron 主进程 + Fake Provider Brain 联合冒烟。该测试不需要真实 API Key，也不需要生产聊天历史。
- 真实 Electron + Fake Provider 联合冒烟值得安排，但本次没有执行；无需授权真实 MiMo 请求。真实 MiMo 仍应由用户个人电脑手动验收。
- 本地 Windows Electron/pytest 测试通过；本次没有触发 GitHub Actions，故没有 Linux/Windows CI run URL。安装后 Brain 重启、真实插件工具刷新、远程/微信渠道下的模型生成及真实 API 行为未由本次隔离测试证明。

## 手动验收步骤

1. 使用独立测试插件市场和隔离 Brain 数据目录启动客户端，不连接生产用户数据。
2. 点安装并观察按钮状态；分别用测试后端注入成功和失败结果确认列表更新及错误恢复。不要选择真实插件安装到日常环境。
3. 让 Brain 按正式重启流程加载新插件，检查 `/api/plugins` 与工具 Schema；先用本地 Fake Provider 触发工具调用，核对宿主返回值和聊天最终答复。
4. 检查主进程/Brain 日志只包含错误类别、请求标识或渠道等诊断字段，不含请求正文、API Key 或 Token。
5. 若需真实 MiMo 验证，由用户在个人测试配置中执行；不得把真实密钥放入 CI。
