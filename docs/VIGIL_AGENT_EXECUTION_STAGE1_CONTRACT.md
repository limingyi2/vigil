# Vigil 主对话与 Creator 多步任务执行 — 阶段一契约（P0 契约收敛）

> 状态：阶段一契约（对应 `VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md` 的 **P0 契约收敛**），冻结候选稿。
> 范围：只交付本契约。模型适配、执行服务、流式订阅、Creator 多步生成、隔离与安装在 P1–P5 实现。
> 代码基线：`4c541ac`，Vigil v1.0.1。实施前重新核对 dev 与工作区差异。
> 依据：`VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md`（上层方案，权威）、`VIGIL_CREATOR_OPTIMIZATION_PLAN.md`（Creator 细节）。
> 相关代码：`brain/brain.py`、`brain/model_providers/`、`brain/server.py`、`brain/creator.py`、`brain/plugin_structure.py`、`brain/plugin_ops.py`、`brain/creator_install.py`、`brain/plugin_transaction.py`、`client/src/js/pages.js`、`client/electron/main.js`、`client/electron/preload.js`。

---

## 0. 目标、边界与不可协商原则

本轮以**可验证的任务完成**作为成功标准。一次用户提交可以包含多次模型请求、工具执行、验证与修复；**一次模型响应结束不等于整个任务完成**。

**不可协商原则**：

1. **成功必须有证据。** 没有执行工具时，不得声称已修改、已验证或已安装；`[DONE]`、HTTP 200、空正文都不能单独证明任务成功。
2. **终态唯一且不可翻回。** 同一 run 只有一个终态；完成与取消竞争以**先持久化的合法终态**为准。
3. **预算耗尽只暂停，不代表完成。** 授予预算与继续是**两个分离动作**，互不隐式触发；累计消耗不清零。
4. **显示订阅不驱动执行。** 订阅、重连、断开只影响显示，**不得**创建任务、发模型请求或重试执行。
5. **未知即拒绝/降级并标注。** 未知能力默认拒绝；未知模型输出上限采用**有说明的兼容值**，不猜测最大值。

**范围澄清（与上层方案一致）**：

- 本轮覆盖 Vigil 主对话执行链与 Creator 插件生成，共用模型适配、结果归一化、流式事件与预算基础设施；**普通问答与插件生成保留各自执行策略与验证方式**，不强迫所有聊天先生成计划。
- **不包含**：Codex 会话接管、GitHub 项目可视化插件业务；通用 Task/Step/Attempt 恢复系统的阶段二；Brain 重启后自动续跑或自动重放有副作用的操作。
- 原生任务恢复工作线（`VIGIL_TASK_RECOVERY_STAGE1.md`）**暂停**；本轮只实现当前执行所需的最小状态与文件保存，**不宣称完成其恢复与副作用去重保证**。

**阶段一完成标准**：后续开发者遇到本契约列出的任意一个执行/暂停/取消/预算/兼容场景，都能据此确定处理方式；路由图、能力来源、参数策略、双生命周期、预算计时、接口与事件样例**无冲突、无悬空入口**。

---

## 一、现状与问题（已核对代码）

| 入口 | v1.0.1 当前行为（代码锚点） | 本契约目标 |
|---|---|---|
| `brain.stream_chat` | 流式正文 + 思考展示；单次输出按分类设为 **600 / 4096**（`brain.py:1314`）；**此函数当前不支持工具调用**（`brain.py:1283`） | 能展示流不等于能持续执行多步任务；统一按模型能力与步骤需求决定输出 |
| `brain.draft_native` 工具循环 | 默认 **24 轮 / 270 秒**（`brain.py:1053-1054`），按轮检查时间；`_mt = 4096 if _know else 600`（`brain.py:1061`）；超出后另作无工具收尾（`brain.py:1067-1070`） | 固定轮次/预算不宜作为长期任务默认值；**收尾不能冒充任务成功** |
| `brain.chat`（`/api/chat`） | 非流式、可带工具与图，返回 JSON | 保留；作为多步任务的一个执行入口 |
| `brain/creator.py` | 非流式 `mp.chat()`，**硬编码 `max_tokens=4096`**（`creator.py:240`），`timeout=180`（`creator.py:243`），一次 JSON 携带全部文件 | 分阶段、分文件生成；删除固定值 |
| `brain/model_providers/` | 工厂 `get_provider(cfg)`（`__init__.py:34`）按 `model_mode` 选 cloud/local/hybrid；`capabilities()` 声明能力（`base.py:78`） | 作为**唯一**能力来源与参数适配层 |
| `brain/server.py` 路由 | 见 §二路由图；错误统一 `{ok:false, err:...}` | 补齐主对话/执行/Creator 的路由与降级图 |

> 关键结论：**此表描述不同代码路径，不假定主对话所有请求都经过同一个工具循环。** 下面 P0 必须先补齐 `server.py` 路由、前端选择及降级路径图，避免重复执行。

---

## 二、路由图与降级路径（P0 冻结）

### 2.1 服务端路由现状（已核对）

鉴权：所有路由先过 `_check()`；失败 → `401 {"err":"bad token"}`（`server.py:1614-1616`）。管理类接口在各自分支再判管理员。受限设备（`role=device`）走默认拒绝闸门。

| 路径 | 方法 | 处理 | 说明 |
|---|---|---|---|
| `/api/chat` | POST | `brain.chat`（非流式，可工具/带图） | 主对话执行入口 |
| `/api/chat` | GET | 极少数 GET 化探测（兼容） | 保留 |
| `/api/chat/stream` | POST | `brain.stream_chat`（SSE） | **当前无工具调用** |
| `/api/creator/*` | 混合 | `creator.py`（见 §5.1） | 同步返回 |
| `/api/agent-center/*` | POST/GET | `codex_adapter.handle_http` | 本轮**不改** |
| `/api/plugins*`、`/api/plugin/*` | 混合 | `plugin_ops` / `plugin_host.frontend_assets` | 插件加载/资产 |
| `/api/attachments/*` | 混合 | `attachments` | 上传/关键帧 |
| `/api/device/*`、`/api/management/*`、`/api/sync/*` | 混合 | 设备/心跳/同步 | 本轮**不改** |

### 2.2 前端选择与降级路径（P0 冻结）

> **本节区分“已实现事实”与“目标路由”，不得混写。** 现状由客户端**按附件与流式桥可用性**选择路径（`client/src/js/pages.js:1095-1135`），**并不判断“多步任务/需要工具”**；是否使用工具由**服务端**在所选路径内决定。目标路由在 P2 引入统一执行服务后生效。

**A. 已实现事实（v1.0.1，冻结为兼容基线）**：

```text
用户提交（当前前端）
  ├─ 带附件 → POST /api/chat（非流式，JSON）
  ├─ 无附件且宿主流式桥 global.vigil.apiStream 可用 → POST /api/chat/stream（SSE）
  │      └─ 流式出错 / 无 done 事件 → 前端按当前逻辑显示“失败/无响应”（`pages.js:1076-1079`），不做自动重发
  └─ 无附件且无流式桥 → 降级 POST /api/chat（非流式）
```

- 前端**不持有**“多步任务”判定；`/api/chat` 走工具循环，而 `/api/chat/stream` **当前无工具**（`brain.py:1274`、`1283-1284`）。
- **流式出错当前只显示错误**（`pages.js:1076-1079`），**不自动**改用非流式重跑。

**B. 目标路由（P2 生效，冻结设计）**：

```text
用户提交（request_id）
  └─ 服务端/客户端判定是否“多步任务/需要工具”
       ├─ 否（普通闲聊）：快速路径（保留流式展示）
       └─ 是（多步任务）：统一执行服务（按 run 管理并发/预算/取消/订阅）
```

- **禁止重复执行（目标硬约束）**：同一次用户提交**只能触发一次**执行器；降级/切换**仅对无副作用步骤**自动进行，有副作用步骤需明确继续（§六）。
- **回退开关只影响新请求**：P2 用统一执行服务替代现有路径，已运行任务不因开关切换被重复调度。
- **Creator 不走主对话聊天锁**：执行状态按认证主体、会话与 run 隔离；有副作用的共享资源另行串行化。

### 2.3 统一执行开关（P0 冻结）

- 新执行路径使用**集中开关**逐步启用；开关**只影响新请求**，已运行任务不得因开关切换被重复调度。
- 预算、事件与权限状态**不能在不同路径各维护一份互不一致的数据**（单一来源，§五/§六）。

---

## 三、模型能力来源与参数策略（P0 冻结）

### 3.1 能力来源（按优先顺序）

1. **当前服务端的明确限制、经过校验的管理员配置。**
2. 服务商模型元数据接口返回的能力。
3. 适配器维护的官方模型能力表。
4. **未知模式。**

能力记录至少包含：**服务商、规范化端点标识、模型 ID、输入/输出或共享上下文限制、参数名称、来源、更新时间、可信状态**；**不保存 API Key**。缓存按**端点与模型**隔离；配置改变时重新解析；**不能只按模型名称匹配**代理。

- 模型自述**不能**作为上限证据；一次请求成功仅说明该次可用。
- **首次普通生成不进行昂贵的极限探测。**

### 3.2 参数选择

- **已知能力**时，输出额度结合**当前步骤、上下文剩余空间、作业剩余预算**分配，不必每次用满最大值。
- 区分**独立输入/输出限制**与**共享上下文限制**；先计算请求可容纳性，**不能统一套用一个相减公式**。
- 由**适配器**处理 `max_tokens` / `max_completion_tokens` / `max_output_tokens` 差异，以及思考额度的提供者语义。
- **未知能力且接口允许省略**时：**不传输出上限**，显示“服务端默认，上限未知”；省略**不等于无限输出**，也不保证更长。
- **接口必须传值**时：优先使用管理员兼容配置；无配置时暂用可配置的 **4096**，并**明确标为兼容值**，**不能宣称探测得到**。
- 参数拒绝**仅**在明确、可解析的上限错误下修正；**最多自动修正一次**，消耗计入预算。**网络中断不能当作参数错误重发。**

### 3.3 现有适配层映射（已核对，P0 冻结为改造基线）

| 层 | 现有锚点 | 本契约要求 |
|---|---|---|
| 工厂 | `get_provider(cfg)`（`model_providers/__init__.py:34`），`normalize_mode`（`:28`）缺省/乱填 → cloud | 保持“没配新功能就是老行为”的 fail-safe |
| 模式 | `MODES = (cloud, local, hybrid)`（`:25`）；hybrid 主模型走云端 | 保持；hybrid 本地委派仍属后续 |
| 能力声明 | `ModelProvider.capabilities()`（`base.py:78`）返回 `{kind, tools, native_tool_calls, xml_tool_text, stream, vision}` | **作为统一能力对象**扩展上下文/输出/参数名来源 |
| 错误语义 | `ProviderUnavailable` / `ProviderHTTPError` / `ProviderResponseError`（`base.py:26-38`） | 映射到 §4 结束原因分类 |
| Ollama | `num_ctx`/`num_predict`/`think`（`ollama.py:77-81`），原生 `/api/chat` | 参数差异**留在适配器**，上层不感知 |

> **P0 定名**：基础模块置于 `brain/model_providers/` 与**新增执行服务模块**；执行服务命名在 P1 按现有代码确定，**避免另建第二套模型配置、鉴权、审批和安装器**。

### 3.4 参数收敛（删除固定 600 / 4096）

- **删除 `brain.py:1061`、`brain.py:1314` 的 `600/4096` 分类硬编码**，以及 `creator.py:240` 的固定 `max_tokens=4096`。
- 改为：`能力表 → 步骤需求 → 剩余预算` 共同决定；**能力未知**时，**接口允许省略则省略参数**（不传输出上限），**仅当接口必须传值**才使用 §3.2 的管理员兼容配置并标注 `未知能力兼容模式`。这与 §3.2 一致：**允许省略则省略；必须传值才用兼容配置。**
- 普通闲聊仍应**快速返回**；不为“文本越长越好”无限追加请求。

---

## 四、结果归一化与结束原因（P0 冻结）

提供者层**统一输出**：`正文`、`工具调用`、`允许展示的推理摘要/内容`、`usage`、`结束原因`、`错误类别`、**是否完整结束**。底层字段由各适配器解释。

**至少区分以下结束原因**：

| 分类 | 含义 | 是否完整结束 |
|---|---|---|
| `normal` | 正常结束且有正文 | 是 |
| `tool_calls` | 需要执行工具 | 否（继续） |
| `length` | 长度截断 | 否 |
| `refusal` | 拒绝 | 否 |
| `service_error` | 服务错误（`http_error`/`bad_response`） | 否 |
| `connection_lost` | 连接中断（`unavailable`） | 否 |
| `thinking_only` | 仅思考无正文 | 否 |
| `invalid_structure` | 无效结构 | 否 |

**规则**：

- `[DONE]`、HTTP 200 或空正文**都不能单独证明任务成功**。
- `usage` 缺失时标为**未知或估算**，**不伪造精确消费**。现有 `stream_options.include_usage`（`brain.py:1326`）与 `usage.prompt_tokens/completion_tokens` 统计（`brain.py:1092-1094`）为基线。
- **XML 工具文本不作为默认可执行协议**：仅当提供者**显式 opt-in**（`supports_xml_tools`，`cloud.py:56`/`base.py:59`）且当前允许时才解析；否则按普通正文处理（`brain.py:1101-1128` 现状保持）。

---

## 五、执行生命周期与草稿生命周期（双向分离，P0 冻结）

**执行生命周期**与**草稿安装生命周期**分离。同一 `run` 的终态**只允许原子持久化一次**。

### 5.1 统一执行状态表

| 执行状态 | 行为 | 后续 |
|---|---|---|
| `queued` | 已创建、未执行 | 调度后进入 running |
| `running` | 当前阶段执行 | 成功、等待、暂停或失败 |
| `waiting_user` | 缺授权、输入或必须确认的证据 | **同一进程内**经明确动作继续 |
| `paused_budget` | 预算不足，停止新派发 | 授予预算后**显式继续** |
| `succeeded` | 对应执行目标验收通过；Creator 为**草稿生成结束** | 终态 |
| `failed` | 不可恢复或允许的修复已用尽 | 终态 |
| `cancelled` | 已停止调度 | 终态，另报告在途操作结果 |
| `interrupted` | Brain 重启或执行上下文丢失 | 终态，**不自动续跑** |

### 5.2 阶段（stage）与状态的分离（解决终态冲突）

`analyzing` / `planning` / `generating` / `validating` / `repairing` / `assembling` 用作 **`stage`**，**不与上述生命周期重复建两套终态规则**。

- **Creator 对外如需 `draft_ready`**：明确映射到 **`succeeded` + `draft_id`**（见 §九）。
- **安装成功写草稿状态**，**不再改生成 run 的终态**。

### 5.3 允许转换（冻结）

| 从 | 到 | 触发 |
|---|---|---|
| `queued` | `running` | 调度认领成功 |
| `running` | `waiting_user` | 缺授权/输入/证据 |
| `waiting_user` | `running` | 明确 `continue`（同进程，§六.3） |
| `running` | `paused_budget` | 触及预算上限 |
| `paused_budget` | `running` | `grant_budget` **且**随后显式 `continue`（两动作分离） |
| `running` | `succeeded` | 验收全部满足 |
| `running` | `failed` | 不可恢复 / 修复耗尽 / 无进展达阈值 |
| **任一非终态** | `cancelled` | 取消生效 |
| **任一非终态** | `interrupted` | 进程重启/上下文丢失 |

**终态**：`succeeded` / `failed` / `cancelled` / `interrupted`。

### 5.4 草稿安装生命周期（独立）

| 草稿状态 | 行为 | 与执行 run 的关系 |
|---|---|---|
| `draft_ready` | 草稿就绪，待审核 | 由执行 run 的 `succeeded` 产生（携带 `draft_id`） |
| `approved` | 用户批准（绑定代码摘要 + Manifest + 权限） | 不改 run 终态 |
| `installed` | 事务安装成功 | **写草稿状态**，不改 run 终态 |
| `draft_cancelled` | 用户放弃 | 不改 run 终态 |
| `rejected` | 审批失效（变更/扩权） | 需重新审核 |

---

## 六、预算与时序（P0 冻结）

### 6.1 预算维度

预算继续保留：**请求数、累计 token、总执行时间、单请求超时、文件资源限制、修复次数**。

- **预算配置单一来源**；Creator 现有默认值（`VIGIL_CREATOR_STAGE1_CONTRACT.md` §8.3）可作为初始值；主对话**按交互策略单独配置**，**不把 24 轮 / 270 秒简单复制**为长期任务默认值。
- **每步及每次模型调用前检查停止条件**。重复修复无进展达到阈值则失败或等待用户，**不循环碰运气**。

### 6.2 预算语义（不可混淆）

- **预算到达只表示暂停**，不表示完成。
- 追加预算**修改上限，累计消耗不清零**；额度**必须覆盖继续所需消耗**。
- **明确区分执行时间与绝对 deadline**。
- **等待用户时是否计时须冻结**：**默认不计入执行时间**（`waiting_user` 挂起计时），但 **deadline 不因等待而暗中延长**——即 wall-clock deadline 照走，execution-time 预算暂停。两者分离记录。
- `usage` 不可得时依靠**请求数、时间、资源上限**兜底；超过预算的**在途调用完成后记录实际消耗**，**不假装能精确提前截停计费**。

### 6.3 授予与继续（两个分离动作，冻结）

| 动作 | 语义 | 禁止 |
|---|---|---|
| `grant_budget` | 追加预算额度，修改上限，**不清零累计消耗** | **不隐式启动/继续执行** |
| `continue` | 在允许状态认领执行，继续派发 | **不隐式授予预算** |
| `input` / `grant_permission` | **只保存输入并检查条件**（条件满足后仍为 `waiting_user`） | 不隐式改预算；**不自行恢复执行** |

- **`input` 与 `continue` 分离（冻结）**：`input` **只保存输入/授权并重新检查条件，不自行恢复执行**；条件满足后状态**仍为 `waiting_user`**，须再显式 `continue` 才回到 `running`（`waiting_user → running` 只由 `continue` 触发，见 §5.3 转换表）。
- **只有允许状态才能认领执行**；**重复点击不能启动第二个执行器**。
- **本期继续仅限当前进程仍持有有效执行上下文的 `waiting_user` / `paused_budget`**。重启后变为 `interrupted`；用户可审阅保留成果后**新建任务**，**不承诺无重复执行**。跨重启继续留给暂停中的任务恢复工作线。

### 6.4 取消语义

- `cancel` **请求取消**：停止继续派发后续请求。
- **取消 ≠ 立即停止副作用**：**取消成功不代表在途工具副作用已经停止**；**未知结果单独展示，禁止自动重放**。
- 重新订阅显示连接**不重发生成请求**；取消后不自动重启。

---

## 七、接口与事件样例（P0 冻结）

### 7.1 建议操作接口（统一，主对话与 Creator 共用形状）

| 接口 | 作用 | 关键约束 |
|---|---|---|
| `POST /api/agent/runs` | 创建 run | 稳定 `request_id` + 鉴权 + **原子去重** |
| `GET /api/agent/runs/<id>` | 读快照（状态/阶段/文件/验证/游标） | 校验归属与认证；**`run_id` 本身不是授权** |
| `GET /api/agent/runs/<id>/events?after=<seq>` | 只读订阅/补读 | **不得启动新的模型生成** |
| `POST /api/agent/runs/<id>/cancel` | 请求取消 | 幂等；已终态返回当前终态 |
| `POST /api/agent/runs/<id>/grant_budget` | 追加预算 | **不隐式启动** |
| `POST /api/agent/runs/<id>/continue` | 显式继续 | **不隐式授予预算** |
| `POST /api/agent/runs/<id>/input` | 补充输入/授权 | 解除 `waiting_user` |

> Creator 侧沿用既有 `/api/creator/jobs...` 命名（`VIGIL_CREATOR_STAGE1_CONTRACT.md` §5.1）；两者共享**事件信封**与**生命周期语义**，命名差异由适配置层映射。**创建及所有写动作均带稳定 `request_id`、鉴权与原子去重。**

### 7.2 统一事件信封

`{ run_id, seq（单调）, type, ts, payload }`；Creator 关联 `job_id` / `draft_id`。

| 事件 | 内容 |
|---|---|
| `stage` | 真实阶段（分析/规划/生成/验证/修复/组装） |
| `text_delta` | 正文/说明增量 |
| `think_delta` | 提供者**明确可展示**的推理内容；**不伪造** |
| `tool_started` / `tool_finished` | 工具名、参数校验结果、结果摘要 |
| `file_started` / `file_completed` | 文件名、状态、长度、检查结果 |
| `validation` | 静态检查问题及处理结果 |
| `usage` | 已知消耗与剩余预算；未知费用**不显示虚构数值** |
| `paused` / `waiting_user` | 暂停/等待信号 |
| `done` / `error` / `cancelled` | **唯一终态** |

### 7.3 样例

**创建请求（`POST /api/agent/runs`）**
```json
{
  "request_id": "req_2f9c7b",
  "kind": "chat",
  "intent": "把下载目录按类型分好类",
  "model_snapshot": { "provider": "cloud", "model": "deepseek-chat" }
}
```
**创建响应**
```json
{ "run_id": "run_ab12cd", "status": "queued",
  "events_url": "/api/agent/runs/run_ab12cd/events?after=0" }
```
**快照**
```json
{
  "run_id": "run_ab12cd",
  "request_id": "req_2f9c7b",
  "owner": { "user_id": "u_local", "src": "pc" },
  "status": "paused_budget",
  "stage": "generating",
  "model_snapshot": { "provider": "cloud", "model": "deepseek-chat" },
  "budget": { "max_requests": 40, "max_tokens_total": 256000, "max_seconds": 1800,
              "deadline": "2026-10-15T11:00:00+08:00",
              "consumed": { "requests": 6, "tokens": 41230, "exec_seconds": 210 } },
  "error": null,
  "event_cursor": 42
}
```
**事件样例**
```json
{ "run_id": "run_ab12cd", "seq": 43, "type": "tool_finished",
  "ts": "2026-10-15T10:21:03+08:00",
  "payload": { "tool": "move_file", "ok": true, "summary": "移动 12 个文件" } }
```
```json
{ "run_id": "run_ab12cd", "seq": 88, "type": "done",
  "payload": { "status": "succeeded" } }
```
```json
{ "run_id": "run_ab12cd", "seq": 60, "type": "paused",
  "payload": { "status": "paused_budget", "reason": "budget_exhausted" } }
```

### 7.4 重连与缓存（冻结）

- **订阅/重连只读**，不创建任务、不发模型请求。创建响应丢失后**以 `request_id` 找回原 run**。
- 执行终态**持久化**；有限事件缓存**按游标补读**；缺口**返回快照 + 明确提示**，**不谎称补回逐 token**。
- 界面**区分**“本次响应结束 / 等待工具 / 等待授权 / 预算暂停 / 任务完成”。
- **服务端在发起工具前校验权限**；前端**不持有 Brain Token**，仍走现有 Electron 代理。
- 生成源码**按文本展示，不注入聊天 DOM**；推理内容只展示提供者明确可展示的部分，**不进入长期检查点或插件文件**。
- 对**不支持模型原生流式**的服务商：显示真实阶段并在结果到达后展示内容，**不能模拟逐 token 当作真实流**。

---

## 八、兼容规则与旧接口映射（P0 冻结）

### 8.1 路由与错误格式（已核对的现行形态）

- **鉴权失败**：`401 {"err":"bad token"}`（`server.py:1615`）。
- **通用错误封装**：`{ok:false, err:"..."}`（`server.py` 大量分支，如 `_json(400, {"ok": False, "err": ...})`）。
- **成功封装**：`{ok:true, ...}`。

### 8.2 旧接口适配（按动作分别映射）

| 旧接口 | 新语义 | 兼容要求 |
|---|---|---|
| `POST /api/creator/draft`、`/api/creator/revise` | **创建生成作业并同步等待**到 `draft_ready` 或失败 | 响应体与**今天一致** |
| `/api/creator/{drafts,approvals,validate,delete,approve,install}` | 列表/查询/验证/删除/审批/安装 | **保持各自职责**，不并入生成作业 |

- **旧响应保持 `{ok:false, err:...}` 等实际格式及原 HTTP 语义**；新接口**单独定义**错误结构（`{run_id, error:{class, message, ...}}`）。
- 旧 `/api/chat`、`/api/chat/stream` 外部契约不变；新执行路径**并行**提供，**回退仅影响新请求**。

---

## 九、与 Creator 契约的对齐（P0 修订项）

P0 须修订 `VIGIL_CREATOR_STAGE1_CONTRACT.md`，消除与本节冲突：

| 冲突点 | 旧表述 | 修订后（本契约） |
|---|---|---|
| **终态冲突** | §5.6 把 `draft_ready`/`installed` 当状态，`installed` 为终态 | `draft_ready` → **`succeeded` + `draft_id`**；`installed` 属**草稿生命周期**，**不改执行 run 终态** |
| **状态/阶段混淆** | §5.6 把 `analyzing…assembling` 与生命周期并列 | 这些是 **`stage`**，不是独立状态/终态 |
| **预算继续入口** | §8.2 仅“授予额外预算” | `grant_budget` **不隐式启动**；`continue` **不隐式授予**；**等待用户是否计时冻结**（§6.1） |
| **旧接口错误格式** | §5.8 写作 `{error:{code,message}}` | **沿用现行 `{ok:false, err:...}`**（`server.py:1443-1532`） |
| **Manifest / 未知能力** | §3.6 已含“未知能力默认拒绝” | 确认保留；**未知 id 默认拒绝**，`host_actions` 只走声明式 |
| **取消语义** | §5.8/§7.3 | 明确 **取消 ≠ 停止在途副作用**，未知结果**不自动重放**（§6.4） |

**Creator 对外 `draft_ready` 映射（冻结）**：`succeeded` + `draft_id`；审批/安装/运行验证属**独立草稿生命周期**（§5.4）。

---

## 十、故障验收表（阶段一交付物）

| # | 场景 | 期望行为 | 验收断言 |
|---|---|---|---|
| AE-1 | 已知模型输出策略 | 不再统一 600 / 4096 | 按能力+步骤+预算分配；能力来源可追溯 |
| AE-2 | 代理同名模型缓存 | 按端点+模型隔离 | 不按模型名串用；配置改变重新解析 |
| AE-3 | 必填输出参数未知 | 用显式兼容值 | 仅兼容场景用 4096，标注“未知能力兼容模式” |
| AE-4 | 参数错误 | 最多修正一次 | 网络中断**不**当参数错误重发 |
| AE-5 | 长度限制/仅思考/流中断 | 不误报完整成功 | `length`/`thinking_only`/`connection_lost` 单列 |
| AE-6 | 一次提交多轮 | 完成多个模型/工具轮次 | 前端显示**真实阶段**；工具参数碎片**不执行** |
| AE-7 | 重复创建/继续/重连 | 不增加工具执行或生成 | `request_id` 原子去重；订阅只读 |
| AE-8 | 预算耗尽 | 保留有效成果 | 授予额度后**显式继续**，累计消耗不归零；未授予**不能继续** |
| AE-9 | 完成与取消竞争 | 单一终态 | 先持久化者获胜；另一请求幂等返回当前终态 |
| AE-10 | 重启 | 未结束 run 标 `interrupted` | **不自动重跑**；已就绪草稿仍可审核 |
| AE-11 | 写操作结果未知 | 进入核对/等待 | **不能凭“没收到响应”重做** |
| AE-12 | 旧接口 | 聊天/Creator 错误格式不变 | `{ok:false, err:...}` 与原 HTTP 语义保持；新接口单列错误结构 |

**断言级细则**：

1. **无证据不得声称完成**（§0.1）。
2. **终态唯一且不可翻回**（AE-9；§5.3）。
3. **预算与继续分离**（AE-8；§6.3）。
4. **订阅不驱动执行**（AE-7；§7.4）。
5. **未知即拒绝/兼容并标注**（AE-3；§3.2）。
6. **取消不等同回滚**（§6.4）。
7. **旧接口错误格式沿用现行形态**（AE-12；§8.2）。
8. **Creator `draft_ready` → `succeeded`+`draft_id`**（§5.2/§九）。

---

## 十一、与上层方案的阶段映射（本契约 = P0）

| 方案阶段 | 本契约提供 |
|---|---|
| **P0 契约收敛** | **本文档**：路由图（§二）、能力来源/参数策略（§三）、结果归一化（§四）、双生命周期（§五）、预算计时（§六）、接口/事件样例（§七）、旧接口兼容（§八）、Creator 对齐（§九） |
| P1 模型适配 | 依 §三/§四 实现结果归一化、能力缓存、动态输出参数、长度/中断识别 |
| P2 主对话执行与流 | 依 §二/§五/§六/§七 实现统一工具循环与订阅、按 run 管理并发/预算/取消 |
| P3 Creator 多步生成 | 依 `VIGIL_CREATOR_STAGE1_CONTRACT.md`（已按 §九 修订）实现 |
| P4 包与授权 | 依 `VIGIL_CREATOR_OPTIMIZATION_PLAN.md` §六 分阶段实现 |
| P5 集成与发布 | 依 §十 验收；定义与记录边界 |

**本期以模拟提供者、受控本机工具及无害测试面板验证**；真实模型冒烟另行记录服务商、配置、实际 `usage` 与结果，**不以无上限压力测试证明能力**。

---

## 附：代码锚点索引

| 主题 | 文件:行 |
|---|---|
| 工具循环（轮/预算/输出分类） | `brain/brain.py:1053-1054`（24 轮/270s）、`brain.py:1061`（`4096 if _know else 600`）、`brain.py:1066-1070`（按轮查预算）、`brain.py:1084`（`mp.chat`） |
| 流式聊天（当前无工具） | `brain/brain.py:1274`、`brain.py:1283-1284`（无工具说明）、`brain.py:1314`（`_mt=600/4096`）、`brain.py:1326`（`stream_options.include_usage`） |
| XML 工具文本门 | `brain/brain.py:1101-1128`；`model_providers/base.py:59`、`cloud.py:56` |
| 提供者工厂/模式 | `brain/model_providers/__init__.py:25,28,34` |
| 能力声明 | `brain/model_providers/base.py:78`（`capabilities()`） |
| 提供者错误语义 | `brain/model_providers/base.py:26-38` |
| Ollama 参数适配 | `brain/model_providers/ollama.py:77-81`（`num_ctx`/`num_predict`/`think`） |
| 鉴权与错误封装 | `brain/server.py:1614-1616`（401）、`server.py:1179`（`_json`）、`server.py:1195-1532`（`{ok:false,err}`） |
| 聊天路由 | `brain/server.py:208-209`（`/api/chat`、`/api/chat/stream`） |
| Creator 路由 | `brain/server.py:1441-1534` |
| Creator 固定 4096/超时 | `brain/creator.py:240`（`max_tokens: 4096`）、`creator.py:243`（`timeout=180`） |
| 客户端流式/Creator UI | `client/src/js/pages.js` |
| Electron 代理/能力边界 | `client/electron/main.js`、`client/electron/preload.js` |
