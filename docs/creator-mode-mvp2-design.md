# Creator MVP-2 设计文档：审核 → 授权 → 安装

> **状态：设计稿，未实现。** 本文件只写方案与实证依据，不含任何安装/执行代码，
> 也没有改动 P3 审批策略或 Skill Host 安全限制。
>
> 涉密与越权是这一版的核心：MVP-1 只产出**草稿**（可看、可改、可删，绝不安装），
> MVP-2 要跨过"把模型生成的文件真的放进 `plugins/` 或技能目录"这条线。

## 0. 一句话

给草稿加一条**人点一次的、绑定内容摘要的、一次性**的安装授权；
安装本身**不写第二个安装器**，把已审核的内容喂给现有 P4 事务。

## 1. MVP-1 现状（实证，非推测）

| 事实 | 证据 |
| --- | --- |
| 草稿落在 `<DATA_DIR>/creator_drafts/<cr_id>/`，历史版本在**目录外**的 `.history/<cr_id>/` | `creator.py:_draft_dir` / `_history_dir` |
| `meta.json` 记 id/kind/requirement/summary/risks/model/version/status/validation/created_at | `creator.py:_persist` |
| **全库没有任何内容摘要（hash）** | `grep sha256 brain/creator.py` 无命中 |
| `validate_draft()` 会把结果**写回** `meta["validation"]` | `creator.py:381-383` |
| 没有任何安装/装载入口 | `server.py:_creator_admin` 的 docstring 与实现 |
| 界面只有 重新验证 / 删除草稿 / 提交修改 | `client/src/js/pages.js:draftCardHtml` |

⚠️ 第 3、4 条是本次设计的直接动因：**"审核的是什么"目前无法被证明**，
而 `meta.validation` 与内容放在同一处、且可被再次写。

## 2. 关键问题与结论

### Q1 草稿快照哈希怎样绑定审核内容？

**算摘要，算两次，存三处。**

1. `creator.content_digest(draft_id) -> (digest, files)`：

   ```
   digest = sha256("\n".join(f"{sha256(bytes(content))}  {relpath}"
                             for relpath, content in sorted(files)))
   ```

   取文件集合（排除 `meta.json`）、按相对路径排序、每个文件先各自散列再拼 ——
   与文件系统遍历顺序、换行、mtime 全部无关，只与"内容是什么"有关。
   同时返回逐文件摘要，供界面逐行展示。

2. 生成时记进 `meta["content_digest"]`；**读取/审核/安装时都重算**，
   三处不一致就以"重算"为准（`meta` 只是缓存，不作为判据）。

3. 授权记录里存 `(draft_id, version, digest, kind, target_name)`。

**为什么必须含 `version`**：`revise_draft` 是"同目录升 version、旧版进 `.history/`"，
同一个草稿目录在不同版本上的内容摘要天然不同 —— 授权必须精确到版本，
不能"这个草稿我批过"就长期有效。

### Q2 现有插件和 Skill 安装事务如何复用？

**复用整个事务，不要另写安装器。** 现有 P4 的两条链路已经是完整的：

| 环节 | 插件 | 技能 |
| --- | --- | --- |
| 形状校验 | `plugin_ops.inspect_zip`（只准一个 `.py` + 可选 `zxplugin.json`） | `skill_structure.check_skill_directory` |
| 静态检查（不 import） | `plugin_structure.check_plugin_directory` | `skill_structure`（`.js/.mjs/.cjs/.json/.md`、保留名、entry 层级） |
| 事务 | `plugin_ops.install` → `plugin_transaction.commit` | `plugins/skill.py:_install_zip` → `skill_transaction` |
| 提交语义 | 备份 → `os.replace` → 写注册表 → **配置并发守卫**（配置被并发改动就中止安装） | 同左（复用 `file_tx` 原语） |
| 激活 | **不 import**，只落文件 + `restart_needed=true` + `restart_state.note_change` | 同上，scope=`skill`（宿主启动时写 `started_at`） |
| 回滚 | 备份保留在 stage；回滚失败会明确报"备份保留于 …" | 同左 |
| HTTP | `POST /api/plugins/install`（zip 走 base64，`_plugin_admin` 里 **POST 必须 admin**） | `POST /api/skill/install`（`_r_install`） |

MVP-2 只做**一件事**：把已审核的草稿目录**在内存里**打成 zip，
交给 `plugin_ops.install(...)` / 技能侧同一个 `_install_zip(...)`。

**为什么在内存里打 zip 而不是直接拷目录**：
- 只有一次读取：读完 → 算摘要 → 与授权比对 → **从同一份字节**打 zip，
  压缩包里就是被审核的那份内容。校验与拷贝之间没有第二次读盘，堵掉
  TOCTOU（校验完、拷贝前被换掉）。
- 走 `install()` 意味着仍然享受它的全部纪律：形状校验、静态检查、事务、
  回滚、注册表、`restart_needed`、配置并发守卫 —— 一行都不会退化。
- 顺带避免了"草稿目录名不是最终插件名"这类映射问题（现有 `install` 已经从
  manifest 里取名，`_guess_plugin_name` 那套逻辑直接沿用）。

**明确不做**：不在事务里 `import` 生成代码；不新增"直接跑草稿"的旁路；
不因为"是本地生成的"就跳过静态检查。安装后激活仍只由受控重启完成
（与手动装 zip 完全同一条路，风险不增加）。

### Q3 Creator 安装该用什么独立授权机制？

**独立模块 + 独立存储 + 一次性令牌**，形状借 P3，对象不借 P3（见 Q4）。

`<DATA_DIR>/creator_approvals/<approval_id>.json`：

```json
{
  "approval_id": "64位hex（random 32 bytes）",
  "draft_id": "cr_xxx", "version": 3,
  "digest": "…", "kind": "plugin", "target_name": "probe_plugin",
  "files": [{"name": "probe_plugin.py", "sha256": "…", "bytes": 35}],
  "validation_ok": true,
  "approved_at": 1728.0, "expires_at": 1728.0,
  "approved_by": {"device_id": "…", "token_kind": "admin|brain"},
  "state": "pending", "used_at": 0, "install_result": {}
}
```

纪律（逐条对应要防的事）：

| 要防的 | 机制 |
| --- | --- |
| 未经审核就安装 | 没有 `approval_id` 一律拒绝；`approval_id` 只能由**人在界面上点**生成，模型侧无入口（见 Q5） |
| 重复审批 / 复用 | 一次性：`state: pending → used`，`used_at` 落盘；第二次安装命中"这条授权已经用过了" |
| 越权安装 | `approve` 与 `install` 都要求 `_is_admin()`；记下 `device_id`，非同一设备安装要显式提示（见"遗留风险"） |
| 草稿被替换 | 安装前重算摘要，与 `digest` 不符即拒绝；`revise` 后旧授权自动作废（version 变了） |
| 审批过期 | `expires_at = approved_at + 10min`；过期不装，要求重新审 |
| 并发安装 | 复用 `plugin_transaction.LOCK` 同级串行化：同一草稿的安装串行执行 |
| 审核后被环境变化 | 安装仍走 P4 的**配置并发守卫**（配置被别的请求改过 → 中止安装） |

### Q4 P3 Codex Agent Center 的审批，能真复用吗？

**不能直接复用 —— 这是实测读代码后的结论，不是想当然。**

`brain/codex_adapter.py` 的审批对象是 **Codex App Server 发来的 JSON-RPC 请求**：

1. **它的 respond 是"沿连接写回响应"**：`POST approvals/<64hex>/respond` 最终要把
   一个协议响应送回 Codex 那条 connection。Creator 安装没有这样的请求对象，
   要么写进死连接，要么命中 `agent_center_route_missing`。
2. **它的载荷形状按 method 白名单取**：`_approval_protocol_data` 只认
   `item/commandExecution/requestApproval` / `item/fileChange/requestApproval` /
   `item/tool/requestUserInput`，风险字段是 `{command, cwd, kind}`，
   `can_respond` 还要求 `command + cwd + kind=="command"` 三者齐全。
   "装一个插件"没有 command，塞不进去 —— 硬造出来的条目会被判成不可响应。
3. **绑定维度里没有内容摘要**：它绑 `(connection_id, task_id, turn_id, generation)`，
   恰好缺"防内容替换"需要的那一维。
4. **生命周期跟着连接走**：断连即 owner 校验失败/过期；而 Creator 的真实流程是
   "看草稿 → 关掉 Agent Center → 回来安装"，跨越连接生命周期。
5. **语义不同**：P3 是"这一次请求允许执行这条命令"；Creator 是"允许把这一组
   文件装进 `plugins/`，并在重启后激活"。

✅ **要复用的是它的纪律与界面模式**（这是它的价值所在）：
单次生效、明确的一次性、`owned` 才可操作、有 `expires_at`、
必须人点击、界面上把**拟执行的东西**原样展开给人看、批准不扩展到后续请求。
客户端 `agent-center.js` 的审批卡（风险原文 + 拒绝/仅本次批准 + 过期倒计时）
可以直接照搬到"安装审核卡"上。

### Q5 如何防止未经审核就执行生成代码？

结构性约束（不靠约定）：

1. **`/api/creator/*` 里永不出现 install / load / import 生成代码**（MVP-1 已确立，
   MVP-2 保持）。安装只在新端点 `/api/creator/install`。
2. **审批只能由人点**：`/api/creator/approve` 需要 admin；
   它不进 `TOOLS`、不进 `ROUTES`、不在 `clearance.py` 的任何放行清单里 ——
   工具面（模型能调的）与 HTTP 面（人/客户端调的）是两套入口，模型没有手。
3. **必须展示可核对的东西**：审核卡里给的是**文件清单 + 每个文件的 sha256 + 目标
   路径 + 是否覆盖同名插件 + 静态验证报告**，而不是模型的自我总结
   （"通过 ≠ 沙箱安全"这句提示继续留在卡上）。
4. **三次摘要核对**：审核时算 → 安装前算 → 打包前从同一份字节算。
5. **审计**：每次 approve / install / 回滚都落一条（谁、何时、哪个 digest、结果），
   存 `<DATA_DIR>/creator_approvals/_audit.jsonl`，可回看。
6. **失败不静默**：安装失败按 P4 的语义原样上报（含 `rollback: restored|failed`、
   `recovery_path`），不吞错、不假装成功。

## 3. 落地清单（MVP-2 要动的文件，仅供评估）

| 文件 | 改动 |
| --- | --- |
| `brain/creator.py` | + `content_digest()` / 逐文件摘要；`_persist` 记 `content_digest` |
| `brain/creator_install.py`（新） | 授权记录存储 + 摘要校验 + 串行化 + 调现有 `plugin_ops.install` / 技能安装 |
| `brain/server.py` | + `POST /api/creator/approve`、`POST /api/creator/install`、`GET /api/creator/approvals`（admin） |
| `client/src/js/pages.js` | 草稿卡加「审核并安装」→ 二次确认卡（文件清单 + 摘要 + 提示） |
| `brain/tests/test_creator_install.py`（新） | 见下方验收表 |

## 4. 验收标准（写死的断言，实现后必须逐条有测试）

| # | 断言 |
| --- | --- |
| 1 | 没有授权 → 安装被拒，`plugins/` 无任何变化 |
| 2 | 审核后 `revise` 改了内容 → 安装被拒，提示摘要不符（审核时 d1 / 现在 d2） |
| 3 | 同一条授权安装两次 → 第二次明确拒绝（"已经用过了"） |
| 4 | 非 admin 调 approve / install → 403 |
| 5 | 授权过期 → 拒绝，要求重审 |
| 6 | 同名目录型插件已存在 → 按现有规则拒绝跨布局覆盖，不破坏原插件 |
| 7 | 安装中途失败 → 回滚成功且备份保留；回滚失败要报出备份路径 |
| 8 | 安装成功 → `restart_needed=true` 且重启后可回看（`restart_state`） |
| 9 | 全程不 `import` 生成代码（测试用带副作用的模块探针验证） |
| 10 | 静态检查不通过 → 连授权都不给（验证失败不能批准） |
| 11 | 配置并发变更 → 安装中止，用户配置不被覆盖（P4 守卫保持有效） |
| 12 | P4 原有测试全绿（无回归） |

## 5. 遗留风险 / 需要用户拍板的地方

1. **"同一个人"怎么认定**：现在只有设备 token/brain token，没有人的身份。
   授权记录里能记 `device_id`。**要不要强制"审批的设备 = 安装的设备"？**
   强制更安全（另一台设备拿到 token 也装不了），但会挡住"手机上批准、
   电脑上装"的用法。建议：默认只记不拦，界面提示。
2. **授权有效期**：建议 10 分钟。太短（审核到安装要读代码）易过期，太长削弱"单次"的强度。
3. **覆盖已有同名插件**：P4 现在是"zip 装的时候允许覆盖同名 `.py`，但拒绝覆盖
   同名**目录**插件"。Creator 生成的草稿要不要**一律拒绝覆盖已安装的包**？
   建议：默认拒绝，要覆盖必须走"先卸载"这条明路（多一步，但可解释）。
4. **技能宿主不在线**：`_r_install` 现在要求 `host_alive()`。Creator 安装要不要
   同样硬要求宿主在线？建议：是（与现有行为一致，否则"装了但永远不生效"）。
5. **静态检查的边界**：`check_plugin_directory` 不执行代码，因此"通过"只代表形状与
   Manifest 没问题。审核界面必须继续把这句话摆出来，不能让人以为通过了就安全。
