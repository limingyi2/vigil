# Agent Workspace UI 交接

分支：`feature/agent-workspace-ui`

## 本轮完成

- Agent Center 首页改为 Agent 工作室总览：Codex 实时卡片、DeepSeek Harness 和 Work Buddy 待接入卡片、最近任务摘要、添加 Agent 入口。
- Codex 详情按需打开；完整任务、observed 历史、事件和原生审批只在详情视图加载。
- Codex 详情继续复用原有 Agent Center 状态机和审批身份字段：`connection`、`generation`、`thread`、`turn`、`request`、`item`，并保留 `response_enabled`、stale、owned/observed 限制。
- Brain 缺少 Agent Center 路由时显示“当前 Brain 版本不支持 Agent Center，请检查版本”；Token/权限、App Server 不可用和过期状态分别显示。
- 未接入 Agent 只显示“待接入”，没有在线、任务、审批或控制能力。

## 验证

- Agent Center state：12 项通过。
- 桌面通知：3 项通过。
- Windows Electron 隔离检查：通过，Electron 28.3.3。
- Electron 检查包含：3 张 Agent 卡片、1 个真实 Codex、2 个待接入卡片、详情打开、owned/observed、断连重连、审批安全条件、主题和壁纸兼容。
- 证据：`docs/agent-center-codex-p2-evidence/electron-results.json`、`workspace-overview-light.png` 及同目录截图。

## 尚未接入

仓库当前没有可信的 DeepSeek Harness 或 Work Buddy Agent Adapter，因此本轮不伪造其状态。后续接入应提供状态、任务、事件、身份和权限契约后，再替换待接入卡片。

## 2026-10-15 Windows 自动检测、登录与受管连接

- 两个返回按钮共用的 `closeDetail()` 显式恢复总览可见性；连续进入详情、返回和轮询后，三张卡片仍可见。
- Electron 主窗口启动监督时，首次成功读取 Brain 状态后，若 Codex 已断开，则通过已有管理员 POST 接口自动尝试连接一次。Brain 暂时不可达时继续等待状态轮询；已连接或连接中的会话不重复连接。
- `brain/codex_runtime.py` 检查 PATH 中的原生 CLI，并在 Windows 回退查找 Codex 本机安装目录与 npm 原生包（包含 PATH 中 npm shim 对应的自定义安装目录）。不执行 shell shim，不扫描或读取凭据文件。
- 使用官方 `codex --version` 检查版本，使用 `codex login status` 的退出码确认本地认证状态；两个命令各有 6 秒上限。登录状态命令的 stdout/stderr 全部丢弃，不解析账号、邮箱或密钥。状态接口只增加安装状态、版本、认证状态和固定错误分类；本地登录存在不代表远端凭据有效性或模型权限已经验证。
- 未安装、版本检测失败、未登录、检测超时、握手失败和重连耗尽分别显示明确原因。总览与详情提供官方安装/登录说明、终端命令和“重新检测并连接”入口。
- “通过官方 CLI 登录”只有用户主动点击时才调用受管理员和回环鉴权保护的 `POST /api/agent-center/login`，由 `codex login` 自己打开官方浏览器认证流程。Vigil 不接收或复制凭据，不转存 OAuth 链接或登录原始输出；登录进程最多等待 5 分钟。成功后重新检查官方状态并连接；失败/超时明确提示；详情中的“断开”可取消等待，关闭 Brain 会清理自己启动的登录进程。
- 自动连接失败会展示错误，后续状态轮询继续运行；不会因每次轮询重复发起连接。用户手动断开后，本次客户端运行期间不会被轮询重新连上；下次客户端重新启动会重新检查连接。
- 已建立连接的传输断线后，按 1/2/4 秒退避最多重连 3 次；耗尽后等待手动操作。缺失 CLI 或未登录时停止重试。重连请求绑定取消代次，旧请求不会覆盖手动断开或新的登录流程。
- 连接仅启动/连接 Vigil 自己管理的 Codex App Server，不启动模型任务；已有的传输断线恢复仍由 Adapter 负责。浏览器 ChatGPT 不是本机连接通道。
- 针对性验证：Node 状态与通知 18 项；Python 自动连接、Adapter、真实 Brain HTTP 与原生 CLI 回归 50 项；真实 Electron 28.3.3 隔离回归 24 场景，包含两个返回按钮重复导航、启动自动连接、缺失/未登录/耗尽原因、官方链接、主动登录路由、手动断开、历史/通知/审批隔离及任务创建中断。JS 语法与 `git diff --check` 均检查。
- 本机真实 CLI 实测版本 `0.162.0-alpha.17.2`，官方状态为已登录；独立临时受管 App Server 握手成功，受管任务数量为 0。未注销或修改现有账号，新的浏览器登录流程用进程与回环夹具覆盖，未重新完成真人登录。
- Electron 使用隔离 Brain 和确定性 Codex 事件；原生 CLI 协议回归只使用独立 `CODEX_HOME` 和合成的本地模型夹具凭据，无线上模型调用。本轮未重新验证真实 Codex 人工批准/拒绝闭环。运行产物保存在被忽略的 `.local-dev/agent-center-regression/`，可用 `VIGIL_TEST_EVIDENCE_DIR` 指定其他证据目录。

官方依据：[CLI 登录状态与退出码](https://learn.chatgpt.com/docs/developer-commands?surface=cli)、[官方登录流程](https://learn.chatgpt.com/docs/auth)、[App Server](https://learn.chatgpt.com/docs/app-server)。
