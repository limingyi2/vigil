# Vigil Agent Center — P2 验收报告

日期：2026-10-11。基线：本地 `master / 47e3a2b`（P1）。本阶段止于客户端状态查看、事件历史、提醒及已有 owned 任务操作。

## 1. 交付内容

左侧全局导航新增 **Agent Center**，与原有统一管理页面分开；原有云端节点管理语义保持不变。

- 展示 Codex App Server 连接状态、受管任务、状态、最近活动、当前工具操作类型、失败次数和历史轮次。
- 提供连接、重连、断开、刷新，以及通过 P1 既有接口创建独立受管任务、选择 Codex 模型、启动和中断。页面明确说明只读沙箱、网络禁用及启动会调用所选模型。
- owned 与 observed 分区。observed 始终 `unknown`，仅显示历史元数据；历史轮次的 `inProgress` 不转换为实时运行结论，没有任务控制入口。
- 审批/输入事项只展示与提醒。页面没有批准、拒绝或答复按钮；后端审批响应仍然返回 `409 approval_response_disabled`。
- 复用现有 `API.get/post/pick → preload → 主进程 HTTP 代理`，凭据不进入渲染进程。管理员与 loopback 闸门保留。
- 浅色、深色与 Wallpaper 使用同一 DOM。新增 CSS 仅作用于 Agent Center；阅读面板不叠加 `backdrop-filter`。

## 2. 事件同步与通知规则

单实例监督器随 Electron 页面壳启动，无需先打开 Agent Center；切换页面后仍继续监督。页面订阅在离开时释放，避免残留 DOM 监听。

1. 初次载入读取现有事件作为历史回放，不发送桌面通知。
2. 后续以 `events?after=<cursor>` 增量读取。先读取事件，再取任务/审批快照，避免新建任务的事件无法归属。
3. `gap` 或 `connection_id` 改变时重取 `after=0` 和任务快照，并持续显示缺口提示；重同步不会把回放当作新事件通知。
4. 服务不可用或 401 时标记缓存过期、显示错误、禁用操作；恢复认证后继续同步。新的 CLI generation 不恢复旧待审批事项。
5. 仅 owned、当前连接及 generation 的新增 `waiting_approval / waiting_input / failed` 提醒。历史来源、observed、旧代次和迟到轮次完成均不提醒。
6. 通知按连接、代次、任务、轮次、提醒类型合并；同一轮次的相同等待类型只提醒一次，状态通知和审批请求不会各弹一次。不同轮次可重新提醒。
7. 页面保留最近 500 条事件，P1 服务最多保留 2000 条。超过范围的早期事件无法从此接口恢复；页面明确提示，不宣称完整永久历史。

代码检查确认原客户端只有 `U.toast`，没有原生通知入口。因此增加最小 Electron `Notification` 桥：限定三种提醒类型与固定文案，只接受主窗口主 frame、实际 `index.html` 来源，主进程再次去重。Windows AUMID 与现有包的 `com.vigil.client` 一致。通知点击显示主窗口并打开 Agent Center；通知不可用时保留页面内 toast。

活跃任务目标轮询间隔 2 秒，其余 5 秒；不并行启动重复轮询。Agent Center GET 超时为 5 秒，POST 为 45 秒。窗口隐藏后的实际定时精度仍受 Chromium 调度影响，未承诺后台常驻服务级实时性。

## 3. P1 最小兼容补充

没有新增第二套后端、没有改变 P1 的 ownership 或审批机制。

- `event_key / turn_key`：任务快照提供与脱敏事件 ID 对应的关联键。
- `current_operation`：只记录经过白名单筛选的操作类型、哈希 item 标识和开始时间；不暴露命令、参数、正文或凭据。操作结束、断线、历史恢复和终态后清空。
- 线程状态事件使用当前轮次关联键，以便状态事件与请求事件正确合并提醒。
- `tool_failures` 沿用 P1 计数口径，目前计入明确的 `commandExecution.status=failed`；没有将一次工具失败直接判为任务失败。

## 4. 实际验收结果

| 验证层 | 结果 | 范围 |
|---|---:|---|
| Python 自动回归 | 32 项通过 | P1/P2 Adapter、实际 Brain HTTP 鉴权链、真实 Codex App Server 对本地模型夹具 |
| Node 自动回归 | 14 项通过 | cursor、缺口、重启、通知去重、历史/observed/旧代次排除、原生通知 IPC 来源校验 |
| P2 真实 Electron | 18 场景通过 | 实际页面、生产 preload、生产 HTTP handler、真实本机 HTTP 服务与确定性事件 |
| 原聊天 UI Electron 回归 | 47 项通过 | 聊天、历史、多行输入、模型、人格、知识库入口、普通主题及本地 Wallpaper 夹具 |

P2 Electron 使用 **28.3.3**，`sandbox=true / contextIsolation=true / nodeIntegration=false`，无 preload/renderer 错误。三次原生提醒分别对应审批、失败、输入；三次均收到 Electron 的 `show` 回调，没有重复生成原生通知。

| 必需场景 | 实际验证 |
|---|---|
| 正常任务实时更新 | 自动轮询显示 running 和当前工具类型；结束后显示 completed |
| 等待审批只通知 | 展示待处理项；无答复按钮；没有 POST approvals 请求 |
| 断线重连 | 状态转 unknown，审批过期；重连保持历史来源，直到新任务事件 |
| 重复事件 | 同一审批重复 payload 和重复 IPC 均不再通知 |
| 历史会话 | observed 的历史 inProgress 仍显示 unknown，实际计算样式确认控制按钮不显示 |
| Electron 页面可打开 | 实际加载 index.html，通过导航打开、选择任务并查看事件 |

额外覆盖明确失败、阻塞输入、离开页面后的提醒、连接 ID/序列重置、HTTP 401 恢复、cursor 缓冲区溢出、1380×880/900×600 布局、创建/启动/中断。

截图检查发现公共 `.btn` 的 display 会覆盖 `hidden` 属性，已在 Agent Center 作用域修复，并增加计算样式断言。

## 5. 验证边界与剩余事项

- 本次 **没有线上模型 API 调用**。P2 页面测试中的 Codex 事件由隔离夹具生成；另行运行的三项真实 CLI 回归也只连接认证的 loopback 模型夹具。不是线上模型能力评测。
- 使用真实 Electron 渲染进程、生产页面和 preload；HTTP 请求函数/IPC handler 从当前 main.js 原样提取执行，通知模块直接复用生产代码。没有以生产配置启动整个主进程，不读取真实钥匙串、生产历史/记忆，也没有打包安装验收。
- P2 页面的 Wallpaper 截图验证材质与布局，未接真实 Steam 壁纸。原聊天回归另行运行了现有本机隔离 Wallpaper host；不能据此宣称所有真实壁纸均已验证。
- `show` 回调证明原生通知 API 展示流程成功，不证明桌面通知永远不会被系统勿扰策略抑制；其他操作系统未验证。
- 当前只展示最近开始且尚未结束的工具类型，不展示命令内容，也不提供完整执行输出；同一轮次的同类等待合并提醒，所有待处理事项仍列在页面。
- 缓冲区缺口能检测、提示并重同步可用记录，但不能恢复已淘汰事件。P2 不新增永久归档 API。
- 审批和补充输入答复仍禁用，等待任务可能阻塞；此阶段允许显式中断 owned 任务。

未实施 P3、自动批准、桌面接管、多 Agent、混合模式、Planner 或云端模型路由修改。未推送 GitHub。

## 6. 修改文件

| 文件 | 内容 |
|---|---|
| `client/src/js/agent-center.js` | 页面、任务选择、已存在的任务操作、后台监督订阅与提醒 |
| `client/src/js/agent-center-state.js` | 可独立测试的串行同步、cursor、缺口恢复与通知去重 |
| `client/src/agent-center.css` | 作用域样式、主题、材质、隐藏控制按钮与窄窗口适配 |
| `client/src/index.html` | 加载新页面资源 |
| `client/src/js/app.js` | 初始化监督、通知导航、页面订阅清理钩子 |
| `client/electron/agent-center-notifications.js` | 受限原生通知 IPC 与主进程去重 |
| `client/electron/main.js` / `preload.js` | 接入通知桥、AUMID、Agent Center 请求超时 |
| `brain/codex_adapter.py` | 最小元数据与事件关联兼容补充 |
| `brain/tests/test_codex_adapter.py` / `_http.py` | 元数据清理/脱敏、轮次关联与实际权限闸门回归 |
| `client/tools/test-agent-center-*.js` | 状态、通知与真实 Electron 自动验收 |
| `tools/agent_center_p2_fixture.py` | 仅测试用隔离 HTTP/事件夹具，控制经私有 stdin |
| `tools/verify_agent_center_p2.py` | 可复现验证入口与源文件 SHA256 记录 |
| 本报告与 `agent-center-codex-p2-evidence/` | 验收结果、截图、脱敏事件及回归记录 |

## 7. 复现与证据

从项目根目录执行（需现有 Python、Node、client Electron 依赖及 Codex CLI）：

```powershell
python tools/verify_agent_center_p2.py --include-chat
```

真实 CLI 回归在未安装 Codex 时会跳过，应检查回归日志，不能将跳过认作通过。

- [P2 Electron 结果](agent-center-codex-p2-evidence/electron-results.json)
- [脱敏事件轨迹](agent-center-codex-p2-evidence/events.json)
- [自动回归记录](agent-center-codex-p2-evidence/regression.txt)
- [原聊天 UI 回归](agent-center-codex-p2-evidence/chat-regression.json)
- [源文件校验](agent-center-codex-p2-evidence/source-manifest.json)
- [等待审批截图](agent-center-codex-p2-evidence/approval-light.png)
- [深色窄窗口截图](agent-center-codex-p2-evidence/page-dark-900.png)

完成后本地提交，停止等待 P2 审核。
