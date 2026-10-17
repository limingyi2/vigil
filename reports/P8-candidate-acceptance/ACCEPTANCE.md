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
| 全新安装 1.1.0 候选包并启动 | **未执行** | workflow 首先在干净临时路径安装官方 1.0.2，再将 1.1.0 安装到同一路径做升级；没有单独把 1.1.0 安装到第二个空目录。runner JSON 的 `fresh_install: true` 指基线安装，不能据此认定 1.1.0 候选做过空目录安装。 |
| 从受支持旧版本升级 | **通过** | 校验官方 1.0.2 安装包（100,523,853 字节，SHA-256 `6ea65b226b11ee685ee190a9f0bd760f00bb35e515818a492f2f36ecc6970971`），再于隔离目录执行 1.0.2 → 1.1.0。 |
| 配置、聊天历史、Creator 草稿、插件、Skill、其他用户文件保留 | **通过** | runner 写入各类 sentinel，升级后逐项断言；回滚后再次断言保留。 |
| 新会话 A/B 隔离及 renderer 重载恢复 | **通过** | 已安装 UI + Fake Provider 验证 A/B 上下文不串、renderer reload 恢复。 |
| 插件安装、加载与聊天工具调用 | **通过** | 安装 `vigil_smoke`，确认插件列表加载；模型请求 `smoke_ping`，Electron 收到 `VIGIL_PLUGIN_CALL_OK`。 |
| Brain 启动失败/配对不匹配时阻断 | **通过** | 注入不匹配的 Brain manifest；客户端拒绝服务请求，随后验证恢复旧版本。 |
| 安装失败的回滚和旧安装保护 | **通过** | 损坏 checkpoint 时安装被拒绝且旧目录 hash 不变；精确回滚恢复官方 1.0.2 文件树，用户数据保持。 |
| 卸载行为与用户数据保留策略 | **未执行** | 本次 workflow 没有调用 NSIS uninstaller；不能据此确认卸载后用户数据策略。 |
| PR Python 单测 | **通过** | SHA `05ba0c7…` 的 CI JUnit：981 tests，0 failures，0 errors，10 skipped。 [JUnit artifact](https://github.com/limingyi2/vigil/actions/runs/39030289911/artifacts/12099817644)。 |
| 全仓 Python 语法检查 | **通过** | 同一 CI 的 `compileall brain pet` job 成功。 |
| CI JavaScript 逻辑回归 | **通过** | 同一 CI 的 `test-p2-stream-lifecycle.js`、`test-unified-runtime.js`、`test-public-updates.js` step 成功。 |
| 精确 SHA Electron 回归：P1 / P2 / Creator 抽屉 / 模型 / 桌面集成 | **通过** | P1 12、P2 7、P2 Creator 9、模型 5、桌面集成 9 项通过。Windows workflow 也再次通过 Creator renderer diagnostics。 |
| 精确 SHA Fake Provider Electron A/B、欢迎页、Brain 重启集成 | **通过** | `test-electron-brain-e2e.js --screenshots`：7 组通过，覆盖冷启动、10 个历史会话、A/B、Creator 草稿、renderer reload、Brain 重启和新进程冷启动。 |
| 精确 SHA `test-chat-v2-electron.js` | **部分通过** | 聊天/P6、附件、视频行内选项、弹窗、布局和 Wallpaper 布局检查均通过。最后 Wallpaper WebGL 场景 30 秒未渲染首帧而超时，见下节。 |
| 用户主力环境未被触碰 | **通过** | 没有在主力机执行安装、升级或卸载；Windows 安装生命周期测试在 GitHub 临时 runner。 |

## Wallpaper WebGL 失败记录

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

复现时聊天、P6、附件/视频和前置 Wallpaper 布局测试已通过。当前日志没有表明是 P6 会话/UI 改动导致；根因仍未定位，不能标记为通过。失败测试留下的隔离诊断目录：`C:\Users\limingyi\AppData\Local\Temp\vigil-chat-v2-dokJfV`，未清理。

## 与 P6.3 报告的变化

[P6.3 报告](P6.3-ACCEPTANCE.md) 记录的旧候选来自 `73af3af564debb7567952968634d7b012fa504aa`，大小 `101,662,065` 字节，SHA-256 `b4f47548546c334d465cf6070d85d733a62e65f6897790e51a3475f12062e7f1`。本报告的候选来自最新 PR SHA `05ba0c7544ddca2509c22e22d064bcb9745d9149`，其大小与 hash 均不同；旧候选未冒充本次产物。

本次新增了精确最新 SHA 的 Windows 安装升级/回滚候选 artifact、JUnit 记录、真实 Electron 回归和截图。全新安装候选与卸载策略仍未单独验证，Wallpaper 首帧失败仍存在。

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

## 合并建议

**当前不建议合并 PR #8。** 已通过的隔离升级/数据迁移/回滚和功能回归有较强证据，但用户要求的 1.1.0 空目录全新安装及卸载/用户数据行为尚未执行，且 Wallpaper WebGL 回归仍失败。应先补齐前两项隔离验收，并决定 WebGL 首帧问题的处理或正式豁免，再重新评估合并。
