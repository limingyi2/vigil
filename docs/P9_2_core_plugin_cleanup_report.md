# Vigil P9.2 Core 插件边界清理记录

日期：2026-10-19
基线：`origin/dev` / `cc811175d745e76d72e10d33d29c64738db807d4`
版本：`1.1.7` / code `28`（本阶段未改版本号）

## 结论

本阶段已清除 Core 设置中的固定微信桥接入口，并把桌面扩展管理收拢到通用“插件与扩展”入口。已有 Wallpaper 扩展安装器继续使用独立版本目录、启停状态、启动健康检查和失败回退；`userData/wallpaper-engine` 数据仍与程序包分离。

**P9.2 的完整插件化验收尚未通过。** 当前 Wallpaper renderer 脚本仍由 Electron 主进程注入 Core renderer，并在 Core 页面中注册路由；它不是隔离的扩展 UI。微信 Bridge 仍没有可安装的桌面插件 artifact、Main 进程生命周期管理器或受控配置存储。此提交没有把这些能力伪装成已完成。不得据此宣称已满足“无插件专属代码的纯净 Core”或“微信插件安装后可管理”。

## 实际审计结果

- Core 中微信设置页是一个说明/连接检测页，不管理 Bridge 进程；该固定页已移除。
- `pc`、`wechat`、`wechat_group` 仍是 Brain 的通用渠道标识，参与聊天历史、人设、记忆和权限分区。本阶段保留这些协议及调用方，不触碰微信账号、Bridge 仓库、真实配置或部署。
- Electron 构建只打包 `client/electron/**` 与 `client/src/**`，显式排除根目录中的 `wechat-bridge/**`。当前 `main.js` 未启动微信 Bridge，`client/electron/forward.js` 仍不是主进程启动项。
- Wallpaper manager 在 `userData/extensions/state.json` 保存程序状态；Wallpaper 资源在启用恢复时才由 manager 加载。独立程序版本位于 `userData/extensions/programs/`，用户设置、壁纸素材和 Workshop/自定义路径位于 `userData/wallpaper-engine/`。
- Wallpaper 扩展的宿主能力和状态管理已有实现，但 renderer assets 仍由主进程 `executeJavaScript` 注入 Core 页。禁用/卸载没有独立 iframe/renderer 生命周期，也没有通用插件页面注册协议。
- `brain/plugins/wallpaper/**` 是旧 Brain 插件副本；本阶段未删除。尚未完成外部调用方清点及打包依赖证明前，保留兼容代码更安全。
- PC 默认人格此前已在 commit `6f4f444` 改为自然、友好、专业风格，本阶段未重复修改；微信人格仍由独立 persona key/渠道配置解析。

## 本次变更

| 文件 | 变更 | 原因 |
|---|---|---|
| `client/src/js/pages.js` | 删除固定“微信桥接”设置页；移除设置系统组中的 Wallpaper 专属页入口；设置首页只保留“插件与扩展”通用入口，并将现有扩展管理控件放入能力与扩展组 | Core 不再提供固定 Wallpaper/微信设置导航，保留现有 Wallpaper 管理可达性 |
| `client/tools/test-plugin-market-electron.js` | 更新 Electron UI 回归，验证通用入口、无固定微信导航及扩展管理和市场页面 | 适配新的设置入口并覆盖实际渲染 |
| `client/tools/test-plugin-ui-boundaries.js` | 新增设置入口静态边界检查 | 防止固定入口回归 |
| `client/package.json` | 将静态 UI 边界检查纳入 P9 扩展测试命令 | 让本次检查进入常用回归流程 |

## 验收状态

| 场景 | 状态 | 证据 / 缺口 |
|---|---|---|
| 纯净 Core 启动、聊天及 Agent | **未完成验收** | 本阶段无安装包隔离启动记录；Bridge 未由 Core 启动，但 wallpaper 脚本/注册机制仍在 Core renderer 路径中 |
| 安装 Wallpaper 后动态注册受控页面 | **未通过** | 仍通过 Core renderer `executeJavaScript`；没有隔离页面注册协议 |
| 禁用 Wallpaper 后入口及运行资源释放 | **未通过** | 宿主 stop 能力存在，但本阶段未实现页面注销/监听释放的隔离生命周期 |
| 卸载 Wallpaper 后程序资源清理、用户素材保留 | **部分通过** | 扩展 manager 测试验证停用、旧版本回退和用户数据保留；卸载仍保留版本目录以支持回滚，尚未定义安全垃圾回收 |
| 未安装微信插件时无微信入口/Bridge 进程 | **部分通过** | Core 固定入口已移除；Core 没有 Bridge 启动调用。Brain 通用渠道/隔离代码仍保留。不存在桌面微信插件可安装流程 |
| 安装微信插件后的生命周期与页面注册 | **未实现** | 没有可安装 artifact、凭据 vault、进程 supervisor 或 UI 页面 |
| 插件崩溃不影响聊天 | **未验证** | 现有 Wallpaper 启动失败被捕获的日志路径仍在；未完成隔离 renderer 的崩溃注入测试 |

## 测试

- `node tools/test-plugin-ui-boundaries.js`：通过。
- `node tools/test-extension-manager.js`（使用锁文件匹配的依赖）：通过。覆盖安装/启停/健康检查/回滚/用户数据保留和 Manifest fail-closed。
- `node tools/test-manifest-schema.js`（使用锁文件匹配的依赖）：通过。
- Electron `tools/test-model-settings-electron.js`：通过，模型保存/重启恢复及设置窗口回归。
- Electron `tools/test-plugin-market-electron.js`：通过，含新通用设置入口、市场未配置状态及本地安装交互。
- Electron `tools/test-chat-v2-electron.js`（使用仅含假数据的本地 Wallpaper 包）：通过聊天、会话、Persona、工具/页面及 Wallpaper UI 回归；不代表 WebGL/Steam/真实桌面壁纸环境验收。
- 全仓 Python `pytest -q`：通过，完整测试运行结束（含跳过项）。
- 首次从主工作区直接调用扩展测试失败：主工作区 `node_modules` 缺失 `ajv/dist/2020`；切换到既有锁文件匹配依赖后测试通过。未改动或重装主工作区依赖。
- Windows 候选包：已构建 `1.1.7` / code `28`，采用锁文件一致的 Node 依赖；初次构建暴露主工作区依赖目录与 lockfile 不一致，已保留旧目录并用完整一致依赖重建成功。
- 隔离安装生命周期：通过。安装器退出码 0，Electron 启动、Brain ready、假 Provider 首次聊天成功；重启恢复聊天历史；卸载退出码 0，程序文件移除，73 个用户数据文件逐文件保持不变。
- 安装包源提交、SHA-256 和精确大小以最终 `client/dist-release/release-manifest.json` 为准；隔离结果保存在 `client/dist-release/P9.2-isolated-lifecycle-report.json`。
- 真实 Wallpaper/WebGL 首帧、真实微信、远端 Bridge 和远端部署：本阶段未执行。

## 未完成与后续最小范围

1. 先建立桌面扩展独立 WebContents/沙箱 UI：Core 主进程仅提供按权限枚举的 RPC；移除 Wallpaper 对 Core renderer 的脚本/CSS 注入，并让页面注册、事件监听及卸载与扩展 WebContents 同生命周期。
2. 将 Wallpaper 的设置界面、样式与业务控制迁入独立 artifact；Core 留下通用扩展管理器及明确允许的 Wallpaper capability adapter。清点后再决定是否可从 Brain release 排除旧 `brain/plugins/wallpaper/**`。
3. Bridge 需要单独定义本地/远端安装 artifact 与生命周期协议、隔离凭据存储及状态检查。未取得独立 Bridge artifact/schema 前，不应提供会执行任意目录程序或处理旧凭据的“安装”按钮。
4. 为 1–3 增加生命周期/崩溃隔离测试，再做真实安装包验收。回滚方式是恢复 renderer 注入和旧 Core 入口；所有 `userData` 保持不变。不得自动清理旧扩展版本或个人资产。
