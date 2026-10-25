# Vigil Creator 多文件生成与受控能力扩展 · 阶段一设计契约

> 状态：阶段一契约（对应 `VIGIL_CREATOR_OPTIMIZATION_PLAN.md` 的 **P0 契约与兼容设计**），**已冻结（2026-10-15）**。终态、预算继续入口、旧接口错误格式、Manifest 样例、未知能力策略五项已按 `VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md` 修订；P1 范围见上层方案 §9.1/§9.2。
> 范围：只交付本契约。生成器、检查器、宿主能力桥、流式 UI、安装事务改动均在 P1–P5 实现。
> 代码基线：`4c541ac`，Vigil v1.0.1。
> 依据：`VIGIL_CREATOR_OPTIMIZATION_PLAN.md`（权威方案）；相关代码 `brain/creator.py`、`brain/plugin_structure.py`、`brain/plugin_ops.py`、`brain/plugin_transaction.py`、`brain/creator_install.py`、`brain/plugin_host.py`、`brain/server.py`。

---

## 0. 目标、边界与三条核心原则

本轮目标：让 Vigil 能生成**带界面、可连接外部服务的多文件插件**，并把**生成过程、权限和失败原因**展示清楚。

**三条核心原则（不可协商）**：

1. **前端文件可以生成，生成内容仍先进入草稿区。** 生成 `.html/.js/.css` 不代表可运行；草稿在审核并明确批准前不获得运行/安装权限。
2. **插件声明权限，宿主负责实际执行权限检查。** Manifest 只是"申请"，授权由宿主建立并记录，运行时由宿主实际校验；Manifest 不能自授权限。
3. **取消固定 4,096 tokens 上限，改为模型适配、分文件生成和整任务预算。** 不得简单省略参数后依赖服务商默认值；须按实际模型能力决定参数，未知能力用有说明的兼容策略。

**范围澄清（与方案一致）**：

- 本轮**不开发 GitHub 项目可视化插件**，不实现分支图、项目进度识别、仓库导入或同步业务；**验收不使用 GitHub 可视化插件作为交付物**。用户消息中提到的"项目进度插件"仅作为**能力申请举例**（"读取选定目录 + 通过宿主访问指定仓库的分支/提交"），**不作为本阶段交付物**。若后续要以此为端到端验收样例，须先由用户显式改口径，本契约不预设。
- 不扩展 Codex 接管；不改变普通聊天、既有模型设置和已有内置插件行为。
- 原生任务恢复工作线（`VIGIL_TASK_RECOVERY_STAGE1.md`）**暂停**；本轮只做 Creator 必需的作业状态，不实施 Task/Step/Attempt 恢复系统。

**阶段一完成标准**：后续开发者遇到本契约列出的任意一个生成/权限/取消/兼容场景，都能据此确定处理方式；包格式、事件协议、错误分类、模型预算配置、能力边界、默认资源上限**无冲突**。

---

## 一、现状与问题（已核对代码）

| 环节 | v1.0.1 当前行为（代码锚点） | 本契约目标 |
|---|---|---|
| Creator 生成 | `creator.generate_draft(kind, requirement, api_cfg)`（`creator.py:455`）单次非流式调用 | 分阶段、分文件的作业 |
| 输出形态 | 一个 JSON 对象内 `{"files": {相对路径: 内容}}`（提示词见 `creator.py:172`） | 小型计划 + 逐文件内容 + 最终组装 |
| 输出预算 | **硬编码 `"max_tokens": 4096`**（`creator.py:240`）、`mp.chat(timeout=180)`（`creator.py:243`） | 删除固定值；模型适配 + 分层预算 |
| 插件文件白名单 | `_PLUGIN_EXTS = (".py", ".json", ".md", ".txt")`（`creator.py:71`）；安装侧 `ALLOWED_EXT = (".py", ".json", ".md", ".txt")`（`plugin_ops.py:46`） | 版本化多文件契约；放行 `.html/.js/.css` 静态资源 |
| 子目录 | `_safe_relpath` 拒绝子目录（`creator.py:75`）；`plugin_structure` 拒绝子目录 | 受控子目录白名单 |
| 结构检查 | `plugin_structure`（`_check_python`/`_read_manifest`，共 208 行）**不执行代码** | 扩展多文件/入口/引用检查 |
| 大小上限 | `MAX_ZIP_BYTES=4MB`、`MAX_UNPACKED=16MB`、`MAX_FILE_BYTES=2MB`、`MAX_FILES=64`（`plugin_ops.py:42-45`） | 集中配置并显示实际限制 |
| Manifest 校验 | `validate_manifest`（`plugin_ops.py:497`）、`re_name_ok_safe`（`plugin_ops.py:434`） | 增加能力/入口/前端资源/依赖字段 |
| 安装 | `creator_install.approve/install` + `plugin_transaction`（一次性批准、字节绑定、事务回滚） | 扩展绑定权限声明与授权 |
| 联网/目录 | 主要由**提示词**要求禁止 | 改为能力声明 + 授权 + 宿主实际检查 |
| Creator API | `server.py:1441-1534`：`/api/creator/{draft,revise,validate,delete,approve,install,drafts,approvals}` | 新增作业作业型接口，旧接口保留兼容 |

> 关键结论：**现有静态检查不执行生成代码，也不是安全沙箱。** 当前进程内 Python 插件与已有前端注入机制，**不能仅凭新增 Manifest 字段就宣称具备逐目录或逐域名限制**（`plugin_host.py` 通过导入/执行插件模块提供工具，进程内运行 = 无隔离）。

---

## 二、阶段一交付物清单

1. 一套**可版本化的 Creator 多文件插件包结构**（§三）。
2. **Manifest 能力声明**字段（§三）+ **能力与授权模型**（§六）。
3. **生成阶段与事件协议**（§四、§五）。
4. **错误分类、截断与取消规则**（§七）。
5. **模型适配与分层预算配置**（§八）。
6. **默认资源上限**（集中配置，§九）。
7. **兼容规则**（旧单文件格式，§十）。
8. **故障验收表**（§十一）。

---

## 三、多文件插件包结构与 Manifest

### 3.1 目标结构

```text
sample-panel/
├── zxplugin.json          # Manifest（必需）
├── __init__.py            # Python 后端入口（可选；受限模式须满足 §6.2）
├── frontend/              # 受控子目录：前端资源
│   ├── index.html         # 前端入口
│   ├── app.js
│   └── style.css
└── README.md
```

> 这是**目标结构示意**。实施时同时修改：① Creator 输出计划与模板；② 文件名/扩展名/目录/大小校验；③ `plugin_structure` 的多文件/入口/资源引用检查；④ 安装/更新/回滚对相同结构的支持；⑤ 前端资源加载器与宿主通信接口。**不能只扩展后缀白名单。**

### 3.2 允许的文件类型、目录深度、数量与大小

| 项 | 首期规则 | 现状对照 |
|---|---|---|
| 后端源码 | `.py` | 已允许 |
| Manifest / 数据 / 文档 | `.json`、`.md`、`.txt` | 已允许 |
| 静态前端资源 | `.html`、`.js`、`.css`（**新增，单独列白名单**） | 现状禁止 |
| 图片等二进制 | **首期不放行**（可后续单列白名单） | 现状禁止 |
| 可执行二进制 | **永久禁止** | 现状禁止 |
| 目录深度 | 允许**受控子目录**：`frontend/` 一级，**深度上限**见 §九（初始 ≤ 2 层） | 现状拒绝子目录 |
| 文件数量 | §九集中配置（初始沿用 `MAX_FILES` 语义，按多文件放宽） | `plugin_ops.py:45` |
| 单文件 / 总大小 | §九集中配置（初始沿用 `MAX_FILE_BYTES` / `MAX_UNPACKED`） | `plugin_ops.py:43-44` |

### 3.3 目录与文件名校验（延续并扩展 `_safe_relpath`）

- 禁止 `..` 路径穿越、绝对路径、`~`、盘符逃逸。
- **符号链接逃逸**：拒绝（不跟随 symlink），与现有"Never read rejected links/special files"一致（`plugin_structure.py:200`）。
- 文件名沿用 `re_name_ok_safe`（`plugin_ops.py:434`）安全字符集。
- 扩展名按 §3.2 白名单；**大小写归一**后判断。
- 允许的子目录仅 `frontend/`（受控集合，可配置）；其他目录一律拒绝。
- **路径穿越、符号链接逃逸、可执行二进制继续阻止**；允许生成 HTML/JS **不代表**允许获得 Electron 或系统权限。

### 3.4 前端如何调用插件后端

- 前端资源在**隔离容器**中运行（§6.2），**不直接**持有 `window.vigil`、Node、Electron 或 Brain Token。
- 前端通过**受控宿主桥**调用：桥按"实际插件身份 + 已批准版本 + 当前授权"校验每一次调用。
- 若插件提供 Python 后端，则前端 → 宿主桥 → 后端工具的调用链与**现有插件工具调用契约**对齐（`tools.all_specs` / `plugin_host.run_tool`）；工具说明、函数签名、schema **必须与真实宿主一致**，生成器**不得**创造不存在的 API。
- 前端资源默认**阻止直接外连**，网络经宿主代理申请（§6.2）。

### 3.5 每类文件怎样检查、检查失败怎样展示

| 文件类型 | 静态检查 | 失败展示 |
|---|---|---|
| `.py` | Python 语法（`_check_python`）；入口存在性 | 文件名 + 行号 + 语法错误 |
| `.json` | JSON 可解析；Manifest 结构（`validate_manifest`） | 字段级错误 |
| `.js` | JS 语法（**P2 新增**，不执行） | 文件名 + 行/列 + 语法错误 |
| `.html` | 基本结构 + 相对资源引用存在性 | 缺失资源名 |
| `.css` | 基本语法 | 文件名 + 行 |
| `zxplugin.json` | 入口/前端资源/能力/依赖字段完整 | 字段级错误 |

**检查失败怎样展示**：`validation` 事件/报告列出 `{file, kind, message}`；**格式通过不代表业务正确**；检查器**不执行生成代码**。

### 3.6 Manifest（`zxplugin.json`）字段契约

| 字段 | 含义 | 首期 |
|---|---|---|
| `package_format_version` | 包格式版本（新增，用于区分新旧格式） | **必需** |
| `plugin_id` | 插件标识 | 必需 |
| `entry` | Python 后端入口（相对路径，如 `__init__.py`） | 可选 |
| `frontend.entry` | 前端入口（如 `frontend/index.html`） | 可选 |
| `frontend.assets` | 前端资源列表 | 可选 |
| `capabilities` | **能力声明**（§六），逐项含用途与范围 | 可选（有则必须授权） |
| `dependencies` | **依赖声明**（只声明与展示，**不自动安装**） | 可选 |
| `min_host_version` | 最低宿主版本 | 可选 |
| 既有字段 | `name` 等（`validate_manifest` 已校验） | 保持 |

> **未知能力默认拒绝**；**声明、授予、运行时检查分别记录**；Manifest **不能自授权限**。

### 3.7 新包格式与运行模式（冻结）

**格式版本值**：`package_format_version` 为整数；**首版 = `1`**。无该字段且无 `frontend` 的包按旧格式处理（§十）。

**Manifest 完整样例（受限前端 + 可选后端）**：

```json
{
  "package_format_version": 1,
  "plugin_id": "sample-panel",
  "name": "sample-panel",
  "min_host_version": "1.0.1",
  "entry": "__init__.py",
  "frontend": {
    "entry": "frontend/index.html",
    "assets": ["frontend/app.js", "frontend/style.css"]
  },
  "capabilities": [
    { "id": "filesystem.read",
      "scope": { "dir": "<user-selected>", "recursive": true },
      "purpose": "读取项目文件以计算进度" },
    { "id": "network.https",
      "scope": { "domains": ["api.example.com"], "methods": ["GET"] },
      "purpose": "拉取只读状态" },
    { "id": "storage.plugin",
      "scope": { "dir": "<plugin-data>" },
      "purpose": "读写本插件专属数据目录" }
  ],
  "host_actions": ["storage.plugin.read", "storage.plugin.write"],
  "dependencies": []
}
```

**纯前端包样例（无 Python 后端）**：

```json
{
  "package_format_version": 1,
  "plugin_id": "sample-frontend-only",
  "frontend": { "entry": "frontend/index.html",
                "assets": ["frontend/app.js", "frontend/style.css"] },
  "capabilities": [
    { "id": "storage.plugin",
      "scope": { "dir": "<plugin-data>" },
      "purpose": "读写本插件专属数据目录" }
  ],
  "host_actions": ["storage.plugin.read", "storage.plugin.write"]
}
```

**能力声明结构**：`capabilities[]` 每项为 `{ id, scope, purpose }`——`id` 取自 §6.1 能力表（**未知 id 默认拒绝**）；`scope` 为**结构化对象**（目录/域名/方法与频率）；`purpose` 为人类可读用途。前端在受限模式下**只能**经宿主桥调用 `host_actions` 中声明且已授权的**声明式宿主动作**。

**三种运行模式的识别与加载**：

| 运行模式 | 识别条件 | 加载方式 |
|---|---|---|
| **受限前端包**（纯前端） | `entry` 缺失且 `frontend.entry` 存在 | **不进入 Python 插件加载路径**；在隔离容器加载 `frontend/*`，只能经宿主桥调用 `host_actions` 与已授权能力 |
| **受限前端 + 后端包** | `entry` 存在且满足 §6.2 隔离条件 | 前端同上；后端**仅在被验证的隔离可用后**启用；否则该包**标记"后端不支持"**，前端草稿仍可审核 |
| **旧单文件包** | 无 `package_format_version` 且无 `frontend` | 走**现有插件加载路径**（与今日一致） |

**纯前端包的加载与调用（防误落旧路径）**：

- 加载：宿主把 `frontend.entry` 指向的文档载入**隔离容器**（不共享主聊天 DOM，不注入 `window.vigil`/Node/Electron）。
- 调用：前端**只能**经受控宿主桥调用 `host_actions` 中声明且**已授权**的动作（如 `storage.plugin.read/write`、`network.https.get`）；桥按"实际插件身份 + 已批准版本 + 当前授权"校验。
- **防误落旧路径**：包内存在 `frontend/` **或** `package_format_version` 时，**禁止**回退到旧插件加载/注入路径；识别为受限前端包失败时**拒绝加载并提示**，**不静默降级**为全权限运行。

**依赖声明**：`dependencies[]` **只声明与展示，不自动安装**（见 §四）。

---

## 四、目标生成流程（分步骤、分文件）

```text
用户发送创作需求
  → 创建 Creator 作业并返回 job_id
  → 生成文件计划、接口和能力声明           （stage: analyze → plan）
  → 校验计划与文件路径                      （stage: plan_check）
  → 按依赖顺序逐文件生成                     （stage: generate）
  → 文件语法、包结构、引用与能力检查           （stage: validate）
  → 对可修正失败进行有限修复                  （stage: repair）
  → 组装正式草稿及验证报告                    （stage: assemble）
  → 用户审核文件、差异和所需权限
  → 明确批准后安装（复用现有事务入口）
```

**生成过程中禁止**：执行插件、测试真实联网、使用生产凭据、自动调用 pip/npm。

### 4.1 分文件生成的意义

避免把全部代码塞进一个巨大的 JSON 字符串（现状 `creator.py:172`）。文件内容仍用**明确协议**传递，但**一次失败只修复对应文件**。

---

## 五、Creator 作业与流式事件协议

### 5.1 接口形态（已冻结）

| 接口 | 作用 | 关键约束 |
|---|---|---|
| `POST /api/creator/jobs` | 创建作业 | 稳定 `request_id` **防提交重发创建重复作业** |
| `GET /api/creator/jobs/<id>` | 读取状态/文件清单/验证结果/事件游标 | 校验归属与认证 |
| `GET /api/creator/jobs/<id>/events?after=<seq>` | 只读订阅或补读已有事件 | **不得启动新的模型生成** |
| `POST /api/creator/jobs/<id>/cancel` | 请求取消 | 停止继续派发后续模型请求 |
| `POST /api/creator/jobs/<id>/grant_budget` | 追加预算 | **不隐式启动/继续**；不清零累计消耗（§5.9） |
| `POST /api/creator/jobs/<id>/continue` | 显式继续 | **不隐式授予预算**（§5.9） |
| `POST /api/creator/jobs/<id>/input` | 补充输入/授权 | 解除 `waiting_user`（§5.9） |

> 以上接口与 `VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md`（统一执行契约 §7.1）共享**事件信封与生命周期语义**；命名差异由适配层映射。

**兼容（冻结）**：旧 `/api/creator/*` **保留同步返回与既有错误格式**。其中**仅 `POST /api/creator/draft` 与 `POST /api/creator/revise` 属“创建生成作业”动作**——内部创建作业并**同步等待到执行终态**（`succeeded`→对外 `draft_ready` + `draft_id`，或 `failed`），响应体与今日兼容；**其余 `validate / delete / approve / install / drafts / approvals` 保持各自原职责，不创建生成作业**（`server.py:1441-1534`）。映射见 §5.8。

### 5.2 作业模型

作业至少包含：**归属、请求 ID、当前阶段、状态、模型快照、预算/消耗、文件状态、安全错误分类**。读取/订阅/取消**均校验归属及既有认证**；**`job_id` 本身不是授权**。

### 5.3 事件类型

事件用 `job_id + seq` 标识：

| 事件 | 内容 |
|---|---|
| `stage` | 分析/规划/生成文件/验证/修复等**真实阶段** |
| `think_delta` | 模型**实际返回且可展示**的思考内容；**未提供时不伪造** |
| `text_delta` | 可见说明或当前文件生成预览 |
| `file_started` / `file_completed` | 文件名、状态、长度、检查结果 |
| `validation` | 静态检查问题及当前处理结果 |
| `usage` | 已知消耗与剩余预算；**未知费用不显示虚构数值** |
| `done` / `error` / `cancelled` | 明确终态，**避免无终止的等待提示** |

### 5.4 显示层规则

- 正文、思考区、错误样式**复用主对话**；控制逻辑按 `job_id` 隔离，**不能串入普通聊天或另一作业**。
- 订阅经现有 Electron preload/main 代理接入，**不让渲染器直接取得 Brain Token**。
- **断开显示连接 ≠ 取消**；**重新订阅不重发生成请求**。
- 缓存缺口通过快照恢复并明确提示，**不能声称补回已丢弃的逐 token 事件**。
- **完整思考内容只作临时展示**，不写入插件文件、长期日志或恢复检查点。
- **文件预览以文本展示，不能作为 HTML 注入聊天页。**

### 5.5 与重启的关系

**P1 刷新丢失缺陷增补（2026-10-15）**：按共同方案 `VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md` §9.2，创作与修改必须保存用户请求、kind、稳定 ID、作业关联和安全错误摘要；失败时无草稿文件，也必须有可恢复的展示记录。刷新/重开客户端后恢复记录并查询已有作业，不重新发起生成。未送达服务端的请求由隔离的本地待确认记录兜底；按稳定 ID 与服务端合并去重。该记录不进入普通聊天模型上下文或长期记忆，不保存完整推理、凭据及无界原始响应。显式清除展示记录不隐式删除草稿或取消任务。

本次只做 Creator 必需的作业状态与已有文件保存。**Brain 重启后未完成作业标为中断**；**不承诺跨重启自动续跑，也不自动重新计费生成**。

### 5.6 作业状态机与允许转换（冻结，按统一执行契约对齐）

> 本节按 `VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md`（统一执行契约，P0）对齐：**执行生命周期与草稿安装生命周期分离**，**阶段（stage）与生命周期状态分离**。旧稿把 `analyzing…assembling`/`draft_ready`/`installed` 与生命周期并列，现修正。

**执行生命周期状态**（Creator 生成 run）：

| 状态 | 含义 |
|---|---|
| `queued` | 已创建，尚未开始 |
| `running` | 当前阶段执行（对应 `stage`） |
| `waiting_user` | 缺授权/输入/必须确认的证据 |
| `paused_budget` | 预算不足，停止新派发 |
| `succeeded` | 草稿生成结束（对外 `draft_ready` + `draft_id`） |
| `failed` | 以错误终止 / 修复耗尽 |
| `cancelled` | 已取消 |
| `interrupted` | 进程重启导致中断 |

**阶段（`stage` 字段，非状态）**：`analyzing` / `planning` / `generating` / `validating` / `repairing` / `assembling` 作为 `running` 的 `stage`，**不另建终态规则**。

正常路径：`queued → running(stage: analyzing→planning→generating→validating→(repairing→validating)*→assembling) → succeeded`

| 从 | 到 | 触发事件 |
|---|---|---|
| `queued` | `running` | 调度认领成功（stage 依次推进） |
| `running` | `waiting_user` | 缺授权/输入/证据 |
| `waiting_user` | `running` | 明确 `continue`（同进程） |
| `running` | `paused_budget` | 触及预算上限 |
| `paused_budget` | `running` | `grant_budget` **且**随后显式 `continue`（两动作分离，§5.9） |
| `running` | `succeeded` | 草稿组装完成（对外 `draft_ready` + `draft_id`） |
| **任一非终态** | `failed` | 不可恢复错误 / 修复耗尽 |
| **任一非终态** | `cancelled` | 取消生效 |
| **任一非终态** | `interrupted` | Brain 重启 |

**执行终态**：`succeeded` / `failed` / `cancelled` / `interrupted`。`interrupted` **不自动续跑**，仅允许只读查询与用户显式新操作（§5.5）。

**草稿安装生命周期（独立，不改执行 run 终态）**：`draft_ready`（由 run 的 `succeeded` 产生）→ `approved`（绑定代码摘要+Manifest+权限）→ `installed`（事务安装成功，**写草稿状态**）；另有 `draft_cancelled`（用户放弃）、`rejected`（审批失效，需重新审核）。**安装成功不再改生成 run 的终态。**

### 5.7 请求 / 快照 / 事件 JSON 样例（可直接实现）

**创建请求（`POST /api/creator/jobs`）**
```json
{
  "request_id": "req_2f9c7b",
  "kind": "plugin",
  "requirement": "生成一个显示项目进度的面板",
  "model_snapshot": { "provider": "openai", "model": "gpt-4o" }
}
```
**创建响应**
```json
{ "job_id": "job_ab12cd", "status": "queued",
  "events_url": "/api/creator/jobs/job_ab12cd/events?after=0" }
```
**作业快照（`GET /api/creator/jobs/<id>`）**
```json
{
  "job_id": "job_ab12cd",
  "request_id": "req_2f9c7b",
  "owner": { "user_id": "u_local", "src": "pc" },
  "status": "running",
  "stage": "generating",
  "model_snapshot": { "provider": "openai", "model": "gpt-4o" },
  "budget": { "max_requests": 40, "max_tokens_total": 256000, "max_seconds": 1800,
              "consumed": { "requests": 6, "tokens": 41230, "seconds": 210 } },
  "files": [ { "path": "frontend/index.html", "status": "done", "bytes": 812 },
             { "path": "frontend/app.js", "status": "generating", "bytes": 0 } ],
  "error": null,
  "event_cursor": 42
}
```
**事件样例**
```json
{ "job_id": "job_ab12cd", "seq": 43, "type": "file_completed",
  "ts": "2026-10-15T10:21:03+08:00",
  "payload": { "path": "frontend/app.js", "bytes": 2048, "validation": "ok" } }
```
```json
{ "job_id": "job_ab12cd", "seq": 88, "type": "done",
  "payload": { "status": "succeeded", "draft_ready": true, "draft_id": "draft_7g8h" } }
```
> **终态映射（冻结）**：执行 run 的终态为 `succeeded`（对外可读 `draft_ready` + `draft_id`）；`draft_ready`/`installed` 属**草稿安装生命周期**，**不写入执行 run 终态**（§5.2/§5.6）。示例：
```json
{ "job_id": "job_ab12cd", "seq": 60, "type": "error",
  "payload": { "class": "output_truncated", "file": "frontend/app.js",
               "message": "输出被截断，未写入半个文件" } }
```

### 5.8 取消 / 完成 / 错误并发与重连终态（冻结）

- **终态仅一次**：同一作业只有一个终态事件。
- **优先级**：`cancel` 与 `done`/`error` 竞争时，**先持久化的终态获胜**：若 `cancelled` 已落盘，后续 `done`/`error` **不改写**为完成（最多补记"实际已完成"到审计）；若 `done`/`error` 已落盘，`cancel` **幂等返回当前终态**，不产生第二次终态。
- **取消语义**：停止派发后续模型请求；**不回滚**已产生的文件；对已派发请求**报告实际完成情况**（§七）。
- **重连终态处理**：重连 `events?after=<seq>` 时——已有终态事件 → 直接返回终态 + 快照，**不启动新生成**；游标早于已丢弃事件 → 以**快照**补齐并标注"部分中间事件不可回放"，**不谎称补回逐 token**。
- **旧接口同步映射**：**仅 `POST /api/creator/draft`、`POST /api/creator/revise`** 内部创建一个作业并**同步等待到执行终态**（`succeeded`→**对外 `draft_ready` + `draft_id`**，或 `failed`）；**其余旧接口（`validate` / `delete` / `approve` / `install` / `drafts` / `approvals`）保持各自原职责，不创建作业**。**返回体与错误对象沿用今日实际形态 `{ok:false, err:"..."}`**（`server.py:1443-1532`；**不是** `{error:{code,message}}`），成功为 `{ok:true, ...}`。适配层在 P1 实现，**不改变外部契约**；**新作业接口单独定义**错误结构（`{job_id, error:{class, message, ...}}`）。

### 5.9 预算授予、继续与等待（冻结）

三个动作**互相分离**，均需鉴权与归属校验：

| 动作 | 接口 | 语义 | 禁止 |
|---|---|---|---|
| 追加预算 | `POST /api/creator/jobs/<id>/grant_budget` | 修改预算上限，**不清零累计消耗** | **不隐式启动/继续** |
| 显式继续 | `POST /api/creator/jobs/<id>/continue` | 在允许状态认领执行 | **不隐式授予预算** |
| 补充输入/授权 | `POST /api/creator/jobs/<id>/input` | **只保存输入并检查条件**（不自行恢复执行） | 不改预算；解除需再 `continue` |

- **`input` 与 `continue` 分离（冻结）**：`input` **只保存输入/授权并重新检查条件，不自行恢复执行**；条件满足后状态**仍为 `waiting_user`**，须再显式 `continue` 才回到 `running`（`waiting_user → running` 只由 `continue` 触发，见 §5.6 转换表）。
- **只有允许状态才能认领执行**；重复点击**不能启动第二个执行器**。
- **等待用户是否计时（冻结）**：`waiting_user` **不计入执行时间**，但 **wall-clock deadline 照走**，两者**分离记录**（与统一执行契约 §6.2 一致）。
- **本期继续仅限当前进程仍持有有效执行上下文的 `waiting_user` / `paused_budget`**；重启后为 `interrupted`，用户可审阅保留成果后**新建任务**，**不承诺无重复执行**。

---

## 六、能力声明与授权

### 6.1 首批能力

| 能力 | 授权范围 | 宿主执行方式 |
|---|---|---|
| `storage.plugin` | 本插件专属数据目录 | 宿主解析插件身份并限定目录 |
| `filesystem.read` | 用户选定目录及读取范围 | 宿主校验路径、符号链接和当前授权 |
| `filesystem.write` | 明确批准的输出目录或目标 | 宿主检查当前权限及写入策略 |
| `network.https` | 目标域名、用途及允许的方法 | 宿主代发请求，限制跳转和目标地址 |
| `background.sync` | 频率、用途、启停状态 | 宿主调度，撤销后停止新任务 |

**未知能力默认拒绝。**

### 6.2 实际执行边界（必须解决的架构问题）

- 新生成前端运行在**隔离容器**中，不共享主聊天 DOM，不直接获得 `window.vigil`、Node、Electron 或 Brain Token。
- 前端调用**受控宿主桥**；桥按**实际插件身份、已批准版本、当前授权**检查每次调用，**不能只相信消息中的插件 ID**。
- 前端**默认阻止直接外连**，经宿主网络代理申请；代理校验域名、方法、目标 IP、重定向与返回大小；**跳转不能绕过授权或带出凭据**。
- **独立 Python 进程只提供故障隔离，本身不是权限沙箱**。任意 Python 后端**只有在可验证的权限隔离完成后**，才能作为"受限插件"启用。
- 可先交付**"隔离前端 + 声明式宿主动作"的受限模式**；尚未实现的 Python 隔离能力**明确标为不支持**；保持草稿可审核，**不自动降级为全权限运行**。
- 已有原生受信插件**保持兼容**，其权限边界**如实标注**；**不得**给它加一个 Manifest 就宣称已受限；本轮**不自动迁移**已有插件。

### 6.3 授权生命周期

- 安装前展示能力、用途、具体范围；运行时**再次核验**。
- 更新新增权限、变更授权范围或改变已审核字节后，**旧批准失效**，需重新审核。
- **撤销后**拒绝新的能力调用，并停止后续后台调度；**已发出的请求或写入需报告实际完成情况**，不能把撤销等同于回滚所有副作用。
- 能力授权记录与现有 Creator 一次性安装批准**分开**；批准快照**同时绑定代码摘要、Manifest 和权限要求**。安装继续委托现有事务实现（`plugin_transaction.py`），**不增加旁路复制安装器**。

> 关于凭据：令牌由**宿主管理**，插件只得到经过授权的操作结果，**不得到凭据值**。第三方账号接入只**预留通用凭据引用机制**，本轮**不建设 GitHub 连接器或任何仓库业务**。

---

## 七、错误、截断、取消与有限修复

### 7.1 结束原因分类（错误分类）

保留提供者结束原因，至少区分：

| 分类 | 含义 |
|---|---|
| `empty_content` | 空正文 |
| `output_truncated` | 输出被截断（**不得误报成普通 JSON 错误**） |
| `invalid_format` | 格式错误 |
| `provider_error` | 服务错误 |
| `validation_failed` | 验证失败 |
| `budget_exhausted` | 预算耗尽 |

- **思考内容不能代替源码正文**；只返回思考时提示"未得到文件正文"。
- 结构计划可使用**已确认支持的 JSON/schema 输出能力**；**不向所有服务商盲目发送同一格式参数**。
- 逐文件生成以**服务端已批准的文件名**为目标，内容协议与模型身份分开，减少巨大 JSON 中源码转义导致的失败。
- 错误展示**安全分类、受影响文件、操作建议**；诊断**不得**直接打印完整模型响应、请求头或凭据。

### 7.2 截断与有限修复规则

- **截断后不得把半个文件标为完成。** 仅在预算允许时**继续该文件或重新生成该文件**，再整体检查。
- 格式修复与语法修复**分别计数**；建议初版**每文件一次自动修复**，P1 固定每文件与整作业上限（§九）。
- 修复有**总次数上限**且计入作业消耗。

### 7.3 取消规则

- `POST .../cancel` **请求取消**：停止继续派发后续模型请求。
- **取消 ≠ 立即停止副作用**：已派发的请求/写入需报告实际完成情况（与权限撤销同一原则，§6.3）。
- 重新订阅显示连接**不重发生成请求**；取消后不自动重启生成。

---

## 八、模型适配与分层预算（删除固定 4096）

### 8.1 实施原则

- **移除 Creator 硬编码 `max_tokens=4096`**（`creator.py:240`）。
- 普通用户**不必**为每次生成手动填 tokens；高级设置可覆盖模型输出预算。
- **不承诺无限输出。** 直接省略 `max_tokens` 可能触发服务商默认小上限，因此必须**按实际模型/提供者能力**决定请求参数；**能力未知时采用有说明的兼容配置，不猜测最大值**。

### 8.2 分层预算

| 层级 | 作用 |
|---|---|
| 单次模型请求 | 适配模型上下文与可用输出上限；结合当前文件任务分配 |
| 单个文件 | 限定总字节、续写/重生成及自动修复次数 |
| 整个作业 | 限定总请求、累计 tokens、时间、可确认费用 |

**规则**：

- 请求、修复、继续生成**都计入同一作业消耗**；显示连接重连**不增加**生成请求。
- 预算耗尽后**停止新请求**（状态置 `paused_budget`），保留已验证文件与错误位置；追加预算**修改上限且不清零累计消耗**，额度**必须覆盖继续所需消耗**。**`grant_budget` 不隐式启动、`continue` 不隐式授予预算**——须先授予、再显式继续（两动作分离，见 §5.9 与统一执行契约 §6.3）。
- 费用无法准确计算时显示"**未知**"，仍用**请求数、tokens、字节、时长**约束。
- 模型与服务商配置在**作业创建时固定快照**；用户在聊天中切换模型**不能**悄悄改变正在生成的作业；重新选模型属于**明确的新操作**，**不复制凭据进作业记录**。

### 8.3 预算 / 修复集中配置与默认值（冻结，取消"待 P1 固定"）

集中配置键（**单一来源**，缺失取默认；界面显示实际生效值）：

| 配置键 | 作用 | 默认值 | 缺失配置时处理 |
|---|---|---|---|
| `creator.limits.max_files` | 文件数量 | 64 | 取默认 |
| `creator.limits.max_depth` | 目录深度 | 2 | 取默认 |
| `creator.limits.max_file_bytes` | 单文件大小 | 2097152（2 MiB） | 取默认 |
| `creator.limits.max_unpacked_bytes` | 解压总大小 | 16777216（16 MiB） | 取默认 |
| `creator.limits.max_zip_bytes` | 压缩包大小 | 4194304（4 MiB） | 取默认 |
| `creator.budget.max_requests` | 整作业模型请求数 | 40 | 取默认 |
| `creator.budget.max_tokens_total` | 整作业累计 tokens | 256000 | 取默认 |
| `creator.budget.max_seconds` | 整作业时长（秒） | 1800 | 取默认 |
| `creator.budget.repair_per_file` | 每文件自动修复次数 | 1 | 取默认 |
| `creator.budget.repair_per_job` | 整作业修复次数 | 3 | 取默认 |
| `creator.budget.unknown_output_tokens` | 模型输出上限**未知时的兼容值** | 4096（**仅此兼容场景**，非全局硬编码） | 取默认 |

**缺失 / 非法配置处理**：任一键缺失 → **取上表默认值并继续**（不失败）；配置了非法值（负、零、超大）→ 拒绝并**回落默认 + 记录告警**；**不得**因缺失配置而退化为"无上限"。

**模型输出上限的兼容策略（冻结）**：`max_tokens` **不再硬编码为 4096**。按模型能力表决定；**能力未知时，接口允许省略则省略**（不传输出上限）；**仅当接口必须传值**才使用 `creator.budget.unknown_output_tokens` 的**显式兼容值**，并在作业中标"未知能力兼容模式"，**不猜测最大值**。

**与方案的 P0 一致性**：本节即方案要求"P0 冻结的默认资源上限与预算配置"。P1 若调整默认值，须**回改本节并记录**；**契约中不再保留"待 P1 固定"项**。

---

## 九、默认资源上限（集中配置）

所有上限**集中配置**（单一来源），**界面显示实际限制**，不在多个模块各自硬编码。

| 上限 | 首期初始值 | 现状锚点 |
|---|---|---|
| 压缩包大小 `MAX_ZIP_BYTES` | 4 MiB | `plugin_ops.py:42` |
| 解压后总大小 `MAX_UNPACKED` | 16 MiB | `plugin_ops.py:43` |
| 单文件大小 `MAX_FILE_BYTES` | 2 MiB | `plugin_ops.py:44` |
| 文件数量 `MAX_FILES` | 64 | `plugin_ops.py:45` |
| 目录深度 | ≤ 2 层（仅 `frontend/`） | 新增 |
| 每文件自动修复次数 | 1 | 新增（§8.3） |
| 整作业修复次数 | 3 | 新增（§8.3） |
| 整作业请求数 | 40 | 新增（§8.3） |
| 整作业累计 tokens | 256000 | 新增（§8.3） |
| 整作业时长 | 1800s | 新增（§8.3） |
| 模型输出上限未知时的兼容值 | 4096（仅兼容场景） | 新增（§8.3） |

> 所有上限**集中配置**，缺失取默认（§8.3），**不再存在"待 P1 固定"项**；P1 调整默认值时须回改本表。

---

## 十、兼容规则

### 10.1 旧版单文件插件继续安装与使用

- **保持旧单文件格式兼容**：`.py` + 可选 `zxplugin.json`（现状 `creator.py:173`）。
- **新格式要有明确版本**：`package_format_version` 区分。
- **不把新目录结构误当成旧格式静默安装**；反之亦然。
- 安装/更新/回滚**同时支持两种结构**（`plugin_ops.install`、`plugin_transaction`）。

### 10.2 判定与迁移

| 场景 | 规则 |
|---|---|
| 包内无 `package_format_version` 且无子目录 | 按**旧格式**处理 |
| 包内有 `package_format_version` | 按**声明的版本**处理；未知版本拒绝并提示 |
| 旧格式被要求生成前端 | 提示"需新版包格式"，不静默加文件 |
| 已有插件 | 本轮**不自动迁移**；权限边界如实标注 |

### 10.3 API 兼容

旧 `/api/creator/*` 全部保留；新作业接口**并行**提供。P1 固定旧接口到新作业的包装方式。

---

## 十一、故障验收表（阶段一交付物）

| # | 场景 | 期望行为 | 验收断言 |
|---|---|---|---|
| AC-1 | 提交重发（请求重复） | 单一次作业 | 稳定 `request_id`，**不创建重复作业** |
| AC-2 | 生成中截断 | 明确 `output_truncated` | **不得**把半个文件标为完成；**不得**误报成普通 JSON 错误 |
| AC-3 | 仅思考无正文 | 明确 `empty_content`/提示"未得到文件正文" | **不伪造**思考占位 |
| AC-4 | 未知能力被申请 | 宿主**拒绝** | 未声明或未授予的能力被拒；Manifest 不能自授权 |
| AC-5 | 能力撤销 | 撤销后**立即生效** | 拒绝新调用 + 停止后台调度；报告已发出请求的实际完成情况 |
| AC-6 | 显示连接断开/重连 | 不重发模型请求 | 订阅/取消校验归属；**`job_id` 不是授权** |
| AC-7 | 生成非法文件 | 文件被拒 | 路径穿越/符号链接逃逸/可执行二进制/超限被拒；受控子目录放行 |
| AC-8 | 预算耗尽 | 停止并保留进度 | 保留已验证文件与错误位置；继续需**新预算授予**，累计消耗保留 |
| AC-9 | 模型切换 | 不影响运行中作业 | 作业创建时快照模型；切换属新操作 |
| AC-10 | 旧单文件包 | 仍可生成/安装 | 旧格式兼容；新结构不误装为旧格式 |
| AC-11 | 审批失效 | 修改/扩权后旧批准失效 | 批准绑定代码摘要+Manifest+权限 |
| AC-12 | 取消与完成并发 | 单一终态 | 先持久化者获胜；另一请求幂等返回当前终态 |
| AC-13 | 重连已有终态作业 | 返回终态+快照 | 不启动新生成；游标缺口以快照补齐并标注 |
| AC-14 | 纯前端包 | 走隔离前端路径 | 存在 `frontend/` 或 `package_format_version` 时**不**落入旧插件加载路径 |
| AC-15 | 旧 `/api/creator/*` | 同步返回与错误格式不变 | 仅 POST draft/revise 包装为生成作业；其余保持原职责；错误对象沿用现有形态 |
| AC-16 | 创作失败后刷新页面（上层方案 §9.2） | 需求与报错可恢复，且不重跑 | 提交即建稳定 `turn_id`/`request_id`；成功/失败/取消/中断为**同一记录**状态更新；服务端持久记录为权威，**未送达**请求由**隔离的本地待确认记录**兜底；恢复显示**不增加**生成或有副作用工具执行；**不保存**凭据/完整推理/未脱敏报文 |

**断言级细则**：

1. **截断不得记完成**（AC-2）。
2. **思考不代替正文**（AC-3）。
3. **未声明/未授权能力默认拒绝**（AC-4）。
4. **撤销立即生效且不等同回滚**（AC-5）。
5. **重连不重发生成**（AC-6）。
6. **`job_id` 非授权凭据**（AC-6）。
7. **不得只加 Manifest 就宣称受限**（§6.2）。
8. **不得自动安装依赖**（§四、§10）。
9. **终态唯一且不可翻回**（AC-12）：取消与完成竞争只有一个终态。
10. **重连不重发生成**（AC-13）：已有终态直接返回。
11. **纯前端包不落旧路径**（AC-14）：识别失败时拒绝加载并提示，不静默降级。
12. **无"待 P1 固定"项**（§8.3/§九）：预算与资源上限均有默认值，缺失取默认。
13. **刷新不丢记录、恢复不重跑**（AC-16；上层方案 §9.2）：Creator 失败/取消/中断在刷新、重开、断流后仍可恢复，且**不触发**重新生成；展示存档与模型上下文/长期记忆分离。

---

## 十二、与方案的阶段映射（本契约 = P0）

**2026-10-15 排期补充**：主对话与 Creator 的共同方案 P1 现包含“模型适配与 Creator 流式复用”。本表保留 Creator 专项阶段编号，跨工作线以 `VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md` §9/§9.1/§9.2 及统一执行契约 §十一为准。P1 接通创作/修改的最小生成作业、只读订阅、取消与重连，以及主对话正文/可展示思考/错误/阶段组件和 Electron 代理复用；按 job 隔离并检查完整性后才形成有效草稿。通用预算授予继续仍在共同方案 P2，分文件生成与自动修复仍在共同方案 P3；本次不开始实现。

**2026-10-15 P1 缺陷增补（上层方案 §9.2）**：创作失败后刷新会丢失需求与报错（`client/src/js/pages.js``creatorSend` 仅插入 DOM，不产生 `/api/history`）。Creator 须在刷新/重开/断流后恢复**展示记录**：提交即建稳定 `turn_id`/`request_id`，成功/失败/取消/中断为**同一记录**的状态更新；服务端持久展示记录 + 作业快照为权威，**未送达**请求由按连接身份与会话**隔离的本地待确认记录**兜底，**不伪装已接收、不自动重新生成**。保留安全错误分类、可读原因、`job_id`/`run_id` 与**已收到部分正文**（标注“不完整”）；**不保存**凭据/完整推理/未脱敏报文。展示存档与**模型上下文/长期记忆分离**（Creator 失败不得写成普通聊天成功回答）。恢复显示**不增加**生成或有副作用工具执行（AC-16）。**保留期限与数量上限集中配置**；现有清除/删除入口**只移除展示记录与本地待确认项**，**不隐式删除草稿文件或取消运行中任务**；**换账号/连接/渠道不串历史**。此项**仅保证记录与显示恢复，不扩展为跨重启任务续跑**。

| 方案阶段 | 本契约提供 |
|---|---|
| P0 契约与兼容设计 | **本文档（冻结包格式/事件/错误分类/模型预算配置/能力边界/默认上限）**；并须与 `VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md`（统一执行契约）的**双生命周期 / 预算计时 / 旧接口错误格式**对齐（见本文 §5.6/§5.7/§5.8/§5.9/§8.2） |
| P1 生成可靠性与流式 | 依 §五/§七/§八 实现作业、流式、结束原因、移除 4096 |
| P2 多文件草稿 | 依 §三/§四 实现计划、逐文件生成、受控目录、检查器 |
| P3 宿主能力与隔离 | 依 §六 实现隔离前端、能力桥、授权、撤销 |
| P4 安装更新衔接 | 依 §六.3/§十 实现审批快照绑定、事务、更新、回滚 |
| P5 专项验收与发布 | 依 §十一 验收；构建与回归 |

**P1/P2 即使生成了联网调用代码，也不能提前安装为受限运行插件**；权限执行边界须在 **P3** 验收完成。

---

## 附：代码锚点索引

| 主题 | 文件:行 |
|---|---|
| 生成入口 | `brain/creator.py:455`（`generate_draft`）、`creator.py:472`（`revise_draft`） |
| 输出协议提示 | `brain/creator.py:172-173`（`files` 字典） |
| 固定 4096 / 超时 | `brain/creator.py:240`（`max_tokens: 4096`）、`creator.py:243`（`timeout=180`） |
| JSON 抽取 | `brain/creator.py:108`（`_extract_json`） |
| 插件扩展名白名单 | `brain/creator.py:71`（`_PLUGIN_EXTS`）、`creator.py:72`（`_SKILL_EXTS`） |
| 相对路径校验 | `brain/creator.py:75`（`_safe_relpath`） |
| 草稿校验 | `brain/creator.py:351`（`validate_draft`） |
| 安装侧白名单/上限 | `brain/plugin_ops.py:42-46`（`MAX_*`、`ALLOWED_EXT`） |
| Manifest 校验 | `brain/plugin_ops.py:497`（`validate_manifest`）、`plugin_ops.py:434`（`re_name_ok_safe`） |
| 安装/事务 | `brain/plugin_ops.py:305`（`install`）、`brain/plugin_transaction.py` |
| 结构检查 | `brain/plugin_structure.py`（`_check_python`/`_read_manifest`，共 208 行） |
| 一次性批准/字节绑定 | `brain/creator_install.py`（`approve`/`install`/`list_approvals`） |
| Creator API | `brain/server.py:1441-1534` |
| 插件运行（进程内） | `brain/plugin_host.py`、`brain/tools.py`（`run_tool`/`all_specs`） |
| 历史交接依据 | `docs/CREATOR_MVP1_HANDOFF.md`、`docs/CREATOR_MVP2_HANDOFF.md`、`docs/creator-mode-mvp2-design.md`（均已随文档清理移除，见 `git log -- docs/CREATOR_MVP1_HANDOFF.md`） |
