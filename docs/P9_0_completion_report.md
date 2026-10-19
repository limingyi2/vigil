# Vigil P9.0 完成报告

日期：2026-10-19。范围：只读代码审计、扩展协议/迁移约定、Manifest Schema、假数据样例、静态测试及相关文档更正。**未修改产品业务代码；未删除/移动旧实现；未构建或发布安装包；未连接真实微信或远端运行机；未发送消息或调用模型。**

## 1. 实际基线

- GitHub `origin/dev` 与本地 `dev` 基线一致：`0a1945998f4e41d9efd588c87dcece39e56cd74a`。开始时原工作区无已暂存/已跟踪修改；存在用户未跟踪文件 `docs/P9_WALLPAPER_WECHAT_SPLIT_AUDIT.md`，始终留在原目录且未纳入本次变更。
- 为保留原工作区，本次在无分支的独立 worktree `C:\Users\26871\.codex\worktrees\vigil-p9-0-contract` 上完成；没有创建新 Git 分支。
- 独立公开项目 [`limingyi2/zhixia-wechat-bridge`](https://github.com/limingyi2/zhixia-wechat-bridge) 默认分支为 `master`，审计时 HEAD 为 `6bd975084e1379a90e3979127d3aafda4b586af5`。同名 `wxauto_bridge.py` 的远端 blob 与 Vigil 快照 blob 不同，证实代码漂移；未发现可证明的自动同步规则，也未连接其运行环境。外部 README 自身也含过期运行路径说法。

## 2. 当前源码链路与兼容基线

### Wallpaper

```text
client/src/index.html
  -> client/src/plugins/wallpaper.js / .css
  -> client/src/js/app.js: VigilWallpaper.init()
  -> client/electron/preload.js: wallpaper.request/importFile/directory
  -> client/electron/main.js: wallpaper:* IPC + wallpaper-host 启动
  -> client/electron/wallpaper-host.js
  -> client/vendor/dsh-wallpaper-engine/**
  -> Electron userData/wallpaper-engine
```

IPC 入口有限定的 host 方法，但 main-process handler 未做明确的扩展身份/sender 授权，未来扩展不能直接继承为任意 IPC。宿主监听 loopback，随机 session capability 保护受限请求。`client/package.json` 的 `electron/**`、`src/**`、vendor glob 将 Wallpaper UI/host/vendor 打入当前客户端包。Brain 另有活跃扫描的 `brain/plugins/wallpaper/__init__.py`，由 `brain/plugin_host.py` 加载并注册旧 `/api/plugin/wallpaper/...` 路由；Brain 打包 spec 收集 `brain/plugins/**`，因此当前 Core 中也包含这个旧 Python 插件。当前 Electron/Brain 打包配置和构建脚本中未发现将 `wechat-bridge/**` 或 `wxauto_bridge.py` 放入 Core 安装包的规则；Bridge 是独立交付。当前 Electron UI 未发现调用旧 Wallpaper API，外部调用方仍待查，因此不能据此删除。

### WeChat Bridge、gate 与双 Brain 转发

- Vigil 快照入口：`wechat-bridge/wxauto_bridge.py`；独立发行源码项目：上节所列公开仓库。入口运行在桥接运行机，使用 wxauto/UIA 访问已有微信会话，通过 Brain `/api/plugins` 探测和 `/api/chat` 发送文本/图片请求。当前代码携带 `wechat`/`wechat_group`；`brain/server.py` 允许 `pc`、`wechat`、`wechat_group` 并将未知渠道归一到 `wechat`。部署中的实际文件版本、配置和远端地址未核验。
- 部署/运维入口包括 `wechat-bridge/vm/` 的启动、安装和隧道脚本，以及 Vigil 的 `deploy/wechat/deploy_bridge_m4.py`、`restart_bridge_m4.py`；后两者仍含旧路径假设，本阶段未执行。
- `brain/plugins/gate.py` 是当前 Brain runtime 插件宿主使用的 gate；`wechat-bridge/brain-plugin/gate.py` 是旧副本。两者主要差异：当前版 PASS 连击阈值默认为 8 且可配置，旧副本写死 3；当前版含 chatty 档位硬闸/恢复概率、@他人预筛、flood guard、provider/model 兼容检查、桥接上下文和 persona helper 处理；旧版缺少这些行为或依赖旧内部字段。最关键的是 `brain/plugin_host.py` 的 `on_turn_in` 只认 `ctx.vetoed`，宿主文档明确空串返回不能否决；当前 gate 使用显式 `ctx.veto()`，旧副本仍返回空串作为 veto 约定，放入当前宿主时可能不阻断。不能直接覆盖/自动同步。生产部署具体用哪份 gate 未验证。
- `brain/ws_forward.py` 是 Brain 的 `/ws/brain` 服务端；`client/electron/forward.js` 是调用端实现，但当前 `client/electron/main.js` 不加载或启动它。它仍因 `electron/**` glob 被打入客户端包。双 Brain 转发是独立、可选能力，不与 Bridge 生命周期绑定。
- `client/src/js/pages.js` 有关于转发/未来插件规划的 UI 文案，与当前独立扩展规划可能冲突；本阶段仅记录，不改产品代码。

## 3. 本次文件变更

- `docs/P9_extension_protocol.md`：定义 Core、桌面扩展、渠道 Bridge 与双 Brain 转发的职责；标注现有 IPC/HTTP/WS 与新契约的区别；规定 ID、SemVer、健康状态、生命周期、RPC、错误/超时/取消、权限最小化、双协议退场条件及当前待验证边界。
- `docs/P9_data_migration.md`：规定 Wallpaper 资产范围、旧配置只读探测、幂等 staging/校验/提交和回退；规定 Bridge 登录态/凭据、渠道隔离、历史接口及失败保留策略。
- `docs/schemas/extension-manifest.schema.json`：Draft 2020-12 严格 Schema，区分 `desktop-extension`/`channel-bridge`；未知属性、类型和权限拒绝；限定 artifact/hash、协议、版本、权限和迁移字段；拒绝绝对/点段入口路径。
- `docs/examples/extensions/wallpaper.manifest.example.json`、`wechat-bridge.manifest.example.json`：假域名、假 SHA-256、无凭据样例。
- `tests/test_p9_contract_artifacts.py`：stdlib 静态 Schema 子集校验和文档/安全契约检查。
- `wechat-bridge/README.md`：纠正不存在的 `brain/wechat_bot/`、绝对化安全承诺和不确定部署状态；记录公开源码仓库与本地快照漂移及 gate 双版本关系。
- `docs/05-微信/P8.2-微信插件化迁移清单.md`：明确旧规划与当前实现的边界，防止把加密凭据库、统一插件安装器等未实现能力描述为已有。
- `docs/USER_GUIDE.md`：区分 Brain Python 插件、Wallpaper 桌面扩展、WeChat Bridge 与双 Brain 转发；说明尚未完成统一协议/生命周期。
- `docs/P9_0_completion_report.md`：本报告。

## 4. 测试结果

| 项目 | 结果 |
|---|---|
| `tests.test_p9_contract_artifacts` | 通过：7 项；含两种合法样例、未知权限/类型、缺字段、未知字段、路径穿越、类型边界、Core/协议兼容、文档路径和假凭据扫描 |
| `tests.test_gate_mention` + `tests.test_forward` | 通过：13 项；使用本机测试代码，无真实微信/远端连接 |
| `brain/tests/test_plugin_manifest.py` | 通过：7 项 |
| `brain/tests/test_plugin_transactions.py` | 通过：23 项，跳过 3 项（测试自身标记为 skip） |
| `git diff --check` | 通过 |
| JSON/Manifest 解析 | 通过（由新增测试解析）；Schema 用于本阶段关键关键词的 stdlib 子集验证，未安装/运行第三方通用 Draft 2020-12 validator |
| 全仓 Python/Node 测试、Electron 启动、真实 Bridge/Wallpaper、远端部署 | 未运行/未验证；不属于静态 P9.0 验收，且真实环境按约束禁止连接 |

首次将 Brain 子目录测试按包名导入时，`brain` 被解析为 `brain.py`，导致两项**测试发现命令**报导入错误；改用 `brain` 目录为工作目录、按 `tests` 发现后，Manifest 7 项与事务 23 项正常通过。没有修改产品代码来绕过该命令问题。

## 5. 两遍自审

**第一遍：完整性。** 协议与迁移文档覆盖生命周期、接口、权限、兼容、Wallpaper 全部已知数据根、Bridge 凭据隔离和旧调用链；Schema 和示例对应两种扩展类型；相关旧文档修订为当前事实，并把 `pages.js` 规划冲突留在报告。检查时发现 README/P8.2 把 Bridge 加密配置和现成插件 UI 支持写成事实，已改为未实现/待验证。

**第二遍：安全和回滚。** 检查未知权限拒绝、扩展入口路径穿越、签名不替代沙箱、Manifest 不授权任意文件/IPC/Brain 管理、用户资产不随禁用/卸载删除、非幂等消息不自动重放、协议 major 不降级、凭据不进示例/普通日志、旧源数据保留。发现 Manifest entry 对 `.` 路径过宽后，Schema 和回归测试已收紧。

## 6. 未解决风险与 P9.1 建议

- 必须先明确独立 Bridge 仓库和 Vigil 副本的发布/同步规则；两份源码当前已漂移。确定桥接运行机实际消费的版本及 gate 部署来源需要有权限的环境所有者后续核实。
- 旧 `brain/plugins/wallpaper` API 的外部消费者、Wallpaper IPC 的实际 sender 边界、Bridge 部署脚本旧路径都未做运行环境验证。
- 当前 Schema 的测试使用标准库子集，不等同于通用 JSON Schema 引擎验证；P9.1 应在安装器实现中采用受支持的 Draft 2020-12 validator 并加入 CI。
- Manifest 的 SHA-256 只校验文件一致性；发行签名、来源信任、进程隔离、凭据库和权限代理仍未实现。`brain/server.py` 对未知 src 的归一化也需在不破坏旧客户端时收紧。

**P9.1 最小范围建议：** 先冻结 Bridge 唯一发行源码及 gate 版本；为桌面扩展和渠道服务建立独立包/Manifest 验证入口及只读 health handshake；仅实现 `installed-disabled → enabled → disabled` 状态和 staging 回滚，不先移除内置 Wallpaper、旧 Brain API 或 `/api/chat`/`msg/ack/result`/`ws_forward`。Wallpaper 首轮仅让扩展 sidecar 托管现有 UI/host，并继续使用原 `userData/wallpaper-engine`。Bridge 首轮仍作为独立服务部署，不纳入 Core 进程。

**回滚：** 新生命周期默认关闭；更新采用版本目录 staging+原子指针切换；健康/迁移失败回切旧程序目录并保留数据；功能开关关闭扩展代理后回到原 Wallpaper 主进程实现和旧 Bridge API；在调用清单、真实数据恢复演练和多版本兼容通过前不删除任何旧代码。

## 7. 提交状态

本阶段结果已提交，提交仅含本报告列出的 10 个交付文件；随后快进到本地既有 `dev`，不推送 `origin/dev`。原工作区的未跟踪审计报告保持不变。本阶段不构建安装包、不开始 P9.1；等待用户审核。
