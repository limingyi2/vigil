# Vigil 开发交接报告（2026-10-14）

## 交接范围与核对口径

本次停止开发，只恢复获授权的云端服务、读取仓库和源码、生成本报告。未修改业务代码，未合并、删除分支，未修改 master。执行 `git fetch origin --prune` 更新远程引用；未执行 pull、merge、reset 或 push。GitHub 状态以本次 fetch 后的 origin 引用为准，功能基线以 `origin/master` / `v1.0.0` 为准；当前工作目录是较早的 feature 分支，不能用其文件直接代表发布版。

报告中的已有测试是此前开发结果，本次不重跑测试、不发送真实聊天、不创建 Codex 任务。本报告不包含凭据、聊天正文或私有配置内容。

# 项目当前状态

## GitHub 与本地状态

- 仓库：`https://github.com/limingyi2/vigil.git`。
- 当前目录：`F:/zhixia`；分支：`feature/agent-center-polish`；HEAD：`65527f79f61240e6c61251417723998118328061`。
- `origin/dev`：`e22ef577b27996c8049a82daedd6b0cf56d735f9`。
- `origin/master`：`ac1938e86eeed50313e6c0cff6ee408d0bc3c63e`。
- `v1.0.0` 指向：`ac1938e86eeed50313e6c0cff6ee408d0bc3c63e`，与当前 master 一致。
- dev 是 master 的祖先；`dev...master` 提交数为 0 / 1，master 多出的提交是 v1.0.0 普通合并提交。master 不包含后续所有 feature 修复。
- 最近完成的 Reasoning Summary / Agent Timeline：`407954345ad1fb585882660dde1c469afb74f7e4`，已推送独立分支，尚未合并。
- 不同 feature 无统一“最新 SHA”；以下表格列出每条远程分支的完整 HEAD。

### 远程分支关系

“已进”表示 Git 祖先关系成立，不表示该分支全部界面已部署到云端或安装包。

| 远程分支 | HEAD SHA | 已进 dev | 已进 master |
|---|---|---|---|
| `origin` | `ac1938e86eeed50313e6c0cff6ee408d0bc3c63e` | 否 | 是 |
| `origin/archive/zhixia-brain-v0` | `a187a9378c3e8acbf5cb9766b477a50117e02bdd` | 否 | 否 |
| `origin/codex/chat-attachments` | `4336aaa625a208bdeae649304ac15215335c4d30` | 是 | 是 |
| `origin/codex/fix-model-settings-cloud-switch` | `1076134a04d00bf58a8bf65f413f7581e534da94` | 否 | 否 |
| `origin/codex/fix-video-attachments` | `91b54e7441e229c29fbfccb639dd098fe1e21650` | 是 | 是 |
| `origin/codex/p3-windows-approval-e2e-20261013` | `4fd368e3d28d538a68408967ec57e67e7248e878` | 否 | 否 |
| `origin/codex/windows-startup-performance-20261013` | `b33514587419c1bcb25d865932450787207f6c36` | 是 | 是 |
| `origin/creator-mode-mvp-1` | `42c6f1a8acc8bcc8e2389dd0b35fcac366a11297` | 是 | 是 |
| `origin/creator-mode-mvp-2` | `d0f96c403c93eb25a61d52b5ddc0241926128e7a` | 是 | 是 |
| `origin/creator-mode-p4-2` | `96b114a457d0dce321539fd1b6ad7ac50fbf6c6f` | 是 | 是 |
| `origin/creator-mode-p4-3` | `0275152581abed62b5e936dc358b5398a59d4d70` | 是 | 是 |
| `origin/creator-mode-p4-4a` | `8f4268fecc96781a805f60dde5060009e34d63c4` | 是 | 是 |
| `origin/creator-mode-p4-4b-1` | `c28ea07dead361b7fb5e641473beca4494f48451` | 是 | 是 |
| `origin/creator-mode-p4-4b-2` | `fd7ca19213e221629af0b03deacd7c00451e301c` | 是 | 是 |
| `origin/creator-mode-p4-ui-finish` | `9c522a75e7b9f71c6d8311e3f1d3b2c50e0dc69b` | 否 | 否 |
| `origin/dev` | `e22ef577b27996c8049a82daedd6b0cf56d735f9` | 是 | 是 |
| `origin/feature/agent-center-polish` | `65527f79f61240e6c61251417723998118328061` | 是 | 是 |
| `origin/feature/agent-workspace-ui` | `b03cb551928ecfb5eaae2171b0757747abcd6404` | 是 | 是 |
| `origin/feature/chat-attachments-polish` | `f5ab6e4ac8aea5d1eb65fbcfa5cacda67cfa0cb2` | 是 | 是 |
| `origin/feature/chat-model-quick-switch` | `29345eb05bb8166919a4871221480244fed7afde` | 是 | 是 |
| `origin/feature/codex-reasoning-summary` | `407954345ad1fb585882660dde1c469afb74f7e4` | 否 | 否 |
| `origin/feature/docs-final-fix` | `c020bb80c6a09afc5daa93a935bf668c821b44bc` | 是 | 是 |
| `origin/feature/model-settings-polish` | `5abfdd71631ea216972a7a177697cb2e5d99f663` | 是 | 是 |
| `origin/feature/pre-master-cleanup` | `5f46825877857cc1962790069eb26ab61e953a5c` | 是 | 是 |
| `origin/feature/settings-model-wechat` | `27be131dba0f50579b54ce27596b989167a05f0d` | 是 | 是 |
| `origin/feature/settings-model-wechat-security` | `2248c2ce710d70b875d5c669e8bcbaa5321f82d6` | 是 | 是 |
| `origin/fix/mimo-structured-tools` | `3559c18f5d28e92787040a38fc023406b50dd1c0` | 是 | 是 |
| `origin/master` | `ac1938e86eeed50313e6c0cff6ee408d0bc3c63e` | 否 | 是 |
| `origin/p3-approval-freeze` | `db56c29718ab3af9fd5eb38703d81469087ff822` | 否 | 否 |
| `origin/ui/v2-chat-redesign` | `9044eb9bfb57e65e54702c567424c18afcb93343` | 是 | 是 |

### 尚未合并到 master 的远程分支

- `origin/archive/zhixia-brain-v0`：`a187a9378c3e8acbf5cb9766b477a50117e02bdd`；未进 dev/master。
- `origin/codex/fix-model-settings-cloud-switch`：`1076134a04d00bf58a8bf65f413f7581e534da94`；未进 dev/master。
- `origin/codex/p3-windows-approval-e2e-20261013`：`4fd368e3d28d538a68408967ec57e67e7248e878`；未进 dev/master。
- `origin/creator-mode-p4-ui-finish`：`9c522a75e7b9f71c6d8311e3f1d3b2c50e0dc69b`；未进 dev/master。
- `origin/feature/codex-reasoning-summary`：`407954345ad1fb585882660dde1c469afb74f7e4`；未进 dev/master。
- `origin/p3-approval-freeze`：`db56c29718ab3af9fd5eb38703d81469087ff822`；未进 dev/master。

上述列表按 Git 历史判定。归档分支和纯文档分支同样可能出现在列表中，不应未经审查全部合并。

### 工作区状态

| worktree | 分支 | HEAD SHA | 核对时状态 |
|---|---|---|---|
| `F:/zhixia` | `feature/agent-center-polish` | `65527f79f61240e6c61251417723998118328061` | 非 clean（12 项） |
| `F:/sync-checks/_baseline_42c6f1a` | `detached HEAD` | `42c6f1a8acc8bcc8e2389dd0b35fcac366a11297` | 非 clean（1 项） |
| `F:/sync-checks/chat-attachments` | `codex/chat-attachments` | `4336aaa625a208bdeae649304ac15215335c4d30` | clean |
| `F:/sync-checks/codex-reasoning-summary` | `feature/codex-reasoning-summary` | `407954345ad1fb585882660dde1c469afb74f7e4` | clean |
| `F:/sync-checks/creator-mode-p4-3` | `detached HEAD` | `0275152581abed62b5e936dc358b5398a59d4d70` | 非 clean（2 项） |
| `F:/sync-checks/creator-mode-p4-4b-2` | `feature/chat-model-quick-switch` | `29345eb05bb8166919a4871221480244fed7afde` | 非 clean（1 项） |
| `F:/sync-checks/p3-approval-freeze` | `detached HEAD` | `db56c29718ab3af9fd5eb38703d81469087ff822` | 非 clean（8 项） |
| `F:/sync-checks/p3-windows-approval-e2e-20261013` | `codex/p3-windows-approval-e2e-20261013` | `4fd368e3d28d538a68408967ec57e67e7248e878` | clean |
| `F:/sync-checks/video-attachments` | `codex/fix-video-attachments` | `91b54e7441e229c29fbfccb639dd098fe1e21650` | clean |
| `F:/sync-checks/vigil-dev` | `dev` | `e22ef577b27996c8049a82daedd6b0cf56d735f9` | clean |
| `F:/sync-checks/vigil-startup-performance-20261013` | `fix/mimo-structured-tools` | `3559c18f5d28e92787040a38fc023406b50dd1c0` | clean |
| `F:/vigil-ui-v2` | `ui/v2-chat-redesign` | `257a7495e9c04ee71bd0be1a0511e777e9f1a6ab` | clean |
| `F:/zhixia-v1.0.0-build` | `codex/fix-model-settings-cloud-switch` | `1076134a04d00bf58a8bf65f413f7581e534da94` | clean |

当前目录交接前已有未跟踪文件，未删除、暂存或提交：

```text
?? brain/vigil-brain.spec
?? cleanup_result.txt
?? deploy/check_cloud_brain.py
?? deploy/check_cloud_config.py
?? deploy/diag_wechat_fallback.py
?? deploy/fix_cloud_mimo.py
?? deploy/test_chat.py
?? deploy/test_cloud_api.py
?? deploy/test_cloud_apis.py
?? deploy/verify_cloud.py
?? find_bridge.txt
?? "wechat-bridge/\344\270\200\351\224\256\345\220\257\345\212\250\344\275\277\347\224\250\350\257\264\346\230\216.md"
```

本报告写入后，当前目录另增加未跟踪文件 `docs/VIGIL_HANDOFF_2026-10-14.md`。其他既有状态保留。`ui/v2-chat-redesign` 本地相对其同名 origin 分支 ahead 4；本地 `codex/p3-windows-approval-e2e-20261013` 的 upstream 指向 `origin/p3-approval-freeze`，虽然显示 ahead 3，其同名远程分支已存在且 HEAD 一致。交接时应分别核对本地 HEAD 和 upstream，避免把 upstream 差异误认为未推送。

## 已完成功能 / v1.0.0 内容

- Electron 桌面聊天、模型选择、本地 Brain sidecar、工具执行与权限确认。
- Model Settings：左下角独立设置窗口；模型名称、Provider、Base URL、Model ID、脱敏 API Key 状态；配置持久化，聊天直接选择已保存模型。
- Chat Attachments：图片、视频、PDF、TXT、常见代码；文件名、大小、上传状态；上传失败阻止后续聊天分析；视频关键帧方案；保留文件与权限校验。
- Agent Center：Codex / DeepSeek Harness / Work Buddy 卡片总览；进入现有 Codex 任务、事件、审批、安全详情；未接入卡片不伪造在线。
- Codex App Server stdio 接入、owned/observed 边界、事件游标与原生审批保护；P1/P2/P3 相关基础已在 master。
- 聊天快速模型切换、Creator/plugin/skill 工具基础、知识库和记忆后端、本地工具安全控制等已有代码也位于发布历史中；并非所有能力都达到完整产品验收。
- pre-master 清理与 Windows 启动文档更新已进入 dev/master。历史版本、旧 feature 工作区和云端部署不因此自动更新。

## 未合并功能 / 修复

- `feature/codex-reasoning-summary`：思考摘要保存、折叠展示及 Agent Timeline、状态流转、工具开始/完成；未进入 v1.0.0。
- `codex/fix-model-settings-cloud-switch`：设置云端/本机切换与页面异步返回、Agent 详情返回总览、数据页超时/错误显示、旧管理页重复 Agent 列表入口、本地工具动态端口轮询修复；未进入 dev/master。此前已更新本机安装客户端的 app.asar，运行版本与 master 存在差异。
- P3 后续审批能力探测和 Creator 收尾文档等未合并项详见远程分支表；须先审查内容与依赖，不自动归并。

# 当前架构

## Electron

`client/electron/main.js` 管理窗口、配置、凭据、HTTP/流式代理与本地工具轮询；`preload.js` 暴露受控 IPC；`client/src/js` 实现页面和状态。渲染层不直接持有服务 token。sidecar 输出就绪标记及实际动态端口，普通请求与工具轮询必须使用一致地址。

安装客户端位于 `F:/vigil`，用户配置在 Windows Roaming 的 `vigil-client` 目录。安装包、源码、sidecar、云端代码四者可能不同步，不能只凭版本名称判断实际运行代码。

## Brain / HTTP API（FastAPI 名称纠正）

当前 `brain/server.py` 实际使用 Python 标准库 `http.server`；不是 FastAPI/uvicorn 服务。旧脚本的“FastAPI”字样是旧说明，不能据此使用 uvicorn 启动。`brain/brain.py` 保留现有聊天和工具调用核心，`runtime.py` 管理配置，`server_launch.py` 是本地开发与打包 sidecar 入口，负责插件、Skill Host、选端口和 `/api/health` 就绪检测。

普通聊天由 `/api/chat/stream` 发送 SSE think/text/done/error，经 Electron IPC 渲染。Codex Agent 事件与普通 Chat SSE 是两条独立链路；普通聊天的 reasoning 内容不等同于 Codex reasoning summary。

## Agent Center / Codex App Server

`brain/codex_adapter.py` / `codex_process.py` 启动 `codex app-server --listen stdio://`，使用 JSON-RPC。`/api/agent-center/*` 提供状态、任务、事件、审批等接口。Electron 详情页按事件游标轮询，活跃约 2 秒、其他约 5 秒，并通过 connection / generation / thread / turn / request / item 身份约束阻止过期响应。

owned 任务由本适配器创建/管理，可接实时事件与受控操作；observed 外部桌面会话只读历史，不能当作在线实时任务。断连、重启或失效后显示 unknown/history，不伪造运行状态。

## 模型管理

`model_hub.py` 被 server 导入，通过 `/api/models` 等已有模型接口读取、保存和切换配置；客户端模型设置与聊天选择器复用已保存配置。云端与本机各有自己的配置，并非切换页面后会自动互相复制。模型列表显示脱敏 Key，真实凭据仅留在受控配置/进程路径。

## 附件系统

`brain/attachments.py` 与客户端上传逻辑实现分片、校验、记录及聊天引用。当前发布代码限制单文件 256 MiB、单片 2 MiB；UI 显示上传状态，失败不会进入聊天流程。模型能力决定图片理解；视频不默认宣称原生理解，可经确认用已有 FFmpeg 提取最多 3 帧。PDF 文本提取依赖 pypdf，默认摘录前 3 页且有文本长度上限；失败保留元数据。不是完整视频理解、OCR 或长文档 RAG 系统。

# 最近完成

| 项目 | 状态与范围 |
|---|---|
| Model Settings | polish 分支已进入 v1.0.0；后续切换/异步页面修复位于独立 codex 分支 |
| Chat Attachments | polish 与视频上传/抽帧失败保留修复已进入 master；大文件、类型和权限控制保留 |
| Agent Center | 卡片总览与 Codex 详情、安全审批已发布；导航返回与重复入口修复未合并 |
| Reasoning Summary | a319d137b6c610db7e5d2e44f7bcda1f4eb70254 起实现；只保留 summaryTextDelta / summaryPartAdded，拒绝完整 reasoning chain；最新轮摘要写 owned.json |
| Agent Timeline | 407954345ad1fb585882660dde1c469afb74f7e4：摘要折叠项、真实状态变化、tool start/completed、结果与耗时；未改任务创建和审批逻辑 |

摘要与 Timeline 先前验证：51 项 Python（含 4 项实际 CLI + 本地模型 fixture）、27 项 Node 前端、21 项 Electron 检查通过。测试使用隔离配置和本地模型，不等于真实云模型、外部桌面会话或真实审批场景均已验收。摘要有 65,536 字符、16 个 reasoning item、32 个 part 索引上限；模型可能不提供摘要。

# 已知问题

1. **外部 Codex 会话不能实时观察**：独立 App Server 的事件流不覆盖其他桌面实例；observed 只读历史，不能直接控制或审批。
2. **Agent Center 限制**：只有 Codex 有真实 Adapter；状态为轮询显示而非前端长连接；事件窗口有界，摘要快照可补摘要文本但不能恢复所有工具事件。真实原生审批完整闭环尚需专项验收，不能用 fixture 测试替代。
3. **RAG / Memory 的准确状态**：不是完全未实现。master 已有 `brain/kb/*`、`brain/plugins/kb.py` 的检索、索引与注入，以及 `memory_store_v2.py` 的记忆、向量检索与降级。记忆 UI 仍主要为统计，完整查看、编辑、生命周期和统一 Agent 记忆方案尚未完成；本次未验收生产检索质量。
4. **多 Agent 未真实接入**：DeepSeek Harness、Work Buddy 为占位，没有新增 Adapter 和编排。
5. **运行版本漂移**：本机安装客户端含未合并修复；云端 server/model_hub 与 v1.0.0 哈希不同，云端未见 codex_adapter.py。GitHub push 不代表自动部署。
6. **旧工作区文档漂移**：当前 feature/agent-center-polish 的 start_dev.bat 仍有固定路径与 npm run dev；master/dev 已是相对路径 + server_launch.py + npm start。不要从旧 feature 工作区复制启动脚本。
7. **安全清理不覆盖历史**：pre-master 清理已合并，但旧未跟踪诊断脚本、历史提交和部署备份可能仍含明文凭据。本次不输出、删除或轮换；公开仓库前必须另行审计与轮换。
8. **连接不等于工具执行成功**：远程状态接口 HTTP 200 仅确认 API 可达，尚未验证完整云端聊天 → 客户端认领 → 执行 → 回传闭环。

## 本次云端恢复记录

- 云端原状：SSH 正常，8788 无监听，无 Python Brain；Node 本机 8790 存活。
- VigilBrain 2026-10-11 01:50 启动、2026-10-14 01:50 日志停止；VigilAgent 2026-10-10 02:09 启动、2026-10-13 02:09 停止。两任务均 PT72H，强烈指向计划任务时限终止。
- 按本次授权保留现有任务动作/触发器，将两任务 ExecutionTimeLimit 改 PT0S；失败恢复间隔 PT1M，RestartCount=3；启动两个任务。
- 修改前任务 XML 备份：云端 `C:/linzhixia/_backups/handoff-20261014/VigilBrain.xml`、`VigilAgent.xml`。未部署源码、未改模型/凭据、未修改审批。
- 验证两任务 Running；8788 在 0.0.0.0 监听，Brain PID 1524；从本机访问云端 `/api/health` HTTP 200、ok=true、frozen=false；`/api/models` HTTP 200；`/api/plugin/remote/stats` HTTP 200。
- 正确 health 路径是 `/api/health`，不是 `/health`。远程 stats 的 ok 字段是成功调用计数，本次为 0，不应直接解读为 API 请求失败；active/pending 也为 0，未证明电脑执行端已注册并消费云端调用。
- Windows 的失败重试只覆盖任务被认定失败，不覆盖服务卡死、正常退出或超过 3 次重试的持续故障；保留现有登录/开机触发器，不承诺立即新增服务守护。

# 下一阶段建议（按优先级，不在本次实施）

1. **P0：稳定运行和交接验收**。持续观察云端任务与 health，核对云端/本机 Brain/客户端地址、版本与端口；用安全只读工具验收远程完整闭环；制定超出 3 次重试、卡死和正常退出的恢复策略。
2. **P0：统一待合并修复与发布版本**。先审查导航/设置/工具轮询修复，再审查 Summary/Timeline 分支；在独立验收环境确认依赖与冲突，明确云端部署范围，再决定 dev/master 合并与打包。此次不合并。
3. **P0：发布前安全检查**。审计当前及历史凭据、未跟踪部署脚本、备份与公开边界；凭据脱离仓库，轮换可能泄露的凭据。不要将生产配置加入交接包。
4. **P1：Codex 审批和断连专项验收**。实际请求/响应、过期、generation 切换、重启恢复、summary 缺失、事件窗口丢失、工具失败；维持 owned/observed 边界。
5. **P1：外部 Codex 观察可行性调查**。核对官方协议和实例边界，先定义只读来源和真实状态契约；不能靠扫描历史伪造实时状态。
6. **P1：附件与模型生产场景验收**。真实图片、小视频、FFmpeg 缺失、PDF/代码文本、256 MiB 边界、模型切换重启持久化，核对能力提示与权限确认。
7. **P2：完善已有 RAG/Memory**。先检查现有后端配置、检索质量、权限分区与回归，再补记忆查看/编辑、来源和清理；避免重复实现已有存储。
8. **P2：多 Agent 接入**。定义 Adapter 的状态、任务、事件、身份与权限接口；有真实连接后替换占位。编排另立范围，不默认增加。

## 开发接手入口

以 master/dev 的 `docs/WINDOWS_DEV_HANDOFF.md` 为新电脑入口：安装 Git/Python/Node，执行 `scripts/bootstrap-windows.ps1`（先 CheckOnly），创建隔离 `.local-dev`，设置 VIGIL_HOME 与 .venv PATH，运行 `scripts/start_dev.bat`。该脚本从仓库相对路径启动 `brain/server_launch.py` 与客户端 `npm start`。不要使用旧固定盘符或不存在的 npm run dev。

相关源码：`client/electron/main.js`、`client/src/js/pages.js`、`client/src/js/agent-center.js`、`brain/server.py`、`brain/server_launch.py`、`brain/codex_adapter.py`、`brain/model_hub.py`、`brain/attachments.py`。Timeline 新增的 `client/src/js/agent-timeline.js` 仅在 reasoning feature 分支查看。

本报告只保存于当前 feature 工作区，不提交、不推送；后续由负责人决定文档提交位置，不应为了交接修改 master。
