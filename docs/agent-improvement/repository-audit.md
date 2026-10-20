# Vigil 全仓能力审计（质量冲刺阶段记录）

日期：2026-10-20（Asia/Shanghai）
审计分支：`codex/vigil-agent-improvement-foundation`
范围：当前 worktree 的 Electron 客户端、Python Brain、插件/技能、工具和权限、会话/记忆、测试与发行配置。审计未连接真实模型、外部服务或用户数据。

## 判读

- **有测试**：存在可运行的本地自动化契约；不代表生产模型/GUI 体验验收。
- **部分覆盖**：有组件或模拟测试，端到端交互仍需验证。
- **待验证**：当前环境或当前测试没有给出证据。
- **环境阻塞**：执行前置条件不存在，原始错误及范围记录在最终验收文档。

## 模块与依赖关系

```text
Electron main / renderer
  ├─ IPC、窗口和安全边界 ──> 本地工具执行、工作区/模型/插件界面
  ├─ HTTP/SSE 客户端 ──────> Python Brain（本地 sidecar）
  └─ 安装/更新事务 ────────> 配对的 Brain 发行目录与签名/哈希清单

Python Brain
  ├─ Provider 适配器 ──────> 模型 API（外部）
  ├─ Agent 执行器 ─────────> tools / plugin_host / delegation
  ├─ 会话、历史、记忆 ─────> 本地运行数据目录
  └─ remote plugin ────────> Electron 本地工具队列与权限确认

插件/技能/Creator
  ├─ Python 插件宿主与权限登记
  ├─ Node skill host
  └─ Creator 生成、审核、安装和前端面板
```

## 能力审计矩阵

| 模块 | 当前判断 | 证据 / 约束 |
|---|---|---|
| Electron 启动、窗口生命周期、IPC | 部分覆盖；GUI 环境待验证 | Node 纯组件测试运行；Electron 用例因缺少 `electron` 依赖而无法加载 |
| Brain 启动、服务状态、请求路由 | 有自动化覆盖 | `run-mode-http`、Agent lifecycle、全仓 pytest；生产 sidecar 启动未验 |
| Provider 设置、流式响应、超时/错误分类 | 有 Fake Provider/契约覆盖；真实接口待验证 | provider-contract、reasoning event 自动化；无受控模型凭据 |
| Agent 规划、多步骤工具调用、完成/失败状态 | 有自动化覆盖；实际模型策略待验证 | 生命周期及计划可观测性通过；真实模型任务不运行 |
| 大型工具输出和文件读取 | 已改进并通过隔离回归 | 超限工具结果形成 65,536 字符内有界 head/tail 预览；文件 offset/length 分块、UTF‑8 边界由临时文件测试验证 |
| 思考、正文、工具事件、快照、断线重连 | 组件/Brain 契约部分覆盖 | timeline、runtime trace、reasoning tests 通过；真实 Electron 断线和事件压力待验证 |
| Agent 状态、取消、长任务 | 有本地自动化覆盖 | Agent lifecycle 与 cancel contract；没有 GUI 长时任务验收 |
| 上下文、历史、会话隔离与恢复 | 部分覆盖 | context/history、conversation isolation 通过；跨 Electron/Brain 重启恢复待端到端验证 |
| 文件、工作区、命令和 Git 权限 | 有自动化安全覆盖 | local tool boundaries 与 tool permission suites 通过；真实用户允许目录行为待人工验证 |
| 插件安装、启停、卸载、Creator | Python 契约/事务及 Node 媒体组件覆盖 | plugin-contract 通过；Electron 市场 UI 和真实外部插件未运行 |
| 模型设置、插件市场、技能与 Creator 界面 | 部分覆盖 / GUI 待验证 | 无 Electron 运行时；renderer 纯组件测试覆盖部分状态和失败展示 |
| 错误提示、重试、停止和加载状态 | 部分覆盖 | 状态/超时/失败组件测试通过；真实模型网络不确定状态未注入 |
| Windows 安装、升级、卸载、用户数据保留 | 更新/哈希契约有覆盖；候选安装包未构建 | `test-unified-runtime.js` 验证双包配对、快照哈希和不包含用户 home；实际 installer 需 Node/Electron/PyInstaller 与配对构建产物 |

## 发现及本轮动作

1. **AG-001 — 大型工具结果终止运行（P1）**：在 65,536 字符边界以上，Agent 将工具结果分类为 `resource_limit` 并终止任务。新增失败用例先复现，再实现有界显式预览；没有调高上限，也没有把模型流式输出超限变成成功。相关修改在 `brain/agent_runs.py`，测试在 `tests/test_p2_agent_runs.py`。
2. **AG-004 — 大文件读取不可续读（P1）**：旧 `local_file_read` 只返回文件头 200KB，schema 没有 offset/length。增加有类型的可选参数、远程插件多参数透传、字节范围读取、严格上限、下一段提示和 UTF‑8 边界对齐。测试只访问临时目录，不使用用户文件。
3. **AG-002 — 事件短暂队列与断线恢复（P1，待验证）**：Brain 测试覆盖 cursor/snapshot 部分路径；Electron 实际断线重连与长时间有限队列压力仍未能验证。本轮没有更改事件实现。
4. **AG-003 / AG-005 — UI/Brain 状态一致性及不确定副作用重试（P1，待验证）**：取消和请求状态契约测试通过，但没有在真实 Provider 和 Electron 组合环境检验；本轮没有更改此链路。
5. **发行包验收阻塞**：`client/node_modules`、Electron 运行时、npm 命令及 `client/build-info.json` 均缺失；`scripts/build_release.py --verify` 因缺少生成的配对身份文件停止。未执行构建，以免制造未经完整验证的安装包。

## 质量门与反向检查

- Python 全仓：1,384 passed、19 skipped、0 failed；跳过原因保留在本地 `-rs` 报告。
- Agent evaluator regression：12 个案例 PASS（240 passed、3 skipped）；security：1 案例 PASS（29 passed）。
- 八个无 Electron 依赖的 Node 测试文件：56 passed。完整 `client/tools/test-*.js` 启动时有 13 个文件因 `electron`、`ws` 或 `ajv/dist/2020` 未安装而失败初始化；此项按环境阻塞，不表示测试断言失败或产品通过。
- 检查执行器的 pytest argv 白名单、cwd 仓库边界、报告/状态目录仓库外限制；测试与试运行命令没有源代码写入命令、shell 字符串、git commit 或网络操作。
- 核对 `client/package.json` 的 Electron Builder `files` 白名单、Brain `extraResources` 路径和 `.gitignore` 中构建/运行数据忽略项；构建规则静态可见，未实际生成包验证。
- 本轮更改限制在 Agent 工具结果治理、远程读取参数契约、文件读取实现和测试/记录；没有替换 Electron + Python Brain 架构，没有清理历史失败证据或本机数据。

## 优先级建议

| 优先级 | ID | 下一步 |
|---|---|---|
| P1 | AG-002 | 在有 Electron 依赖的隔离测试机执行 SSE 断开/重连、cursor gap、重启快照及事件压力验收 |
| P1 | AG-003 | 联合 UI 和 Brain 状态进行完成、失败、取消及断线后的状态矩阵验收 |
| P1 | AG-005 | Fake Provider 故障注入与幂等工具 fixture；验证不确定请求状态下不会重复副作用 |
| P1 | AG-008 | 在干净候选构建环境验证 installer 文件清单、升级/回滚和用户数据保留 |
| P2 | AG-006 | 使用可控 fake provider 进行长上下文、压缩、工具结果预算和多文件检索验收 |
| P2 | AG-007 | 扩展插件权限隔离、越权参数、路径穿越、卸载清理审计 |

以上待办均没有在本轮修复。详细报告文件名、命令及状态见 `final-acceptance.md` 和本机评测报告目录。
