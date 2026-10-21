# Vigil P12 验收记录

分支：`codex/p12-agent-consistency`；基线：`origin/dev` 的 `46f7c5b`（已包含 P10 / PR #11）。所有修改在独立 worktree 内完成，原 `F:\zhixia`、其他 worktree、用户安装、用户数据与插件目录未改动。未合并 PR，未修改 master，未发布正式版本。

## 五轮诊断

| 轮次 | 失败证据与根因 | 修复与结果 | 提交 |
| --- | --- | --- | --- |
| 1 | 原始 XML 文本在完整解析之前直接写入正文、事件和快照，结束后的替换无法撤回已显示协议。 | 增量边界隔离跨 chunk 协议；原生 FC 优先、明确启用的 XML 兼容路径保留；代码示例按 Markdown 原样保留；畸形调用失败。确定性测试及真实 Electron + Brain 流式 FC/XML 通过。 | `e8e7bab` |
| 2 | 暂停请求丢失 src/conversation_id，后端 owner 与创建任务时不一致，真实 run_id 仍返回 404。 | 暂停携带完整归属；快照为权威；404 查询当前会话任务，403 与不可达分别显示；防止重复暂停。重启、台账删除、继续发送通过。 | `e8d3af1` |
| 3 | 执行记录层级厚重、工具证据不全，市场入口藏在设置；模型菜单有多余说明。 | 真实工具开始即显示、按 call_id 更新同一行，参数/结果折叠并脱敏；市场一级入口复用原页面，保留真实聊天 DOM。模型与上下文配置保留。自动测试通过；真实模型暴露了长任务监听丢失，故本轮未判全部通过。 | `f448ba4` |
| 4 | 八秒历史轮询替换运行中 DOM，断开局部 SSE 监听；恢复任务的子 run 未继承父会话 ID。 | 运行中禁止历史替换，结束采用权威快照；恢复继承并校验会话。8.5 秒工具门控测试修复前失败、修复后通过；真实模型搜索/读取/追问及暂停续跑验证完成。 | `9d0768f` |
| 5 | 加强测试发现波浪线代码围栏、带引号凭据和原生 FC 伴残缺 XML 的边界；异步发送准备、启动重绘及已离开页面的迟到列表响应也会覆盖新会话状态。用户另反馈重复设置/创作按钮、英文思考、气泡和导航顺序。 | 保留原生有效调用并隔离 XML；补齐字面代码/脱敏；防重复提交，连接成功只刷新数据，不再重建聊天；失效页面不得回写状态。中文事实状态先于对应工具，删除原始推理气泡和测试说明。一级导航拖动及隐藏可持久化；仅一个设置入口；删除输入框内重复插件/技能按钮。最终验证见下。 | `ca8685f` |

没有以刷新页面解决运行故障，没有关闭工具、绕过校验、放宽失败断言或重写整个 Agent 客户端。旧测试中的入口改为新入口；API fixture 补足生产快照契约；不再引用已移除的刷新按钮。

## 最终自动验证

- Python 全仓：**1435 passed / 17 skipped**，见 [完整日志](p12-full-python.txt)。跳过项包括缺省本地资源和当前 Windows 进程未具备的 symlink/mkfifo 能力。
- Node：**73 passed / 0 failed**，见 [完整日志](p12-all-node.txt)。
- 实际 Electron 主进程 + preload + renderer + 隔离 Brain：**18 场景通过**，见 [集成日志](p12-full-electron-brain.txt)。包含会话隔离、Creator 恢复、工具失败、取消、Provider 超时、窗口退出、Brain 崩溃、1200 个事件/256 行有界台账、断流恢复且不重复派发。
- P12 实际 Electron + Brain / Fake Provider：原生与跨 chunk XML、十轮聊天、owner 暂停、重复点击、暂停续跑、重启、台账丢失、市场往返、导航持久化、中文顺序、模型与创作菜单，见 [P12 日志](p12-round5-electron.txt)。
- Electron IPC fixture 回归： [7 项聊天/取消/刷新恢复](p12-p2-electron.txt)、[Creator](p12-creator-electron.txt)、[模型设置与上下文预算/模式切换/进程重启](p12-model-settings.txt)、[插件市场入口与安装交互](p12-market-electron.txt)。fixture 与真实 Provider 测试明确分开。
- 延迟初始会话列表 400ms 的回归在修复前可稳定复现旧页面写回 welcome，修复后通过。生产代码没有增加等待来掩盖问题。

## 流式边界与事件证据

[protocol-trace.json](p12-evidence/protocol-trace.json) 保存受控原始模型事件、Provider 归一化、Brain durable 事件、逐 chunk 快照和真实工具调用次数；[round1-events.json](p12-evidence/round1-events.json) 保存真实 Electron 接收的 SSE/IPC 事件。

已覆盖：原生 FC、完整/逐字符/跨 chunk XML、正文后调用、多调用、残缺原生 FC 配完整 XML、原生有效 FC 配残缺 XML、畸形或断线调用、普通 XML 及反引号/波浪线代码示例。工具调用有证据才显示，畸形调用无副作用且不能成功。原始协议不进入正文、最终回答或恢复快照。

## 真实模型（明确区别于 Fake Provider）

采用当前已配置并获授权的 `xiaomi / mimo-v2.6-flash`，真实网络请求累计 **11 次**（含诊断失败请求），协议为 **native FC**。凭据只在受控代理进程内使用，不写入报告。只读工具限制为本仓库 README 搜索和读取，无文件写入。

| 场景 | 实际结果 | SSE/IPC 事件数 | 工具次数 | 前端终态 |
| --- | --- | --- | --- | --- |
| 搜索 README* → 读取 → 模型继续归纳 | done | 57 | 搜索 1、读取 1 | 发送消息 / busy=false |
| 完成后同会话连续追问 | done | 6 | 0 | 发送消息 / busy=false |
| 暂停 → 续跑（会话继承修复后重验） | done | 123 | 0 | 发送消息 / busy=false |

保留失败证据：[首次真实模型](p12-evidence/real-model-before.json) 为后端 done、前端仍暂停；[搜索/追问复验](p12-evidence/real-model-search-followup.json) 中搜索与追问成功，但当时续跑仍显示待确认；[最终真实暂停续跑](p12-evidence/real-model.json) 完成且恢复发送。没有把早先失败改写为通过。

真实模型此次采用原生 FC，**真实模型 XML 路径 NOT RUN**；XML 边界由 Fake Provider 经实际 Brain/Electron 验证。原始英文 reasoning 不翻译成臆造内容，UI 只显示中文阶段状态；普通模型正文保持原样。

## 真实 Electron 截图

以下 UI 验收使用真实 Electron/Brain 与受控 Fake Provider，模型名称明确标为 `mimo-fixture`。

- [运行中](p12-evidence/running-light.png)
- [已完成 / 浅色](p12-evidence/completed-light.png)、[深色](p12-evidence/completed-dark.png)
- [模型选择 / 浅色](p12-evidence/model-picker-light.png)、[深色](p12-evidence/model-picker-dark.png)
- [添加与模型：保留唯一插件/技能选择](p12-evidence/add-model-menu-light.png)
- [一级导航展开](p12-evidence/navigation-expanded.png)、[隐藏](p12-evidence/navigation-collapsed.png)
- [一级插件市场](p12-evidence/market-light.png)

真实模型调用期间另有 [运行中](p12-evidence/real-model-running.png) 与 [完成](p12-evidence/real-model-completed.png) 的当时截图；这些是修复过程中的早期界面，不代表最后一版 UI。

任务完成后仍可发送的证据：P12 日志中的十轮连续发送，以及真实追问的 done + button.busy=false；暂停续跑未重复已确认的工具效果。

## 修改范围与剩余限制

- 后端：`brain/model_providers/tool_protocol.py`、`brain/agent_runs.py`、`brain/server.py`；没有整体重写。
- 客户端：`agent-state.js`、`agent-view.js`、`pages.js`、`app.js`、样式、index、Electron SSE/IPC 归属；原有 Electron + Python Brain 与插件机制保留。
- 本次市场仅调整导航；Wallpaper 制品安装失败属于独立 P11 工作，没有将其混入本轮修改，也没有跳过 Manifest、SHA-256、权限或事务安装校验。
- **BLOCKED（旧用例环境/代码依赖）**：`test-chat-v2-electron.js` 仍依赖当前 dev 已移除的 `client/electron/wallpaper-host.js` 和 vendor `lib/index.js`，不能执行完整旧壁纸界面回归。见 [旧测试错误](p12-chat-electron.err)。本轮没有恢复废弃 Core 壁纸实现，也不把该项计为通过。Creator/工作区/市场的现行集成和 IPC 用例通过。
- Windows 候选构建使用 `F:\zhixia\venv\Scripts\python.exe scripts/build_release.py --candidate`。制品与哈希以 `client/dist-release/release-manifest.json` 为准；构建日志位于 worktree 外，只有实际产出并验证后才交付。不会启动安装程序、替换用户安装或发布正式版。
