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

## 验收状态

本报告会在完成最终回归、Windows 候选包构建和隔离安装验收后补齐具体结果、提交和安装包校验值。所有验收使用临时目录和假 API；未连接真实模型服务或远端用户数据。
