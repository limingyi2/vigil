# Vigil 1.1.0 Windows 候选验收报告

日期：2026-10-17（Asia/Shanghai）
仓库：`limingyi2/vigil`
PR：[#8](https://github.com/limingyi2/vigil/pull/8)，状态 Open，未合并
本次候选源提交：`05ba0c7544ddca2509c22e22d064bcb9745d9149`
版本：1.1.0（code 21，`candidate=true`）

## 候选包与构建来源

- 安装包：`Vigil.Setup.1.1.0.exe`
- [下载候选包（GitHub Actions artifact 12100655832）](https://github.com/limingyi2/vigil/actions/runs/39030814929/artifacts/12100655832)；artifact 保留至 2027-01-15。
- 构建 run：[Windows unified installer acceptance #39030814929](https://github.com/limingyi2/vigil/actions/runs/39030814929)，结论 success，运行约 3 分 32 秒。
- 源提交：`05ba0c7544ddca2509c22e22d064bcb9745d9149`。下载后的 `release-manifest.json`、`windows-acceptance.json` 均与此 SHA 相符。
- 安装包大小：`101,661,668` 字节。
- 安装包 SHA-256：`89d187103c237a66df788f0bc45c0162a771428f969c30b4ee322eac5e90735c`。本地下载文件 hash 与 manifest 一致。
- Actions artifact ZIP：`101,662,130` 字节；GitHub API digest：`sha256:2f6e49316c0753bc595d21799bc45020f7f2e8957f5da8bf5b5f3d3ba12a6a4d`。
- 本地副本：`F:\vigil-p8-candidate-39030814929\Vigil.Setup.1.1.0.exe`。仅下载并核验，未在本机运行安装程序。
- 这是未发布的 Actions 候选 artifact，不是 GitHub Release 或正式自动更新包。

## 测试环境与隔离

- 安装、升级、启动故障和回滚测试在 GitHub `windows-latest` 临时 runner 上执行。测试使用临时安装目录和 profile；结果记录 `daily_install_touched: false`。
- Provider 使用本地 Fake Provider，安装验收没有真实线上模型/API Key 测试。
- 最新 PR Python/JS CI 对应同一 SHA： [tests run 39030289911](https://github.com/limingyi2/vigil/actions/runs/39030289911)。本地 Electron 复查使用精确 SHA 的 detached worktree `F:\vigil-p8-validation-05ba`，未更改原工作区未跟踪文件。

## 逐项结果

| 验收项 | 结果 | 证据/说明 |
| --- | --- | --- |
| 以 PR 最新 SHA 构建候选包并核对版本、提交、体积、SHA-256 | **通过** | 见上方 manifest、下载 artifact 与 SHA。 |
| 全新安装 1.1.0 候选包并启动 | **通过（P8 补测）** | 见下方 P8.1 补测；在 GitHub 临时 Windows runner 的空安装目录和隔离 profile 中直接安装精确候选包。 |
| 从受支持旧版本升级 | **通过** | 校验官方 1.0.2 安装包（100,523,853 字节，SHA-256 `6ea65b226b11ee685ee190a9f0bd760f00bb35e515818a492f2f36ecc6970971`），再于隔离目录执行 1.0.2 → 1.1.0。 |
| 配置、聊天历史、Creator 草稿、插件、Skill、其他用户文件保留 | **通过** | runner 写入各类 sentinel，升级后逐项断言；回滚后再次断言保留。 |
| 新会话 A/B 隔离及 renderer 重载恢复 | **通过** | 已安装 UI + Fake Provider 验证 A/B 上下文不串、renderer reload 恢复。 |
| 插件安装、加载与聊天工具调用 | **通过** | 安装 `vigil_smoke`，确认插件列表加载；模型请求 `smoke_ping`，Electron 收到 `VIGIL_PLUGIN_CALL_OK`。 |
| Brain 启动失败/配对不匹配时阻断 | **通过** | 注入不匹配的 Brain manifest；客户端拒绝服务请求，随后验证恢复旧版本。 |
| 安装失败的回滚和旧安装保护 | **通过** | 损坏 checkpoint 时安装被拒绝且旧目录 hash 不变；精确回滚恢复官方 1.0.2 文件树，用户数据保持。 |
| 卸载行为与用户数据保留策略 | **通过（P8 补测）** | 实际运行候选安装目录中的 `Uninstall Vigil.exe /S`，程序文件已移除；配置、聊天、插件、Skill、Creator 草稿及安装目录外 sentinel 均保留。 |
| PR Python 单测 | **通过** | SHA `05ba0c7…` 的 CI JUnit：981 tests，0 failures，0 errors，10 skipped。 [JUnit artifact](https://github.com/limingyi2/vigil/actions/runs/39030289911/artifacts/12099817644)。 |
| 全仓 Python 语法检查 | **通过** | 同一 CI 的 `compileall brain pet` job 成功。 |
| CI JavaScript 逻辑回归 | **通过** | 同一 CI 的 `test-p2-stream-lifecycle.js`、`test-unified-runtime.js`、`test-public-updates.js` step 成功。 |
| 精确 SHA Electron 回归：P1 / P2 / Creator 抽屉 / 模型 / 桌面集成 | **通过** | P1 12、P2 7、P2 Creator 9、模型 5、桌面集成 9 项通过。Windows workflow 也再次通过 Creator renderer diagnostics。 |
| 精确 SHA Fake Provider Electron A/B、欢迎页、Brain 重启集成 | **通过** | `test-electron-brain-e2e.js --screenshots`：7 组通过，覆盖冷启动、10 个历史会话、A/B、Creator 草稿、renderer reload、Brain 重启和新进程冷启动。 |
| 精确 SHA `test-chat-v2-electron.js` | **部分通过** | 聊天/P6、附件、视频行内选项、弹窗、布局和 Wallpaper 布局检查均通过。最后 Wallpaper WebGL 场景 30 秒未渲染首帧而超时，见下节。 |
| 用户主力环境未被触碰 | **通过** | 没有在主力机执行安装、升级或卸载；Windows 安装生命周期测试在 GitHub 临时 runner。 |

## Wallpaper WebGL 首帧问题

在精确 SHA `05ba0c7544ddca2509c22e22d064bcb9745d9149` 上复跑：

```text
$env:NODE_PATH='F:\zhixia\client\node_modules'; npm exec --prefix F:\zhixia\client -- electron F:\vigil-p8-validation-05ba\client\tools\test-chat-v2-electron.js
```

测试在选中 Wallpaper fixture `990003` 后等待 30 秒，最终退出码 1：

```text
Timeout: document.querySelector('[data-status]')?.textContent==='WebWallGL 实时渲染中'
status = WebWallGL 正在准备首帧
error = (empty)
hidden = false; focused = true
iframeLoaded = null; webError = null; fps = 0
```

复现时聊天、P6、附件/视频和前置 Wallpaper 布局测试已通过。详细 HTTP/renderer 诊断、基线对照和限制见 [Wallpaper 调查记录](evidence/wallpaper-webgl-investigation.txt)；原始 host trace 另存于 [wallpaper-host-http.jsonl](evidence/wallpaper-host-http.jsonl)。

**集成场景首帧问题仍未解决。** Base `808684a` 与候选的 Wallpaper adapter、host 和 vendored renderer 无代码差异；两者的独立硬件渲染对照均通过，但未能解释为何完整 `test-chat-v2-electron.js` 流程只在末尾超时。原诊断目录 `C:\Users\limingyi\AppData\Local\Temp\vigil-chat-v2-dokJfV` 保留未清理。

## P8.1 缺口补测（2026-10-17）

最终补测工作流：[run 39035232427](https://github.com/limingyi2/vigil/actions/runs/39035232427)，运行于 GitHub Actions 临时 Windows runner。工作流直接下载 run `39030814929` 的不可变候选 artifact；没有重建或替换候选安装包。补测脚本/工作流提交为 `1e1d08de06d5c3a5ff6c7f05dadf6996118e7b30`，被测安装包仍精确来自 `05ba0c7544ddca2509c22e22d064bcb9745d9149`。

- **通过：独立全新安装。** 安装程序在空临时目录以退出码 0 完成；应用和 Brain 核心文件 hash 与 contents manifest 相符；Electron 启动、Brain `/api/runtime` 健康检查、欢迎首页及 Fake Provider 首条消息均通过。
- **通过：重启恢复。** 结束并重新启动已安装的 Electron 进程后，会话列表及消息正文恢复。此项使用本地 Fake Provider，不代表在线模型验证。
- **通过：卸载和数据边界。** NSIS 卸载程序退出码 0，应用安装目录无残留文件；隔离 profile 中 62 个文件在卸载前后整树 SHA-256 完全相同，覆盖模型配置、会话 SQLite 元数据、Renderer 本地数据、插件、Skill、Creator 草稿及聊天记录；安装目录之外的 sentinel 未被删除。产品 `nsis` 配置未启用卸载时删除用户数据的选项；本补测没有改变该策略。
- **通过：候选包身份。** `Vigil.Setup.1.1.0.exe` 仍为 101,661,668 字节，SHA-256 `89d187103c237a66df788f0bc45c0162a771428f969c30b4ee322eac5e90735c`；补测下载文件与原候选文件 hash 一致。没有生成新的安装包。
- **日志限制：** 候选安装器接受 `/LOG=<path>` 参数且安装/卸载均返回 0，但没有生成 NSIS 详细日志文件。已保存命令行、进程退出码和 stdout/stderr（为空）JSON，以及 Electron/Brain 首次启动和重启日志；最终结果明确记录 NSIS 详细日志不可用。
- **证据：** [补测结果 JSON](evidence/windows-lifecycle-acceptance.json)、[安装命令与退出码](evidence/lifecycle-logs/install.json)、[首次启动日志](evidence/lifecycle-logs/first_launch.json)、[重启恢复日志](evidence/lifecycle-logs/restart.json)、[卸载命令与退出码](evidence/lifecycle-logs/uninstall.json)。完整 runner artifact：[12102061894](https://github.com/limingyi2/vigil/actions/runs/39035232427/artifacts/12102061894)，ZIP 101,664,861 字节，digest `sha256:f186799729bd5b98e6d8a6e3ee14a7f97eb46f1eef34ced190ff0c68c737704c`。该 artifact 包含与原候选逐字节相同的安装包及补测日志。

### 本轮状态

| 缺口 | 状态 | 结论 |
| --- | --- | --- |
| 空目录直接全新安装 1.1.0 | **通过** | Electron、Brain、欢迎页、Fake Provider 聊天通过。 |
| 退出并重启后恢复聊天 | **通过** | 真实安装版 UI 中恢复了补测创建的 conversation 和消息。 |
| 实际卸载和用户数据保护 | **通过** | 程序文件移除，所有指定用户数据及目录外 sentinel 保留。 |
| Wallpaper 集成回归首帧 | **未解决** | 独立 Wallpaper 控制测试在基线/候选均通过；完整聊天回归仍在末尾超时，不能据此判为通过，也没有删除或跳过该断言。 |

### 验收器迭代记录

下列失败是补测脚本探针的逐步修正，不是候选安装/卸载失败；最终版本在 [run 39035232427](https://github.com/limingyi2/vigil/actions/runs/39035232427) 通过：[run 39033053887](https://github.com/limingyi2/vigil/actions/runs/39033053887) 发现重启后会话行需等待 DOM 渲染；[runs 39033503802](https://github.com/limingyi2/vigil/actions/runs/39033503802)、[39033652411](https://github.com/limingyi2/vigil/actions/runs/39033652411)、[39033895120](https://github.com/limingyi2/vigil/actions/runs/39033895120) 发现对话落盘位置不能按猜测的 JSONL 文件名定位，改为用 Brain 会话 API 验证并对完整 userData 文件树做卸载前后 SHA-256 对比；[run 39034094278](https://github.com/limingyi2/vigil/actions/runs/39034094278) 暴露默认 Electron userData 路径不适合作为独立 DevTools 探针，改为在临时 `%APPDATA%/vigil-client` 下指定隔离 userData；[run 39034560746](https://github.com/limingyi2/vigil/actions/runs/39034560746) 发现 SQLite 内部字段断言比公开会话 API 语义更窄，改为断言会话 API 返回成功的历史 turn；[run 39034929601](https://github.com/limingyi2/vigil/actions/runs/39034929601) 确认候选 NSIS 包不会输出 `/LOG` 详细文件，实际安装/卸载仍成功，最终将日志限制明确记录而不把它误报成安装失败。

本轮没有改动产品运行时代码；只增加候选验收脚本/工作流和报告证据，因此没有重跑与本轮无关的 Python、Electron 全量回归。

## P8.2 Wallpaper WebGL 集成超时调查（2026-10-17）

调查针对用户指定的 PR HEAD `193f297234a36a91ef61a4dedd63d4564df41c87`。诊断提交只改 Electron 验收测试与可选 Windows Actions 诊断步骤；工作流在 runner 上核对 `client/src`、`client/electron`、`client/vendor` 与该提交逐文件一致。未改 Wallpaper 产品运行时代码，未重建候选安装包。有效对照为 [Actions run 39038766526](https://github.com/limingyi2/vigil/actions/runs/39038766526)，诊断提交 `cac6f0b20ac76ee65cb2e550ae37686a88a56a12`；[原始 artifact 12103092295](https://github.com/limingyi2/vigil/actions/runs/39038766526/artifacts/12103092295)。

### 复现命令与环境

Actions 手动复现（临时 `windows-latest` runner；不执行 installer job）：

```powershell
gh workflow run windows-package.yml --ref codex/p6-multisession-dialogs `
  -f wallpaper_diagnostics=true `
  -f candidate_source_commit=193f297234a36a91ef61a4dedd63d4564df41c87
```

核心场景由该工作流运行 `npm exec -- electron tools/test-chat-v2-electron.js`，并分别打开 `P8_WALLPAPER_ONLY=1`、`P8_WALLPAPER_RESIZE_ONLY=1` 或 `P8_WALLPAPER_LAYOUT_ONLY=1` 做对照；另运行一次不启用诊断插桩的完整流程及 `test-wallpaper-v2-electron.js`。为适配 runner 的 1024×720 viewport，诊断仅跳过测试最前面的 860px 固定布局断言；原有 Wallpaper 30 秒首帧断言保留。独立 Wallpaper 用例仅跳过两个已知且与 Wallpaper 无关的 composer/input paint 断言，以便运行到 WebGL 检查。

环境为 `windows-latest`、Electron 28.3.3、Chromium 120.0.6099.291。Electron GPU features 报告 `webgl=unavailable_software`；实际 renderer 是 ANGLE SwiftShader（无硬件 GPU），不能将这轮结果称为 AMD/Direct3D 硬件测试。完整结果索引与本地 raw logs 见 [P8.2 diagnostics README](evidence/wallpaper-diagnostics-39038766526/README.md)；原始 Actions artifact 也保留所有截图和 runner 日志。此前探针转义错误的 run [39038332839](https://github.com/limingyi2/vigil/actions/runs/39038332839) 保留在 Actions 中，不能用于判断产品结果；修正的提交 `cac6f0b` 重新跑了整个矩阵。

### 对照结果

| 场景 | 结果 | 观察 |
| --- | --- | --- |
| 完整 `test-chat-v2-electron.js`（诊断开启） | **Wallpaper 子检查通过；整套测试后续失败** | 选中 fixture `990003` 后 iframe 创建、导航、load 完成；scene-live、`vigil-control.js`、renderer JS、`project.json` 与 `scene.json` 请求成功。WebGL2 创建成功，首个 RAF 到达；父页面收到 `state.running=true`，`data-status` 由“正在准备首帧”变为“实时渲染中”。之后整套脚本在独立的 `#retryHistory` 等待处超时。 |
| 完整 `test-chat-v2-electron.js`（无诊断插桩） | **Wallpaper 首帧失败** | 在同一 runner、同一产品运行时代码上再次于“正在准备首帧”处超时；没有更早失败拦截 Wallpaper 场景。 |
| 聊天 harness 中只运行 Wallpaper 场景 | **通过** | 首帧断言通过；没有执行先前聊天/布局场景。 |
| 单独窗口 resize 后运行 Wallpaper 场景 | **通过** | 首帧断言通过。 |
| 仅 Wallpaper 布局设置/尺寸场景后运行 Wallpaper | **通过** | 首帧断言通过。 |
| standalone Wallpaper 流程 | **通过** | 生成 fixture 视频，原有 WebGL 场景首帧断言通过；仅绕过不相关 paint 断言。GPU 仍是 SwiftShader 软件渲染。 |

同一 runner 的无插桩完整流程超时、而诊断完整流程和三种更窄场景通过，说明结果对完整流程/执行时序敏感；resize 与 Wallpaper layout 单独执行未复现前置场景残留。成功轨迹中 iframe 正常加载，关键资源均为 HTTP 200，WebGL2 context 创建成功，父子 `postMessage` 和 `data-status` 更新均发生，没有相关网络失败或 renderer exception。timeout 控制的状态快照则可能是 `running=false/fps=0`，并非明确的宿主消息丢失；fixture 本身为 0 层空场景，renderer 日志中的 `0 layers`、`0 canvasCandidates` 符合该 fixture，不单独构成失败原因。软件渲染路径多次记录 ReadPixels stall。原始失败保存在前述 P8.1 报告与 [run 39037805159](https://github.com/limingyi2/vigil/actions/runs/39037805159)；本轮成功/失败对照保存于 run 39038766526。

### 根因判断与剩余验证

**未完全定位；现有证据更支持 headless SwiftShader 环境中的执行时序/渲染调度敏感性，尚不能排除完整测试的竞争条件，也没有证据证明这是候选产品代码缺陷。** 关键证据是同一产品提交的 isolated、resize-only、layout-only、standalone 都能通过，且插桩完整流程也能通过首帧；相反，无插桩完整流程能再次失败。插桩会改变 renderer/CDP 的执行时序，因此这一差异不足以单独区分测试竞态与软件 GPU 运行时行为。前置 resize/layout 单项通过也不能完全排除真实完整前序场景组合造成的资源竞争。

本轮未能在同一 PR HEAD 的真实 AMD/Direct3D runner 上复跑原先的失败，因此**根因保持未解决**。也没有修产品代码或延长/移除超时断言。下一步最小实验是在可重复且固定版本的 AMD 硬件 runner 上，对当前精确提交连续交替跑“完整流程、完整流程无插桩、Wallpaper-only”，同时保留现有事件 trace；若硬件 runner 无法取得，则在 SwiftShader runner 上进一步将完整流程按前序场景二分，找出触发 timeout 的最小序列，并与 timeout 时 GPU/context 状态对照。

这一轮只运行上述 WebGL 对照，没有重跑安装生命周期或无关 P6 全量测试。没有候选安装包产物变更；没有合并 PR、发布版本或修改 master。

## 与 P6.3 报告的变化

[P6.3 报告](../P6.3-ACCEPTANCE.md) 记录的旧候选来自 `73af3af564debb7567952968634d7b012fa504aa`，大小 `101,662,065` 字节，SHA-256 `b4f47548546c334d465cf6070d85d733a62e65f6897790e51a3475f12062e7f1`。本报告使用的候选包源提交为 `05ba0c7544ddca2509c22e22d064bcb9745d9149`，其大小与 hash 均不同；旧候选未冒充本次产物。验收脚本和报告提交在该候选源提交之后，不改变候选包内容。

本次新增了候选安装升级/回滚 artifact、真实 Electron 回归，以及对同一候选包进行的隔离空目录安装、卸载和数据保留复测。安装生命周期缺口已关闭；Wallpaper 集成首帧问题仍未解决。

### P8.2 后续：上游 WebWallGL 候选原因对比（2026-10-17）

对 Vigil vendored WebWallGL 2.1.0 与 DSH 上游当前使用的 3.0.0 做了定向源码比较。未发现零图层场景首帧完成条件的明确修复，因此未创建实验分支、替换引擎或运行无依据的 A/B。Wallpaper 集成超时仍未定位；建议先作为 SwiftShader 完整集成测试的已知限制记录并转向其他功能。详见[对比报告](WALLPAPER-UPSTREAM-CANDIDATE-CHECK.md)。

## 截图与原始证据

- [真实 Electron 冷启动欢迎页](evidence/p6.3-cold-start-home.png)
- [有 10 个历史会话的欢迎页](evidence/p6.3-ten-history-home.png)
- [新对话空白页](evidence/p6.3-new-conversation-home.png)
- [renderer/Brain 重启后欢迎页](evidence/p6.3-restart-home-with-history.png)
- [Windows runner 原始验收结果](evidence/windows-acceptance.json)
- [候选 manifest](evidence/release-manifest.json)
- [候选安装内容 hash 清单](evidence/Vigil.Setup.1.1.0.contents.json)
- [Python CI JUnit 原始结果](evidence/python-test-results.xml)
- [Wallpaper WebGL 超时日志与复现方式](evidence/wallpaper-webgl-timeout.txt)
- [Wallpaper WebGL 基线对照与根因调查](evidence/wallpaper-webgl-investigation.txt)
- [独立全新安装及卸载验收 JSON](evidence/windows-lifecycle-acceptance.json)
- [隔离 runner 安装/运行/卸载日志](evidence/lifecycle-logs/)

## 合并建议

**目前仍不建议合并 PR #8。** 本轮已补齐空目录全新安装、重启恢复和卸载数据保护；安装生命周期验收缺口关闭。完整 Wallpaper 集成回归的首帧超时仍未解决，应继续定位或由人工决定是否接受该已隔离的测试问题，再重新评估合并。
