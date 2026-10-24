# P20.2 能力发现、用户审批与实际执行 —— 交接文档

- 分支:`codex/p20-2-capability-execution`(基于 `origin/dev` = `3d5a7c6`)
- 本地提交:`7a94f11`(fix(capability): one-shot approval grants and plugin/skill operations)
- 状态:**进行中,未完成**。服务端能力注册与审批语义已落地并验证;Agent Run 审批闭环、客户端卡片、MiMo think 修复、验收测试未开始。
- 推送:push 因 GitHub TLS 握手瞬时失败未完成,提交在本地。恢复后执行
  `git push -u origin codex/p20-2-capability-execution`。

## 一、任务书要求(摘要)

打通"Agent 发现能力 → 生成结构化操作 → 客户端审批卡片 → 用户确认 → 真实执行 → 状态复核 → 证据入 Agent Run"的完整链路。重点:

- 插件管理能力(查询/启用/禁用/卸载)复用 `plugin_manager`,不走第二套生命周期;
- Skill 管理(查询/安装/卸载/重载/验证)复用 `brain/plugins/skill.py`,Host 不在线不得虚报安装成功,不接受任意网址作为安装源;
- 审批绑定身份/会话/操作/目标,一次性,不可变成永久全局授权;聊天里说"我授权了"不算数;
- 客户端执行卡片(目标/类型/状态/影响/确认拒绝/执行证据),不得用通用"运行 Shell"代替;
- Windows MCP 仅核查记录缺口,不从零开发;MiMo think 泄漏独立修复;
- 最多五轮定向修复,禁大重构,不动 wallpaper 归属,不动 master,最后开 Draft PR 不合并。

## 二、已完成(提交 7a94f11)

全部改动在 `brain/capability_registry.py`(+210/-14),已通过定向冒烟与既有测试。

### 1. approve() 一次性授权(修复永久授权漏洞)

修复前的实际问题:
- `grant_ttl=0.0` 默认生成**永不过期**的 grant;
- 请求未带 target 时 grant 的 target 为空串,`matches()` 对空 target 放行**任意目标**;
- grant 在执行后残留,同一 capability/operation 可被再次 `invoke` 直接放行(绕过下一次审批);
- "approved" 状态无并发保护,两个审批动作可重复执行同一副作用。

修复后语义(capability_registry.py:`approve`):
- grant 范围锁死在**请求产生时**的 identity/session/target(由 `request_authorization` 落定),审批方传参只能沿用,不能改目标;
- `try: invoke(...) finally: revoke(grant_id)` —— 无论成功/失败/异常,执行后授权立即作废;TTL(默认 300s)仅作执行中断的兜底;
- `approved` 状态时再次 approve 返回 `request_in_flight`,不重复执行;`completed` 状态重放返回存储结果,handler 不再执行(幂等报告,不是重放副作用);
- 只有双方都不知道身份时才退回 `"*"`(兼容无身份调用方),只要知道一方就绑定。

### 2. 审批目标绑定

`request_authorization` 新增 `_target_from_args()`:从操作参数里的 `plugin/skill/name/package/pkg/target/doc/id` 字段派生 target。"禁用插件 A"的授权只对 A 有效,不能用于 B。

### 3. WRITE 权限层

新增 `WRITE = "write"`,与 READ/DELEGATED 并列;check/invoke 语义不变(非 READ/DELEGATED 一律需 grant),但注册表里能明确看出哪些操作是受控写操作。

### 4. plugins_skills 完整操作集(bootstrap 内注册)

全部为**纯委托**,不实现第二套生命周期:

| 操作 | 权限 | 委托目标 | 说明 |
|---|---|---|---|
| `list` | READ | `plugin_manager.list_plugins()` | 真实状态清单 |
| `plugin_status` | READ | `plugin_manager.status(name)` | 未安装返回 not_installed |
| `enable` | WRITE | `plugin_manager.enable(name)` | 执行后重读 status 作为 `after` 证据 |
| `disable` | WRITE | `plugin_manager.disable(name)` | 同上 |
| `uninstall` | WRITE | `plugin_manager.uninstall(name, purge_data)` | 内置插件保护在 plugin_manager(返回 state=builtin + 中文说明),handler 只透传 |
| `skill_status` | READ | `skill.host_alive()` + `list_skills()` | host 不在线时如实说 |
| `skill_list` | READ | `skill.list_skills()` | |
| `skill_install` | WRITE | `skill._install_zip(raw)` | **只接受 zip_b64**(面板上传内容),**不接受任何 URL**;host 不在线直接拒绝不落盘;安装事务成功后还必须 `_host_has_skill()` 在 Host 清单里确认,否则报 `install_unverified`(status=unknown),不宣布可用 |
| `skill_uninstall` | WRITE | `skill._uninstall(pkg)` | host 不在线拒绝 |
| `skill_reload` | WRITE | `skill._post_json("/reload")` + 清工具缓存 | host 不在线拒绝 |

`plugin_manager` 的返回统一是 `(bool, dict)`、错误字段 `err`;handler 归一为 `error` 并附加 `after` 复核状态。

### 5. 验证记录

- 冒烟(直接驱动 REGISTRY):未审批写 → `awaiting_authorization`+request_id;approve → 恰好执行一次;重复 approve → 不再执行;执行后 live grants 为空;下一次写需要**新**审批;target 绑定生效。全过。
- 既有测试无回归:`tests/test_p17_capability_tools.py`、`tests/test_p20_self_awareness.py`、`tests/test_audit_cleanup.py`(26 过)、`brain/tests/test_plugin_manager.py + test_plugin_market_state.py + test_plugin_transactions.py`(72 过 3 跳)。

## 三、关键架构发现(下一步要用的结论)

### 服务端

- **审批 HTTP 端点已存在**:`brain/server.py:2407-2450` `/api/capabilities/{approve,deny,invoke,events,grants}`(POST,admin 鉴权;approve 的 identity 取自请求凭据 `_who`,不收 body 注入);GET `/api/capabilities`(snapshot)与 `/api/capabilities/events?after=`(事件流)在 `server.py:4198-4211`。**没有独立的 `/api/capabilities/requests` 列表端点** —— 审批请求靠事件流 `capability_authorization_requested` 事件推给客户端。
- **缺口①(核心)**:`agent_runs.py` 完全不处理 `authorization_required`。工具链路是 `agent_runs._tool()`(1862)→ `tools.invoke_spec` → `tools.py:712 capability_call` → `REGISTRY.invoke`;返回 `ok=False, status=awaiting_authorization, request_id` 时,`_tool()` 把它当普通失败落 step(`tool_unknown`/`tool_failed`),模型看到失败文本,审批闭环断在这里。**需要在 `_tool()`/capability_call 返回路径上识别 awaiting + request_id:步骤落为"等待审批"状态、事件流携带 request_id、run 挂起等审批(approve 后续跑或 deny 后如实汇报),而不是失败。**
- 工具白名单边界:`agent_runs.py:2298-2300` `specs = tools.all_specs(...)` 注入 `ctx.allowed_tool_specs`,`_calls()`(1719)强校验工具名——普通聊天只读白名单不受本轮影响(capability_call 不在 READ_ONLY_TOOL_NAMES,只进 Agent Run 工具表)。
- 渠道隔离现状:`localtools.call` 自带风险/渠道矩阵(DELEGATED 透传);`invoke` 的 identity 来自 `ctx.capability_identity` / owner / src(tools.py:726-731),模型不可伪造。App/微信渠道要维持"拿不到 PC 执行授权"——审批端点 `_is_admin()` 鉴权 + identity 绑定已覆盖,测试需验证。
- `task_intent.py` 分类无需改:插件管理类指令已会落 `simple_execution` → agent_run 入口(server.py:3233-3262)。

### 客户端(缺口②)

- **没有任何 capability 审批 UI**。现有的是 Codex Agent Center 审批(`agent-center.js:56-67 approvalCard`,走 `/api/agent-center/approvals/{id}/respond`)和 Creator 安装审批(`pages.js:5201`),都不通用。
- Agent Run 步骤卡片:`client/src/js/agent-view.js:79-129 step(meta)`,`<details class="agent-step" data-state data-tool-cat>`;SSE 事件分发在 `pages.js:2599 onEvent`(白名单:`stage,tool,tool_reused,run_snapshot,think,text,done,error`);`addStep()` 在 `pages.js:2123`。服务端 `server.py:1997-2020` 把 agent_runs 事件翻译成 SSE `tool` 事件。**方案:服务端把 awaiting 审批作为带 request_id 的 tool 事件下发;客户端 step 卡片对该状态渲染 确认/拒绝 按钮,调 `/api/capabilities/approve|deny`,再把结果事件回填同一 step(按 step-key 就地替换的机制已存在)。** `TOOL_RULES`/`CAT_LABEL`(agent-view.js:28-69)已有 capability 分类文案可扩展。
- API 封装:渲染层 `client/src/js/api.js` → preload `ipcMain.handle('api')`(main.js:1045,拒绝自定义 base,自动带 src/token)。客户端调 `/api/capabilities/*` 直接走 `API.post` 即可。

### Windows MCP(缺口③,仅记录)

`brain/` 全目录 grep:MCP 仅 `codex_adapter.py:214`(`mcp_servers` 配置透传)和 `:607`(`mcpToolCall` 事件类型字面量)。**无 MCP Client、无 Server 配置/连接管理、无工具发现、无授权执行入口。** 交付时如实记录缺口,`plugins_skills.skill_install` 不伪装成 MCP 安装,不从零开发客户端。

### MiMo think 泄漏(缺口④)

- 真实向量:MiMo 把推理以 `<think>...</think>` **内联在 `content` 字段**流出(部分 OpenAI 兼容网关行为);`docs/evaluation/comparison.json` 里的 `think:latest` 是 Ollama 模型标签,不是泄漏。
- 收口点:`brain/model_providers/execution.py` `completion_events()`(流式:content 增量在 453-506 行循环里直接 yield `text_delta`;非流式:416-421)与 `normalize()`(188)。**正确做法是在 provider 归一化层剥离内联 `<think>` 块并转入 reasoning 通道(yield `think_delta` 而非 `text_delta`),这样聊天流(pages.js 只显示"思考中"不渲染 think 正文)和 Agent Run(agent_runs think_delta 只进 TRANSIENT)都自动正确。** 注意:`reasoning_content` 正规字段已正确走 think 通道,无需动;要防的是 content 内联块,且必须**流式安全**(标签跨 chunk 分割,参考 `ToolTextBoundary` 的缓冲做法)与**区分合法正文**(用户讨论 `<think>` 字样/代码块内的字面文本不能删——`ToolTextBoundary.feed` 的 code-tick 状态机可直接参考)。
- 先例:`docs/mimo-readonly-protocol-fix.md`(XML 工具标签泄漏的修复,同一 Boundary 思路);`agent_runs.py:2364` 已在 Agent Run 边界用 Boundary。
- Agent Run 的普通聊天流 `_stream_readonly_tool_chat`(brain.py:1824)XML 用 Boundary(1940-1964),但 **think 内联块同样未处理** —— 修 execution.py 归一化层可两处同时覆盖。

### 测试运行环境(重要,本机没有现成 python)

- 仓库 venv(`D:\vigil\venv`)指向的沙箱 base 已失效,`python`/`python3` 是 WindowsApps 占位符。
- 可用解释器:`C:\Users\26871\AppData\Local\Doubao\User Data\sandbox_runtime\bases\772984f6fd3a8ae0ae7d2422cd7c2bfa\python\python.exe`(3.13.16,无 pytest、无网络)。
- 跑测试:`PYTHONPATH="/d/vigil/venv/Lib/site-packages:/d/vigil/brain" <上述python> -m pytest tests/... -q`(借用 venv site-packages 的 pytest 9.1.1;`brain/tests/` 下的用例需要把 `brain/` 加进 PYTHONPATH)。CI 侧是 `pip install -r requirements-dev.txt && pytest tests`(.github/workflows/tests.yml:34-37)。

## 四、未完成工作(按优先级)

1. **Agent Run 审批闭环(缺口①)**:`agent_runs._tool()`/`tools.py capability_call` 识别 `awaiting_authorization`,步骤挂起、事件带 request_id、审批后续跑/拒绝后如实汇报;防止重复提交与审批后改参(request 的 args 快照已在 `request_authorization` 落定,approve 重放的是快照——已实现,Agent Run 侧要对齐)。
2. **客户端审批卡片(缺口②)**:见上;SSE 事件白名单加新事件类型;`agent-view.js` 步骤卡片加确认/拒绝按钮与执行证据展示。
3. **tools.py 文案**:`capability_call` 的工具说明(tools.py:213-224)更新 plugins_skills 新操作与"先查再调"指引(小改)。
4. **MiMo think 修复(缺口④)**:execution.py 归一化层,流式安全剥离 content 内联 `<think>`。
5. **MCP 缺口记录(缺口③)**:写进交付报告即可。
6. **验收测试**:任务书 10 项(插件真实查询/未审批不可改/审批成功复核/拒绝不执行/内置卸载拒绝/Skill Host 离线不虚报/重复-取消-失败-未知/只读无回归/渠道隔离/MiMo 无泄漏)。前 6 项大部分可直接测 REGISTRY + handler(离线);渠道隔离测 identity 绑定;MiMo 用假 SSE 流喂 `<think>` 分片。真实 Windows 客户端授权交互测试需真机,没有就标注未验证。
7. **全量回归**:Python 全仓 + Node CI(`client/tools/test-*.js`)+ compileall。
8. **Draft PR**:分支推送(本地 TLS 失败待重试)→ `gh` 不可用(未登录),用 GitHub API 或让用户开;保持 OPEN 不合并。

## 五、约束提醒(下一轮必须遵守)

- 不动 wallpaper 插件归属;不动 master;不开放通用 Shell;禁大重构(五轮定向修复预算,本轮已用一轮心态做注册层,余量集中给 agent_runs + 客户端)。
- 普通聊天只读白名单不得扩大(capability_call 保持只在 Agent Run 工具表)。
- App/微信渠道不得获得 PC 执行权限(approve 端点 admin 鉴权 + identity 绑定是现有防线)。
- 取消/结果未知不得自动重试副作用操作(approve 的 revoke-after-invoke + agent_runs 的 `tool_unknown`→unknown 语义已铺好)。
