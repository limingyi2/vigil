# Vigil P4 Windows 兼容补丁 — Electron + Brain + Skill Host 真实联动验收报告

- 验收日期：2026-10-12
- worktree：`F:\sync-checks\creator-mode-p4-4b-2`
- 分支：`creator-mode-p4-windows-fixes`
- HEAD（实际）：`7bcc925a52f213ed36a44a4573d6299451fcdd4c`（`7bcc925 fix(windows): clean failed skill installs and harden tests`）
  - 注：任务书给定 `7bcc925c6f4e4d1c8e7e1c6c0a1b0a4b1f9c2f4a` 在仓库中不存在（`git cat-file` 报错），短前缀 7bcc925 与实际 HEAD 一致，判定为任务书笔误，验收按实际 HEAD 执行。
- 工作区状态：验收前 clean；验收后仅新增本证据目录（未跟踪、未提交）。
- 运行身份：**普通用户（非管理员）**，全程无提权。

## 一、阶段 1：环境检查

| 项 | 结果 |
|---|---|
| 包管理器 | npm（存在 `client/package-lock.json`，无 yarn/pnpm lock） |
| Node / npm | v24.21.0 / 11.19.0（系统安装） |
| Electron | 28.3.3 已落盘（`node_modules/electron/dist/electron.exe`），ws 8.22.0 已落盘 |
| 是否执行安装 | **未执行 npm install**：依赖完整，无网络/第三方脚本暴露；`keytar` 目录为既有 `keytar.e2e-disabled` 规避态，主进程对 keytar 缺失有文件兜底，不影响启动 |
| 磁盘 | F: 剩余 324 GB |

## 二、阶段 2：隔离联动环境

- 临时根：`%TEMP%\vigil_p4\`（home / skills / pkgs / profile / logs），验收后随临时目录可弃；
- Brain：临时 `VIGIL_HOME`、端口 **18787→实际 18790**（避开既有服务）、随机 40 位 hex brain_token；
  最小配置（假模型地址、无 sync/management/proactive、空人设模板），**未使用任何生产 Token/配置/Skill**；
  内建 .py 插件复制到隔离 HOME（与生产 ROOT 布局一致）；
- Skill Host：端口 **8791**，`ZX_SKILL_DIR` 指向临时 skills 目录，随机 40 位 hex Bearer Token；
- Electron 28.3.3：临时 userData、禁 sidecar、CDP 9356，渲染层真实 preload/IPC；
- 全程未连云服务器 A/B（sync/management 关闭）。

启动安全拒绝（真实进程，exit=1，日志 `logs/host.log`）：

1. 缺 `ZX_SKILL_AUTH_TOKEN` → `配置拒绝启动：ZX_SKILL_AUTH_TOKEN 缺失或长度不足（至少 32 个字符）`；
2. `ZX_SKILL_HOST=0.0.0.0` → `配置拒绝启动：ZX_SKILL_HOST 只允许 127.0.0.1 或 ::1`。

鉴权三态（真实 HTTP）：无 Authorization **401**、错误 Bearer **401**、正确 Bearer **200**；
拿 brain_token 打 Host **401**（双凭据不互通）；Brain 管理面无 token **401**。

## 三、阶段 3：真实 UI 联动（28/28 通过）

通过 CDP 驱动**真实渲染层**（DataTransfer 构造真实 drop 事件 → FileReader → IPC → Brain 真实路由 → Host 真实 reload），无 API mock、无 fixture。

### 插件（能力与扩展 → 插件）

| 编号 | 场景 | 结果（真实 toast/状态） |
|---|---|---|
| P1 | 路径穿越 zip（`../escape.py`） | 拒绝：`zip 里不许用 .. 跳目录`，不进列表 |
| P2/P2b | 双 .py 根结构包 | 拒绝：`包里塞了 2 个 .py，插件只能是单文件`，不进列表 |
| P3 | 安装 v1 | toast `已装上（共 11 个）· 有后台线程的插件要重启大脑才完全生效`；注册表 1.0.0、落盘 P4PONG-V1 |
| P4 | **重启 Brain 后生效** | p4plug 卡片「运行中」、无装载错误、工具 p4ping 注册 |
| P5/P5b | 更新 v2 + 重启 | toast 重启提示；注册表 **2.0.0**、运行中；落盘 P4PONG-V2 |
| P6 | 卸载 | 文件与注册表项移除，清单消失 |

### Skill（能力与扩展 → 技能）

| 编号 | 场景 | 结果 |
|---|---|---|
| S0 | 宿主在线 UI | 绿标「可以装」，显示 host/Node v24.21.0/临时技能目录 |
| S1 | entry 越界包（`../../evil.js`） | toast `路径不能包含空段、. 或 ..`，目录不落地 |
| S2 | 无 setup/register 包 | toast `Skill install 事务失败（host_reload: HTTP Error 409）；旧版本已恢复`，回滚后目录不留存 |
| S3/S3b | 安装 v1 热重载 | toast 已装上；Bearer 直调 `/skills/call` 返回 `P4-SKILL-V1`；UI 列出 p4skill v1.0.0 与工具 chip `p4skill__echo` |
| S4 | 更新 v2 | toast 含重启提示（restart_needed=true）；**不重启**热重载即生效，调用返回 `P4-SKILL-V2` |
| S5 | **重启 Host 进程** | v2 持久生效（skills=1，call=P4-SKILL-V2） |
| S6/S6a/S7 | 错误 Token 重启 Host | HTTP 无认证仍 401；UI 红标「宿主没起来，装了不生效」+ host「（没连上）」+0 个；正确 Token 恢复后回绿标 |
| S8/S8b | v2 语法错误更新 | reload 409 → toast 报错「旧版本已恢复」；运行时回滚到**先前版本 v2.0.0**（call 仍 P4-SKILL-V2），清单无 broken 条目 |
| S9 | NTFS junction 越界 | 在技能根建指外 junction，`POST /reload` 真实返回 **409**：`不允许符号链接或 junction：...eviljunction`；删除后 reload 恢复 |
| S10 | 双层同名目录包 | Host 自动钻层装载，call=`P4-SKILL-DOUBLE` |
| S11 | 卸载 | UI 条目消失、Host 工具消失（`没有这个技能工具：p4skill__echo`）、目录删除 |
| S12/S12b | 渠道片 UI | 点「微信」片即持久化（wechat=false、pc/微信群保持 true）；伪造渠道 `app` 被服务端拒绝「不认识的渠道：app」 |

对应任务书九项要求：1（插件装卸反馈）✅ 2（Skill 装卸反馈）✅ 3（非法包错误提示）✅
4（安装失败/回滚显示，S2/S8）✅ 5（Host 无/错/正确认证 UI 状态）✅ 6（restart_needed 显示）✅
7（重启 Host/Brain 后新版本生效，P4/P5/S5）✅ 8（已有插件与 Skill 列表/调用）✅
9（无法执行项见下节「未验证/受限项」）。

## 四、阶段 4：回归测试与证据

- Python（`PYTHONPATH=brain`，系统 Python 3.13）：
  - Skill 套件 discover（structure/static_install/transactions/host_security/host_template/windows_compat）：**36 通过，3 跳过**；
  - 插件事务 **21 通过**、Manifest **7 通过**、结构 **9 通过**；
  - `node --check brain/skill_host/server.js` 通过；相关 5 个 Python 模块 `py_compile` 通过。
- 3 个跳过均为环境原因，非缺陷：
  - symlink 创建 ×2：普通用户无 `SeCreateSymbolicLinkPrivilege`（OSError）；Windows 等价的 **junction 越界已由 S9 真实链路覆盖**（Node 对 junction 同样报 SymbolicLink 拒绝）；
  - mkfifo ×1：POSIX 专属，Windows 不适用。
- Windows 保留名（CON/PRN/AUX/NUL/COM1/LPT1）、尾随点空格、大小写冲突由
  `test_windows_skill_compat.py` / `test_skill_structure.py` 单测覆盖（随 36 项一起通过）。
- 证据（全部为本次真实运行产物）：
  - 截图 14 张：`docs/p4-windows-evidence/*.png`（09 截图同时录下成功 toast 与两条失败/回滚 toast；11 为错误凭据红标态）；
  - 机读结果：`results.json`（28 条逐条 detail）；
  - 日志：`logs/`（brain/host/electron 全程）；
  - 可复跑脚本与造包脚本：`harness-p4-e2e.js`、`make_pkgs.py`。

## 五、失败/跳过/未验证项（如实记录）

1. **未改代码即全部通过**，没有阻断性兼容性缺陷。
2. 左下角设备态显示「未连接」：本次未做设备注册（remote/poll 通道），管理面全部走 Bearer token 且功能正常；设备轮询联动不在本次验收范围。
3. P3 审批策略：未做任何改动，也未做真人批准/拒绝 E2E（交接文档本就将其列为独立待办）。
4. symlink 单测因普通用户权限跳过（junction 真链路已覆盖，见 S9）。
5. 最小非阻塞问题（建议后续顺手修，不影响合并判定的安全语义）：
   - **C1（UI 文案）**：Skill 安装/更新成功的 toast 复用插件文案「有后台线程的**插件**要重启大脑才完全生效」，Skill 场景应写「插件/技能」。
   - **C2（UI 漏提示）**：`/api/plugins/uninstall` 响应里带 `restart_needed`，但前端卸载回调未消费、无 toast；插件卸载后运行态实际要等重启才退出，用户无感知。
   - **C3（提示持久性）**：restart_needed 仅存在于 2.6 秒 toast，列表页无持久「需重启」角标，刷新后看不到（重启前插件卡片本身也没有待重启态展示）。

## 六、是否达到合并 master 条件

**代码层面：P4 Windows 兼容补丁在本机通过验收，未发现阻断合并的代码缺陷。**
对照 `WINDOWS_P4_HANDOFF.md` 合并清单：

- ✅ Windows NTFS（os.replace/备份恢复由事务单测覆盖、junction 真链路拒绝、保留名单测）；
- ✅ Host/Python Bridge Bearer 认证、回环绑定、缺凭据拒绝启动；
- ✅ 服务重启（Brain 2 次、Host 3 次）后新版本实际生效；
- ✅ Electron 真实联动（非模拟）；
- ✅ P3 审批策略与权限隔离零改动；本次 diff 为空（仅证据目录未跟踪）；
- ⏸ 交接清单中的「分支已推送且远端 SHA 核对」：按本次任务约束**未推送、未合并**，留待你审核后执行；
- ⏸ P3 真人批准/拒绝 Windows+Electron E2E：独立待办，仍未验证；
- ⏸ Management Agent 旧 Token 轮换：管理员运维动作，不在代码验收内。

结论：**P4 Windows 补丁本身可合并；master 合并仍被「P3 真人审批 E2E」「Management Token 轮换」「按窗口推送并核对远端 SHA」三个既有前置项卡住，均非本次补丁缺陷。**

已按要求停止，等待审核。未推送 GitHub、未合并 master、未改 P3 策略、未改 Management Token、未做 Creator Mode 新功能开发。

---

## 七、证据入库范围与脱敏说明（2026-10-12，C1/C2/C3 修复轮追加）

上面第四、五节记的是当时的原始状态。本轮把证据整理入库时，审查结果与处置如下。

**凭据审查（先查这个）**：全目录扫 40 位 hex **无命中**；`harness-p4-e2e.js` 的
`brain_token` / `skill_token` 全部从 `%TEMP%/vigil_p4/root.txt` 读，没有任何一处硬编码。
本轮修复也没有引入新的凭据写入路径。**未发现真实凭据泄露。**

**入库**：`REPORT.md`、`harness-p4-e2e.js`、`make_pkgs.py`、脱敏后的 `logs/*.log` 与 `results.json`。

**未入库：14 张截图。** 原因不是"没审"，而是**审不干净**——截图 07（技能宿主在线）
画面上带着本机用户的临时目录 `C:\Users\<用户名>\AppData\Local\Temp\vigil_p4\skills`。
PNG 是压缩格式，二进制层扫不出文本：实测对 14 张全量扫匹配串，结果 14 张全是"干净"，
而 07 肉眼一看就有。也就是说**这个扫描给不出任何保证**，不能用它的结果说服自己另外 13 张也没问题。
要入库只有两条路——逐张肉眼过（14 张，成本高且仍可能漏），或重打一遍脱敏环境的截图。
两条都没做，截图继续留在本地证据目录，不入库。

**脱敏范围**：只替换本机用户名与临时目录前缀（→ `%TEMP%` / `%USERPROFILE%`）。
端口、回环地址、包名、断言文本全部原样保留，否则证据就失去核对价值。
`results.json` 的 `detail` 字段里嵌了第二层 JSON，替换按 1/2/4 层反斜杠转义三种形态都处理，
写回后仍是合法 JSON（28 条 results 不变，已复验）。
