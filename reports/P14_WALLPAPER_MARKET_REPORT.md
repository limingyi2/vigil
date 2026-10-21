# P14 第二轮：Wallpaper 生命周期、市场诊断与 WebWallGL

日期：2026-10-21（Asia/Shanghai）
工作分支：`codex/p14-wallpaper-market`
原始实现基线：`0e24afe7c09fd561b7fd1d5b1d79a9000632f51b`
本轮 hotfix 起点：`41eb1e762c6568b37488d83518fdc9794012f218`
本轮实现提交：构建后补入；历史记录见下方“第二轮续修”。
状态：生命周期、市场和聊天 E2E 通过；复杂授权 Scene、导入与播放列表仍未完成。

## 结果概览

本轮将 Wallpaper 插件做成显式可注销的动态贡献；接入固定版本 `webwallgl@3.0.0`，经 Electron 沙箱真实挂载并播放动态 Scene；补齐市场诊断分类和导出入口；完成远程插件 3.1.0 制品与独立发布分支。

验收矩阵：

| 项目 | 状态 | 证据 |
|---|---|---|
| A 未安装不显示入口 | PASS | 隔离 Electron 市场测试检查启动状态与页面列表 |
| B 安装、禁用、卸载、重启正确更新 | PASS | `test-plugin-market-electron.js` 覆盖安装启用、禁用、卸载和重启；禁用清除页面、样式、Wallpaper DOM，保留 localStorage 导航数据且重启不复活旧页 |
| C 诊断真实市场连接 | PASS | Electron renderer→preload/IPC→Brain `/api/market/list`→GitHub Raw 实际请求成功；诊断分类和错误状态由 Brain 返回 |
| D 至少一个 Scene 实时播放 | PASS | 真实 WebWallGL `mount()` 载入 loose Scene，canvas 两帧 SHA-256 不同；场景工程由隔离 fixture 提供，project/scene 通过认证媒体桥取得 |
| E 聊天与插件安全隔离 | PARTIAL | contextIsolation/sandbox 保持，CSP 只允许本地回环媒体连接，媒体路径受 Brain containment 验证；全量既有聊天 E2E 因其读取旧 `brain/config/config.json` 路径失败，未能形成聊天通过证据 |
| F Electron 集成测试 | PASS（核心场景） | 壁纸、市场生命周期和实时市场连接 Electron 测试通过；既有聊天 E2E 单列 20/26 结果 |

## 实际链路与修改

### 插件生命周期和错误入口

Brain 插件扫描继续从运行时插件目录加载插件入口及 frontend 资源；`/api/plugins/frontend` 的安装/启用状态决定 Electron 前端注入内容。Renderer 以 fingerprint 对照注入状态，在变化时先注销旧 owner，再注入当前启用插件。

`VigilPlugin` 现有页面 API 增加 owner 生命周期：插件注册的页面、CSS、清理回调及事件监听器均有 owner；注销时运行清理、移除页面和样式，并在当前页被移除时返回安全页面。壁纸清理销毁 WebWallGL、断开 ResizeObserver、移除 theme/visibility/引擎监听器、背景和遮罩 DOM、CSS 变量与媒体票据。市场测试还验证 localStorage 中旧导航顺序不会重新创建已卸载页面。

### 市场连接诊断

在 Renderer→IPC→Brain→Market API→远端 manifest/package 链路上统一返回结构化诊断类别：`timeout`、`proxy`、`dns`、`tls`、`http`、`auth`、`brain_unavailable`、`manifest_invalid`，并带阶段、HTTP 状态和安全消息。远程异常日志只记录 route/status/分类，不输出 Authorization 或 Token。市场页在失败时展示可操作诊断，并可导出 JSON 供用户提交；导出内容不含 token。

### WebWallGL 与可信媒体

依赖锁定为 `webwallgl@3.0.0`，由客户端安装包本地加载，不使用远程 CDN。插件先从 Brain 读取 `project.json`，再让 WebWallGL 加载 Scene；相对媒体请求使用有时限的本地回环票据，Brain 对每个相对路径重新做路径归属校验。Electron 保持 `contextIsolation: true` 与 `sandbox: true`，未开放任意 `file://` 读取。后台隐藏时 pause，恢复时 resume；ResizeObserver 更新挂载视口；卸载时 destroy 并释放 package cache。初始化失败展示真实错误，并保留明确标记的 preview-only 降级状态，不将静态图标成动态 Scene。

## 可复现测试和证据

在 Windows 11、Node 24.21.0、npm 11.19.0、Python 3.13.15 环境安装了项目声明依赖；Electron 28.3.3 和 PyInstaller 6.22.3 可用。

- `npx electron tools/test-wallpaper-electron.js`：PASS。加载真实 WebWallGL 3.0.0；Scene 工程含 1 个 animated solid 层；校验动画帧变化、`project.json`/`scene.json` 认证读取、视频解码、嵌套网页资源、pause/resume、resize、关闭及插件清理。帧 SHA-256：
  - `c0a0ba8be64c5adc305519865a0e3fdccd2d0833821fe09b05853bdcdadbc7d7`
  - `31e78bacbe9b90cde43a86eedceec08210dc226df15c39abf9dfcf3cfcf3a5da`
- Electron 场景截图：[wallpaper-code11.png](../client/_shots/wallpaper-code11.png)。测试使用无用户数据的临时 Electron `userData` 和临时 HTTP fixture。
- `npx electron tools/test-plugin-market-electron.js`：PASS。覆盖按钮、连接状态、安装失败重试、安装启用、禁用、卸载、重启和旧 localStorage 导航顺序。
- `npx electron tools/test-market-live-electron.js`：PASS。真实 Electron renderer/preload/IPC 调用本地 Brain 市场 API，远程 GitHub Raw 清单返回 `ready`，版本 `1.1`，一项插件。
- `python -m pytest tests/test_plugin_market_state.py tests/test_plugin_manifest.py tests/test_plugin_transactions.py tests/test_plugin_structure.py -q`（从 `brain/` 执行）：PASS，56 passed，3 skipped（共 59 项）。
- 远程 Wallpaper 3.1.0 ZIP：真实 `_install_remote_directory()` 下载制品并验证制品 SHA、Manifest、五个文件 SHA；临时目录事务安装和卸载均 PASS。
- `npm run e2e`：PARTIAL，20/26。修复夹具缺失的临时 Brain token 及 Python 路径后，Electron 成功连接、进入聊天页、无 renderer 异常，本地工具用例通过；设备注册/能力/轮询断言、聊天记录断言和 CDP 截图超时仍失败，故 E 仍是 PARTIAL。
- Renderer 测试捕获的 `ReadPixels` GPU stall 是测试为对比两帧执行 `canvas.toDataURL()` 造成的诊断开销，不等同于应用播放路径的 GPU 使用率测量。Wallpaper Engine 本机 Workshop 目录未作为测试数据；通用 Scene 工程已独立验证。

## 插件交付

已在独立 `vigil-plugins` 仓库分支 `codex/p14-wallpaper-webwallgl` 生成新制品，未覆盖 3.0.1、未合并：

- Draft PR：[vigil-plugins#1](https://github.com/limingyi2/vigil-plugins/pull/1)
- 新版本：3.1.0；WebWallGL 依赖：3.0.0
- ZIP：`plugins/wallpaper/wallpaper-3.1.0.zip`
- SHA-256：`6839f0401aa36991eaa83ad523f4c3d9fcb5f941dac3768362005b93b87f799a`
- Manifest 的五个 payload 文件 hash 已逐个重算校验。`market.json` 固定引用包含该 ZIP 的提交 `e4a9c4f0b678a0592307488a8501f258a3ad9613`，市场清单 URL 的更新已推送到分支 tip `20d92fd0cacec91456325215e7f7ae017936f875`。
- 客户端市场主线需审核并合并该 PR 后才会显示 3.1.0；本轮没有自动合并。
- 插件仓库 Actions 的 Linux/Python 3.12 重建检查：PASS。首次检查揭示 Windows/Linux zlib ZIP hash 不一致；打包器现使用固定 ZIP 存储模式及归一化 Unix header，跨平台重建 hash 为 `6839f0401aa36991eaa83ad523f4c3d9fcb5f941dac3768362005b93b87f799a`。

## Windows 候选包

当前本地候选构建流程已实际运行：Python/PyInstaller 构建 Brain，electron-builder 生成 NSIS 安装器与 portable 包；`scripts/build_release.py --verify --candidate` 通过。第一次试构建发现源码在 Brain 构建后改变，构建脚本正确拒绝不一致包；之后从一致源快照重建成功。

候选安装器：`client/dist-release/Vigil.Setup.1.1.8-p9.3.20261021.1.exe`
Portable 候选：`client/dist-release/Vigil 1.1.8-p9.3.20261021.1.exe`
release manifest 校验及 `scripts/build_release.py --verify --candidate`：PASS。安装器大小 `242,260,782` bytes，SHA-256 `50f41170323d5fa90d4b20240e9d34fb3de1bff82a6a391877a745598a66d539`。候选 source commit：`1d69b89d348f7bf1c264aa75112014f333a77bf6`。构建源码摘要排除报告和 `client/tools/e2e.js`（测试夹具），且验证器允许其后只有 `reports/` 变更，报告回填制品 SHA 后仍可复核。已核对 `app.asar` 内包含本地 `webwallgl.global.min.js`。候选用于验收，不代表正式发布。

## 未完成与人工验收

- 用户自定义图片/视频导入、搜索、播放列表尚未实现。
- 安装器的完整聊天 E2E 仍有设备注册/轮询、聊天记录与截图断言未通过，修正这些独立问题并重跑后才能将 E 标为 PASS。
- 用户机器上的真实 Steam Workshop/Wallpaper Engine 内容、用户配置迁移仍待人工验收；渲染器不依赖 Wallpaper Engine 程序运行。
- 3.1.0 市场 PR 尚未合并，因此远端客户端主市场暂时继续提供 3.0.1；新包通过分支 URL 可安装并已验证。

## 本轮修改文件

- `brain/plugins/plugin_market.py`
- `brain/tests/test_plugin_market_state.py`
- `client/electron/main.js`
- `client/electron/plugin-media.js`
- `client/electron/preload.js`
- `client/package.json`、`client/package-lock.json`
- `client/src/index.html`
- `client/src/js/app.js`、`client/src/js/pages.js`
- `client/tools/e2e.js`
- `client/tools/test-plugin-market-electron.js`
- `client/tools/test-wallpaper-electron.js`
- `client/tools/test-market-live-electron.js`
- `market/plugins/wallpaper/__init__.py`
- `market/plugins/wallpaper/frontend.js`、`frontend.css`
- `reports/P14_WALLPAPER_MARKET_REPORT.md`

## 第二轮续修（2026-10-21）

用户提供的 `C:\Users\limingyi\Downloads\p14_hotfix.py` 已在独立 P14 worktree 审查和运行。执行前确认 branch 为 `codex/p14-wallpaper-market`、HEAD 为 `41eb1e762c6568b37488d83518fdc9794012f218`、工作区干净；远端分支 HEAD 相同，无新提交。

### 修复和测试

- `market/plugins/wallpaper/frontend.js` 分离外观更新与 WebWallGL Scene 生命周期；修复多个滑块连续输入时旧预览配置互相覆盖。已断言 Scene canvas 不被重建、外观值变化、动画帧变化。
- `client/electron/plugin-media.js` 媒体票据闲置 30 分钟过期、活动请求续期、最多 1024 个有效票据；失效 URL 重新签发。Electron renderer 的 loopback fetch/XHR 由主进程按签发票据限制。插件清理调用 IPC 撤销票据。
- `client/tools/test-plugin-media.js`：Node 2/2，覆盖正常资源传输、票据到期/重签、卸载撤销。
- `client/tools/test-wallpaper-electron.js`：PASS，隔离 Electron + WebWallGL 3.0.0。覆盖动态 Scene、外观滑块、视频解码、网页嵌套资源、最小化/恢复、尺寸更新、壁纸切换、卸载 DOM/CSS/监听和媒体票据释放。帧 SHA-256：
  - `1cc6d4547400caeaca7a333a0de132e803ecb7ddecb0343c5a672b9782e888c9`
  - `e63db3c79b3c8eb8dd494abd392217e395a6fa5575e25d3fc4fb94bd45024764`
- `client/tools/test-plugin-market-electron.js`：PASS，插件安装/启用、禁用、卸载、renderer 重启与旧导航状态清理。
- `client/tools/test-market-live-electron.js`：PASS，真实 Renderer → preload/IPC → Brain → GitHub Raw 清单，`ready`，market version `1.1`，1 个插件。
- `client/tools/e2e.js`：PASS，27/27。修复空历史验收选择器；旧脚本把本地 Brain 管理桌面误当远程设备客户端，现以真实隔离 Brain HTTP 注册和设备凭据请求核验哈希、能力和 `last_seen`，相关断言保留。
- `node --check` 所涉 JS、`git diff --check`：PASS。

### 插件制品

- `vigil-plugins` 独立分支 `codex/p14-wallpaper-webwallgl` 已推送更新，PR #1 保持未合并。
- Wallpaper 3.1.0 source commit：`346300588d0248a39d2323bcada5c5fd94766803`；包含 ZIP/Manifest 的 package commit：`ca0b12cbf28a0e834a381045acac3955ad8fc851`；分支 tip：`eeec6f93d469e9b4e95ce2e00b81e3ca52a7db07`。
- 3.1.0 ZIP SHA-256：`1df6c354a68ab001d5b867396966b04731845b6330edbb16b6a51ab85c8be1c5`。Windows Python 3.13 重建后 `git diff --exit-code` 通过；Manifest 五项 payload SHA 与包内容匹配；market.json 指向包含制品的固定 commit。3.0.1 文件保留未覆盖。
- GitHub Actions Linux/Python 3.12 检查由推送触发，结果待 CI 回报。

### 未完成与阻塞

- 本机未发现 Wallpaper Engine Workshop 目录；真实或合法授权的复杂 `scene.pkg`（粒子、Shader、交互）未验证。当前动态验收 Scene 是简单自制 animated solid，不能视为复杂场景兼容证据。
- 自定义图片/视频导入、搜索、播放列表尚未实现。
- Windows 候选构建第一次在 dirty worktree 被 release verifier 拒绝；代码提交后需重试 Windows build、安装和卸载验收，随后补候选包 SHA 与构建产物位置。
- PR #1 尚未合并，主市场更新需要人工审核；本轮未合并 PR/dev/master。
