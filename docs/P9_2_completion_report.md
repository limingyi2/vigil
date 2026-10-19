# P9.2 完成报告

## 基线与根因

- 基线：`origin/dev` 与本地 `dev` 起始提交均为 `e4409f468aab80d12dbce29348e85cb2959ce441`。`master` 未修改。
- PC 人设的 `ASSISTANT_RULES` 明确禁止寒暄、闲聊和情绪支持，且 `build_system_text()` 将其放在提示词末尾，导致普通聊天被压制。规则拼接顺序本身符合 PC 优先级设计；问题在规则内容。PC 规则仍只应用于 `src=pc`，微信/其他渠道仍走各自陪伴人设。
- 设置首页的“壁纸与外观”和“桌面扩展”都导航到 `desktop_extensions`，是重复入口。
- 插件市场把默认仓库固定为开发机路径 `F:\vigil-plugins`，配置不存在时也会静默回退到该路径；缺少仓库、读取失败、空清单都被折叠为空数组，UI 固定显示“市场为空”。刷新接口原本确实执行 `git pull`，但不限制快进且前端忽略失败结果。
- Brain 冻结运行根目录由 `brain/paths.py` 决定：Windows 安装版配置在 sidecar 运行目录的 `config/config.json`。Core 仓库没有市场副本或 `market.json`，不会把开发机目录打进安装包。
- GitHub 可见仓库 [`limingyi2/vigil-plugins`](https://github.com/limingyi2/vigil-plugins) 当前有 `market.json`，清单含一个 `wallpaper` 旧 Brain Python 插件目录；这不是 P9.1 桌面扩展包，也没有在可见仓库内容中核实到安全制品构建/签名分发流程。因此没有把 GitHub 自动安装接入客户端。

## 修改

- `brain/persona_store.py`：将 PC 定位改为自然、友好、专业的 AI Agent；允许聊天、寒暄、讨论、学习和情绪交流，也保留严谨任务执行、权限控制、工具安全、事实准确和不编造结果。仍将渠道专属规则置于最终底线位置。
- `tests/test_relation.py`、`tests/test_owner.py`：覆盖自然聊天规则、PC/微信人设隔离、规则尾部优先级，以及 PC 获准本机工具的实际队列调用。
- `client/src/js/pages.js`：移除重复设置卡片；未安装 Wallpaper 时说明 Core 不内置它，需安装扩展包。保留安装、升级、启用、禁用和卸载入口及既有 IPC 权限边界。
- `brain/plugins/plugin_market.py`：移除开发机绝对默认路径；`market_repo` 从运行时 `CONFIG_DIR/config.json` 解析，相对路径锚定在该 `config/`。为未配置、读取失败、空清单和正常状态返回明确状态。刷新校验本地 Git 工作区并执行 `git pull --ff-only`，再读取新清单。
- `client/src/js/pages.js`：显示插件市场状态和刷新错误，不再将配置缺失或读取失败显示为“市场为空”。
- `brain/tests/test_plugin_market_state.py`、`brain/tests/test_plugin_transactions.py`：覆盖路径解析、市场状态、格式错误、快进刷新，以及使用临时本地 Git 远端的真实 `pull`。
- `client/tools/test-plugin-market-electron.js`：覆盖重复设置入口、未配置提示、刷新错误反馈和现有安装反馈流程。
- `brain/plugin_structure.py`、`brain/plugin_ops.py`：检查发现 Python 导入生成的 `__pycache__` 会让本地插件静态检查误判为未知嵌套目录。忽略普通 Python 缓存目录，市场 staging 复制时排除缓存；其他嵌套目录及符号链接仍按原规则拒绝。
- `docs/04-插件市场使用指南.md`、`docs/USER_GUIDE.md`：替换过时的 `F:\vigil-plugins`/旧私有 CodeUp 指引，说明运行时配置路径、当前 GitHub 清单边界和不自动信任远端插件的约束。

## 验收结果

- **全仓 Python：通过。** 首次运行发现 `branch-atlas` 的自动生成 `__pycache__` 被结构校验误判；修正缓存目录处理后，全仓 `pytest -q` 退出码为 0。相关人设、PC 工具执行、市场状态/本地 Git 刷新、插件事务和 `branch-atlas` 针对性回归也通过。
- **Electron 设置与市场：通过。** 隔离渲染器确认设置首页只有一个“桌面扩展”入口；未配置提示和刷新错误可见；安装失败可重试，成功会刷新安装状态。
- **Electron 模型设置：通过。** 模型保存、切换、重建客户端读取、进程重启恢复均通过。
- **P9 扩展安全回归：通过。** Manifest Draft 2020-12 校验和扩展管理器安装、状态、健康检查、禁用、重启、回滚及用户数据保留测试通过。
- **语法与差异检查：通过。** `pages.js`、Electron 市场测试脚本、`extension-manager.js` 的 Node 语法检查及 `git diff --check` 通过。
- **真实安装包隔离验收：一次完整通过。** 候选版安装到临时目录，验证安装文件清单、Core 启动、Brain health、Fake Provider 首次聊天、Electron 重启后会话/聊天记录恢复，以及卸载时 73 个隔离用户数据文件均未改变。验收报告保存在构建 worktree 的 `client/dist-release/p9.2-debug-2/lifecycle-report.json`。
- **验收波动：需披露。** 同一安装脚本另有两次在 Fake Provider 已收到请求后，未于 18 秒内观察到 renderer 回复而失败；P9.1 保存的旧候选控制组也复现同样失败。一次 P9.2 完整验收成功。未把不稳定的独立 CDP E2E 当作通过：源码模式未连接到 renderer；打包模式停在目录插件页面注册断言。需要后续排查这些现有验收夹具/冷启动时序问题。
- **环境差异：** 主工作区 `client/node_modules/ajv` 是 6.15.0，与 lockfile 要求的 8.17.1 不一致，直接在主工作区运行 Manifest 测试失败；使用 P9.1 worktree 中 lockfile 对齐的依赖重跑后通过。没有改动 lockfile 或安装用户凭据。
- **未执行：** 真实云端模型 API、真实插件远端代码安装、真实微信或用户数据访问。本任务不需要这些联网/外部行为。

## 候选包

P9.2 候选构建产物来自产品代码提交 `6f4f444cdcb6c8b09207e55cf424b4f9eeb4f75c`。后续报告提交只更新验收文档，不改变该候选包所含的产品代码：

- 版本：`1.1.7` / code `28`，candidate 标志为 true，不是正式 Release。
- 源提交：`6f4f444cdcb6c8b09207e55cf424b4f9eeb4f75c`。
- 安装包：`client/dist-release/Vigil.Setup.1.1.7.exe`（构建 worktree）。
- 大小：`106,083,603` bytes；SHA-256：`d7a53d9124daa0c880bcf51dbced6fcc0bd980166e0532b4d8b074e4850eb4f3`。
- 对比保存的 P9.1 安装包 `106,082,797` bytes，增加 `806` bytes。
- 原 P9.1 安装包、blockmap、release manifest、contents manifest 和生命周期报告已保存在 `client/dist-release-p9.1-preserved/`，构建未覆盖旧候选。

**数据保护：** 验收只使用临时 profile 与临时 Brain home。未读写现有用户 API 配置、聊天记录、插件数据或 Wallpaper 用户资产。根工作区原未跟踪审计文件 `docs/P9_WALLPAPER_WECHAT_SPLIT_AUDIT.md` 保持未跟踪且未修改。
