# Vigil P4 C1/C2/C3 修复轮验收报告（c0346e5 → 298e7c6）

- 验收日期：2026-10-12 ~ 2026-10-13
- worktree：`F:\sync-checks\creator-mode-p4-4b-2`
- 分支：`creator-mode-p4-ui-finish`（远程 `origin=limingyi2/vigil`，私有）
- 最终 HEAD：`298e7c6bad8f56981d0fd99f9ed63848125a1bd1`（`298e7c6 fix(restart): 启动时刻改到绑定成功后登记；卸载 restart_needed 只认被卸插件自身`）
- 与既有报告的关系：`REPORT.md` 记的是 **7bcc925 轮**（P4 Windows 兼容补丁首验，28/28，留下 C1/C2/C3 三个 UI 问题），保持原样未删未改，其 §七 已记录该轮的脱敏决定；**本报告只记修复轮**，两轮报告并存。

## 一、轮次总览

| 轮次 | HEAD | 跑的内容 | 28 项 E2E | C123（C1/C2/C3 专项） |
|---|---|---|---|---|
| R1 修复实现轮 | `c0346e5` | C1 文案分流 + C2 卸载消费 restart_needed + C3 待重启状态持久化 | 28/28 ✅ | **26/35**（复现 2 个真缺陷 + 7 条 harness 假失败） |
| R2 缺陷修复轮 | `298e7c6` | 修 C3-7c（note_start 时机）+ C2-4（卸载来源合并） | 28/28 ✅ | **35/35** ✅ |

R2 的 C123 与 28 项各连续跑了两轮（harness 元数据修复前后），**四轮全绿**（35/35、28/28、35/35、28/28），结果稳定可复现。

环境与旧轮完全同构：临时 `VIGIL_HOME`（`%TEMP%\vigil_p4`）、随机 40 位 hex 双 Token（brain/skill 各一把，与隔离配置配对一致）、非生产端口（Brain 18790 / Host 8791 / CDP 9357）、临时 userData、禁 sidecar；**全程未连云服务器、未用任何生产 Token/配置/Skill**。运行身份为普通用户，无提权。

## 二、最终结果明细（R2，证据 `298e7c6/` 与 `298e7c6-c123/`）

### 28 项 Electron E2E：28/28

H0 Host 启动（Bearer/回环/临时目录）· E1 Electron 连接测试 Brain · P1 路径穿越包被拒 · P2 双根结构包被拒 · P2b 非法插件未进列表 · P3 v1 提交且 toast 提示需重启 · P4 重启后运行中 · P5 更新 v2 重启后注册表 2.0.0 · P5b 落盘确为 v2 · P6 卸载后清单/注册表已清 · S0 宿主在线 · S1 entry 越界静态拒绝 · S2 无 setup 包失败回滚 · S3 v1 热装载且工具可调用 · S3b UI 列出技能与工具 · S4 v2 热重载立即生效且提示 restart_needed · S5 Host 重启后 v2 仍在 · S6a 无认证 401 · S6 错误凭据显示离线 · S7 恢复后回在线 · S8 坏包更新失败回滚到先前版本 · S8b status 清单仍为旧版 · S9 junction 技能树 reload 被拒(409) · S10 双层包自动钻层 · S11 卸载后 UI/工具/目录全清 · S12 渠道片点击即持久化 · S12b 非法渠道被拒 · E2 渲染层无严重报错。

### C123 专项（真实 Electron 渲染层 + IPC + Brain HTTP + Node Host）：35/35

- **C1 文案分流（4 条）**：插件装卸 toast 说"重启**大脑**"、技能装卸 toast 说"重启**技能宿主**"，互不冒充；C1-3 技能首装不误报需重启。
- **C2 卸载消费 restart_needed（6 条）**：技能卸载提示重启宿主；带 `start()` 的插件卸载明说"后台线程还在跑、要重启大脑才真正停止"，且有**进程级证据**（卸载后心跳文件仍在增长，大脑重启后才停）；无钩子插件卸载用普通文案（见缺陷 2）。
- **C3 持久待重启状态（24 条）**：`pending = changed_at > started_at` 时间戳判据；插件/技能双 scope 互不清除（只重启 Brain 不清技能提示、只重启 Host 不清插件提示）；整页刷新后提示仍在（来源是服务端判据不是前端变量）；两类"重启失败"（大脑端口被占、宿主非法 Token）都不虚假清除提示，界面不出"已生效"假象；全部清除后再次装卸提示复现。
- **隔离证明（1 条）**：ISO 项记录临时 HOME、40 位随机 Token、非生产端口。

## 三、R1（26/35）复现出的两个真实产品缺陷 → 已修 + 护栏测试

### 缺陷 1（C3-7c）：大脑"重启失败"会虚假清除待重启提示

- **复现**：安装插件后状态文件为待重启；占住端口让大脑重启失败——旧代码 `note_start("plugin")` 在 `run_server` 开头（HTTP 绑定**之前**）执行，而端口被占时进程只打印"没起来"然后继续跑收尾循环、**并不退出**，失败的重试把 `started_at` 推到新时刻 → `changed_at > started_at` 翻转 → 待重启提示被假清除。
- **修复**（`brain/server.py`）：`note_start` 移到 `start_remote_api` 里 `ThreadingHTTPServer` 构造成功之后——启动时刻登记的必须是"服务真的起来了的那一刻"。
- **护栏**：`tests/test_restart_prompt_wiring.py::test_start_time_is_registered_after_the_bind` 断言登记调用在绑定语句之后、且 `run_server` 调到 `start_remote_api` 之前无登记；`restart_state.py` docstring 记下这条时机约定。

### 缺陷 2（C2-4）：卸载无钩子插件被谎称"后台线程还在跑"

- **复现**：`/api/plugins/uninstall` 把"被卸插件自身需要重启"OR 进"重载后剩下的插件有 start 钩子"——reminder 等内置插件恒为真 → 卸一个没有任何钩子的插件也弹"它注册的后台线程还在跑"（谎话）。
- **修复**（`brain/server.py`）：卸载分支 `need = bool(res.get("restart_needed"))`，只认被卸插件自身（`plugin_ops.uninstall` 已在删文件前查好钩子并放进返回值——文件一没就再也问不出来）。
- **护栏**：`test_uninstall_restart_needed_comes_from_the_uninstalled_one_only` 断言卸载块只取被卸插件的字段、不再 OR 重载结果。

两个缺陷均由 R2 的 C3-7c / C2-4 转绿验证；`test_restart_prompt_wiring.py` 21 项全过。

## 四、R1 的其余 7 条失败是 harness 缺陷（非产品问题）

1. **技能 scope 整组假失败（C3-4/5a/8a/8c/8d）**：隔离 HOME 里的 `skill.py` 是旧副本（缺 c0346e5 的三处 `note_change`/`pending_restart`），技能 `changed_at` 从未写入；且 `PYTHONDONTWRITEBYTECODE=1` 下旧 `.pyc` 会被继续使用。修法：harness 增加 `syncBuiltinPlugins()`——每次清场把 worktree `brain/plugins/*.py` 同步进隔离 HOME 并删 `__pycache__`，保证"跑的哪份代码"说得清。
2. **C2-1/C2-3**：插件安装只落盘不 import，重启后客户端不自动重画能力页，卸载按钮不在旧 DOM 里，click 静默落空。修法：restart 后显式 `gotoAbility('pl')` 并等到按钮出现再点。
3. **C1-4 首跑空 toast**（更早一轮）：装卸成功触发 `ctx.refresh()`，能力页固定回「插件」tab，技能 drop 区消失。修法：相关步骤前补 `gotoAbility('sk')`。
4. **元数据采集**：worktree 的 `.git` 是指针文件且分支 ref 在 commondir（主仓库）里；裸 `spawnSync('python')` 在这台机器上恒 EBUSY。修法：commit 经指针文件 + commondir 回退纯文件读；python 版本改异步 spawn + 绝对路径候选（`LOCALAPPDATA` 展开，不硬编码用户名）。

## 五、单元测试与回归基线

- `tests/test_restart_prompt_wiring.py`：21 项全过（含两条新护栏）。
- `brain/tests` 全量：5 failed + 1 error，与已知基线一致（Windows 保留名/symlink 权限/tempfile 清理的环境问题；抽查确认失败点在 `TemporaryDirectory.cleanup()`，与本次改动无关）。

## 六、证据入库范围与脱敏说明

**凭据审查**：全部入库文件扫 40 位 hex **无命中**（仅 commit SHA）；两个 harness 的 Token 全部从 `%TEMP%/vigil_p4/root.txt` 读，无一处硬编码；未发现真实凭据泄露。

**入库**（本目录）：

- `REPORT-c0346e5.md`（本报告）、`sanitize_evidence.js`（脱敏脚本，可复跑复验）；
- `harness-p4-c123-e2e.js`、`make_pkgs_c123.py`（C123 专项 harness 与造包脚本）；
- `298e7c6/results.json`、`298e7c6-c123/results-c123.json`（R2 最终证据，元数据由 harness 自记：commit/python/node/electron 齐全，harness 文件 sha256 前 16 位与入库文件一致）；
- `c0346e5/results.json`、`c0346e5-c123/results-c123.json`（R1 证据，26/35 的失败项即第三节两个缺陷的复现；其 commit/python 为验收后补录，文件内 `metadata_note` 已注明原因与来源）。

**脱敏**：只替换本机用户名与临时目录前缀（→ `%TEMP%`/`%USERPROFILE%`），按 detail 字段 1/2/4 层反斜杠转义三种形态处理（detail 里嵌了第二层 JSON），写回后复验仍是合法 JSON、results 条数不变；端口、回环地址、包名、断言文本原样保留。处理过的文件带 `sanitize_note` 字段。

**未入库：44 张截图**（四轮各 14/8/14/8）。原因与旧轮 §七 相同且本轮再次验证：PNG 是压缩格式，二进制层扫不出文本——实测全量扫匹配串全部"干净"，而画面里带着临时目录路径，扫描给不出任何保证。截图留在本地证据目录，不入库。

## 七、是否满足 P4 集成条件

对照 `WINDOWS_P4_HANDOFF.md`「合并 master 前验收条件」：

- ✅ 分支推送且远端 SHA 核对一致——见下节（本轮执行）。
- ✅ Windows NTFS、Host/Bridge、服务重启（R2 共重启 Brain 5 次、Host 4 次）和 Electron 联动测试通过（28/28 + 35/35，四轮全绿）。
- ✅ 审查提交差异不含真实 Token、密码、私钥、临时文件或生产配置（第六节凭据审查 + 脱敏）。
- ✅ Linux Skill/插件/Host 回归：P4-4B-2 轮已通过并记录于交接文档（本机为 Windows，未重跑）。
- ⏸ P3 真人批准/拒绝端到端验收：仍是独立待办，不能用模拟测试替代。
- ⏸ Management Agent 旧 Token 轮换：管理员运维动作，不在代码验收内。

**结论：P4 代码层面满足集成条件；合并 master 仍被「P3 真人审批 E2E」与「Management Token 轮换」两个既有前置项卡住，均非本补丁缺陷。**

## 八、推送状态

按任务约束：确认 `origin=limingyi2/vigil` 后普通推送 `creator-mode-p4-ui-finish`（**不 force、不合并 master、不删分支**），推送后核对远程 SHA 与本地一致。实际执行结果见文末「执行记录」。

## 九、进入 Creator Mode MVP 尚需的最小工作

1. **P3 真人批准/拒绝的 Windows + Electron 端到端验收**（人工在客户端点批准/拒绝，记录真实 trace；不可用模拟替代）——这是合并 master 的硬前置。
2. **Management Agent 旧 Token 轮换**（管理员在中心/server-b/local-pc 生成新值→更新→负向验证旧值→清理，真实值永不入仓库）——另一个硬前置。
3. 本分支推送 + 远端 SHA 核对（本轮已完成/见执行记录）。
4. 合并 master 后按既定发版流程走客户端/APK 打包与灰度。

除上述两项运维/人工前置外，P4 的代码、测试与证据链已闭合，无未解决的缺陷。

---

## 执行记录（推送后填写）

- 推送时间 / 本地 SHA / 远程 SHA / 核对结果：
