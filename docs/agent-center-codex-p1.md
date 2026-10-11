# Vigil Agent Center — P1-A / P1-B 验收报告

日期：2026-10-11
实施基线：`f5f1359`，本地 `master`。范围止于 Adapter、状态监督、审批归属与去重；没有实施 P2/P3。

## 1. 本阶段结果

已在现有 Brain 中加入最小 Codex Adapter，通过现有 HTTP 路由及鉴权链提供本地管理接口。当前工程使用 `http.server.ThreadingHTTPServer`，没有 FastAPI 应用实例；本阶段复用实际架构，未引入第二套服务框架。

**监督与审批隔离的 P1 检查通过。** 27 项自动回归通过；真实 Codex App Server 上的两个 `gpt-6.1-sol` turn 均正常结束为 `completed`。两项真实任务各有一次命令工具失败，因此它们证明的是运行、事件记录与正常结束能力，**不证明文件读取任务的成果正确，也不代表工具全部执行成功**。这一限制保留在证据与后续问题中。

审批响应完全禁用；没有自动批准或拒绝请求。没有桌面模拟点击，没有推送 GitHub。

## 2. 实现内容

### P1-A：Codex 状态监督

- Codex 进程按需启动，使用 stdio 和原生登录机制；启动前不需要云端。
- 支持创建独立 workspace 和原生 thread，并启动 turn。只登记 Adapter 自己创建的 thread。
- 记录轮次、运行、等待审批、完成、失败、中断、最近活动时间及工具失败数。工具失败不直接覆盖 turn 的最终结论。
- 状态来源区分 `realtime` 与 `history`。`live` 表示状态证据来源，不表示任务一定仍在执行；运行结论以 `status` 为准。
- 进程退出、输入输出断开或协议解析异常后显示 `unknown / live=false`；旧连接代次的事件被丢弃。
- 自动重连最多尝试三次，退避为 1 / 2 / 4 秒。仅恢复持久登记的 owned thread，不自动重放中断前的任务输入。
- 重新连接后恢复到历史状态 `unknown / history / live=false`，直到新的原生状态事件出现。设置变更通知不会被冒充为实时执行证据。
- 结构化事件按连接代次与轮次去重；合法的相同文本 delta 不做盲目去重。旧轮次迟到的完成事件不能结束新轮次。
- 日志采用字段白名单、ID 哈希和有界队列；轮询以 cursor 与 connection_id 对账，缺口通过 `gap` 标识。
- Windows 使用独立 Job Object 管理自己启动的进程树及退出清理，不枚举或终止其他 Desktop 进程。机制依据 [Microsoft Job Objects 文档](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)，本机原生回归验证通过。

### P1-B：审批事件归属与去重

审批唯一键是以下信息的 SHA-256：

```text
connection_id + connection_generation + thread_id + turn_id
+ request_id 类型 + request_id 值
```

另保存 item_id、原生 method、到达时间、有效期和请求状态。字符串 `"0"` 与整数 `0` 不混为一项。

- 只有已登记 owned thread、当前连接代次、当前有效 turn 和完整 item/request 绑定的事件可进入待办表。
- 重复事件以及同一身份但内容变化的重复通知，都不会产生第二项待办。
- Desktop 只读记录标为 `observed / unknown / history / live=false`。没有“把观察项改成 owned”的接口，也不会因看到事件而获得控制权。
- `turn/completed`、中断、连接断开、`serverRequest/resolved` 或超时会使待办失效或解决。
- 待办不写入 owned 登记文件；Adapter 重启后不还原旧审批。原生历史读回也不生成审批事项。
- 即使重新连接后原生服务再次投递旧 turn 的审批，也因该 turn 不属于当前连接代次的显式启动而拒绝入队。
- 所有审批 POST 返回 `409 approval_response_disabled`。内部 `respond()` 同样禁用；协议发送层拒绝 `result/error` 形态的服务端请求答复，accept 和 decline 都不能发送。

目前没有审批按钮、自动决策或交互式问题答复流程。阻塞式提问仅能作为等待事件记录，属于监督范围。

## 3. 文件清单

| 文件 | 主要内容 |
|---|---|
| [`brain/codex_adapter.py`](../brain/codex_adapter.py) | stdio Adapter、owned 登记、状态映射、事件队列、重连、审批身份和去重、本地接口分发 |
| [`brain/codex_process.py`](../brain/codex_process.py) | Windows 原生 Job Object 生命周期；只处理本 Adapter 的进程树 |
| [`brain/paths.py`](../brain/paths.py) | 新增 `AGENT_CENTER_DIR` 路径常量 |
| [`brain/server.py`](../brain/server.py) | 在既有鉴权与设备权限闸门之后挂接 GET / POST 路由；其余业务路由保持原状 |
| [`brain/tests/test_codex_adapter.py`](../brain/tests/test_codex_adapter.py) | 20 项状态、归属、去重、过期、权限和迟到事件回归 |
| [`brain/tests/test_codex_adapter_http.py`](../brain/tests/test_codex_adapter_http.py) | 4 项实际 HTTP handler 路由与鉴权验证 |
| [`brain/tests/test_codex_adapter_native.py`](../brain/tests/test_codex_adapter_native.py) | 3 项真实 CLI + 本机模型夹具的生命周期和审批检查 |
| [`tools/verify_agent_center_p1.py`](../tools/verify_agent_center_p1.py) | 有界验收脚本；默认本机夹具，显式 `--real` 才调用真实模型 |
| [`agent-center-codex-p1-evidence/`](agent-center-codex-p1-evidence/) | 脱敏轨迹、真实 / 夹具摘要、回归输出、源码哈希 |

已验收的 P0 报告和协议材料一并归档，方便审查 P0 → P1 的依据。其他既有未跟踪文件未纳入提交。

## 4. 接口与数据位置

所有 Agent Center 路由都要求：通过既有鉴权、管理员身份、连接来自 loopback。受限设备仍受原允许矩阵约束；没有修改其已有权限。远程调用即使带管理员身份也不能通过此模块的本地闸门。

| 方法 | 路径（前缀 `/api/agent-center/`） | 内容 |
|---|---|---|
| GET | `status` | 连接、代次、模型目录、错误及答复禁用状态 |
| GET | `tasks` / `tasks/<task_id>` | owned / observed 快照、轮次与状态来源 |
| GET | `events?after=<cursor>` | 脱敏事件增量、cursor、gap |
| GET | `approvals` | 待办和已失效记录，response_enabled=false |
| GET | `observed` | 原生历史列表，最多 30 条，返回 next_cursor |
| GET | `observed?thread_id=<id>` | 历史元数据与轮次状态；不返回消息正文，不订阅或接管 |
| POST | `connect` / `disconnect` / `reconnect` | 管理本 Adapter 子进程与连接 |
| POST | `tasks` | 创建 owned thread；可选择已公布模型 |
| POST | `tasks/<task_id>/turns` | body `{ "text": "..." }`；启动 owned turn |
| POST | `tasks/<task_id>/interrupt` | 只中断本登记表的当前 owned turn |
| POST | `approvals/...` | 始终 409，审批答复禁用 |

`tasks` 创建返回的 task_id 是 Vigil 登记 ID，不等于原生 thread_id；不能拿一个 Desktop thread_id 冒充它调用控制接口。

运行数据在 Brain 的 `data/agent_center/`：owned.json、脱敏事件日志和 workspaces。审批待办只在内存中存在。Codex 原生认证由 CLI 自己读取；不复制认证文件，不把凭据传给 HTTP 页面。

沙箱固定为 read-only、网络禁用、on-request 和 reviewer=user，且校验原生 thread/start / resume 的返回权限。每个 turn 再明确传入相同策略。继承的 MCP / 插件 / Apps 只在本 Adapter 的新线程配置中禁用，不修改生产配置或既有 Brain 工具权限。

## 5. 验收证据

### 5.1 六种必验场景

| 场景 | 实际验证方式 | 结果 |
|---|---|---|
| 正常完成 | 两个真实模型 turn；本机夹具 turn | PASS，原生 completed 被记录 |
| 运行中 | 原生 turn/started 与 running 轨迹 | PASS |
| 等待审批 | 原生 App Server + 本机夹具触发命令审批 | PASS，waiting_approval，未答复 |
| 错误 | 本机模型端点 HTTP 400，原生 error / failed | PASS，任务失败和工具失败分别记录 |
| 断线重连 | 杀掉本测试 App Server；观察 unknown；自动连接新代次并 resume owned | PASS，旧待办 expired；不重放旧任务 |
| 重复审批 | 基于原生审批身份重复投递 3 次；内容变化 / request 类型回归 | PASS，仍只有一项待办 |

正常命令在测试 home 中可能要求原生审批。额外回归验证它进入等待时不继续执行，随后只发送 owned interrupt，不答复请求。

### 5.2 自动回归

```powershell
$env:PYTHONPATH = 'F:\Vigil\brain'
python -m unittest discover -s brain/tests -p 'test_codex_adapter*.py' -v
```

结果：**27/27，通过，无跳过**。其中原生 CLI 检查不调用真实模型；没有 Codex CLI 的环境才会显式 skip，不能将 skip 算成原生通过。

HTTP 检查从当前 `server.py` 的 AST 提取实际 handler 与鉴权函数，在临时目录和真实 loopback HTTP server 上执行。它验证实际新增路由，没有加载生产 runtime、Brain 记忆或模型模块；不等同于整个 Electron / 打包 sidecar 的端到端启动验证。

首次原生回归暴露了 Windows 退出清理和恢复时设置通知影响来源标签的问题；已修复并复跑。最终版本的回归输出和源文件哈希归档于证据目录。

### 5.3 两项真实实验

CLI：`0.162.0-alpha.17.2`；模型：`gpt-6.1-sol`，effort=low。均为新的临时 workspace 与独立 thread，不读取 Vigil 人格、历史、知识库或真实项目。

| 实验 | 原生状态 | 耗时 | 工具失败数 | fixture 变更 |
|---|---|---:|---:|---|
| real_task_1 | completed | 11.157 秒 | 1 | 无 |
| real_task_2 | completed | 13.969 秒 | 1 | 无 |

共 2 次真实 turn/start，4 条原生单次用量记录：input 53,675；cached input 45,952；output 244；total 53,919。缓存输入包含在 input 内，没有重复累加累计值。HTTP 请求重试数和实际账单费用没有单独审计。

每项设置 90 秒观察熔断及最多 4 个工具活动的观察限额；出现审批或提问等待就中断，绝不答复。本轮没有触发熔断。这个工具上限是观察后中断机制，不是执行器调用之前的硬配额。

工具失败均为 exitCode=1。当前材料不保留私人正文或完整工具输出，因此失败原因和任务答案正确性未纳入本轮结论。**不会将这些数据描述为“两个文件任务成果已验证成功”。**

## 6. 保持不变与未验证范围

- 混合模式、Planner、委派路由、云端模型路由和既有工具权限未修改。
- 未新增桌面接管、审批执行、插件创作、Agent Center UI 或任务编排功能，未进入 P2/P3。
- 没有验证已有 Codex Desktop 会话的实时状态；只展示 `observed / unknown` 的历史元数据。
- 实测平台为本机 Windows。其他操作系统的退出清理和打包兼容性未验证。
- 新建但还没有 rollout 的 thread 在原生重启后可能无法 resume；保留 unknown 和 resume_failed，不自动新造 thread 冒充恢复成功。
- 默认观察列表目前只提供首批 30 条及下一页游标，没有完整分页导航。
- 只读沙箱限制写入与网络，不承诺任意 shell 只能读取 workspace。工作目录不是完整的读权限隔离机制。
- real 测试复用了原生登录 home，Codex 可能留下这些测试 thread 的正常运行记录；Vigil 生产历史、人格和记忆没有被加载或改写。
- owned 登记文件属于本机可信应用数据，不是抵抗同账户恶意修改文件的安全边界。跨进程接管和跨设备审批仍未开放。
- 审批答复长期禁用时，等待的任务需要显式中断。本阶段不自动拒绝，也不自动发送任何批准答案。

## 7. 复查与审核停止点

本机确定性验收：

```powershell
python tools/verify_agent_center_p1.py --output <新的临时证据目录>
```

真实实验需要显式 `--real`，会产生两个真实 turn；本阶段已执行完毕，不安排自动重复。轨迹仅保存监督元数据，未保存完整消息和命令输出。

本阶段完成后只做本地提交，提交号见本次交付消息。审核前停止在 P1-A/P1-B，等待下一阶段授权。
