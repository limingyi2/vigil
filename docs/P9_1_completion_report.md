# P9.1 完成报告（候选阶段）

状态：P9.1 候选已通过隔离安装验收；不代表正式发布。基线为 P9.0 `14030d38af96b924fb7433d4096ea6548c53586e`，工作目录为隔离 worktree。本次提交由本地 P9.0 基线派生，候选实际源提交、大小和 SHA-256 以同目录 `release-manifest.json` 为准。

## 已实现

- Electron Core 不再静态加载 Wallpaper JS/CSS、宿主或引擎 vendor。新增的桌面扩展管理器将状态存入 `userData/extensions/state.json`，壁纸程序按版本放入独立目录，用户资产继续留在 `userData/wallpaper-engine`。未安装/禁用时不创建 Wallpaper host；启用时加载并健康检查，更新通过 staging 和健康检查后切换，失败保留旧版本。禁用和卸载不删除用户资产及旧程序版本。
- 安装要求用户授予 Manifest 声明的权限；运行时校验 Draft 2020-12 Schema、核心与协议版本、权限白名单、入口路径、包文件清单及 SHA-256。桌面 IPC 仅代理列出的 Wallpaper 操作，并检查主窗口主 frame、启用状态和权限。
- Wallpaper 扩展包可由 `client/tools/build-wallpaper-extension.js` 本地构建，Core 安装包不包含壁纸宿主与引擎。发行目录/签名下载尚未建立，示例清单中的假地址与假哈希不是可发布制品。
- WeChat Bridge 的独立公开仓库 `limingyi2/zhixia-wechat-bridge` 是发行源码权威；`wechat-bridge/**` 是明确标记 revision/blob 的归档兼容快照。`brain/plugins/gate.py` 是 Vigil Brain 当前权威实现；`wechat-bridge/brain-plugin/gate.py` 是历史参考，不覆盖同步。契约测试验证两者共同 OPEN/PASS 决策，同时记录 active `ctx.veto` 与 legacy 返回值的差异。Bridge 仍为独立进程；双 Brain 转发仍独立且默认不启用。
- `deploy/wechat/deploy_bridge_m4.py` 不再包含固定远端源码路径；执行前必须提供并校验权威仓库 URL 和 revision。文档已明确源码/部署边界及未完成能力。
- 通用 Manifest Schema 及运行时副本已加入；未知权限/类型、协议不兼容、Core 版本不足、路径穿越、文件/hash 不一致均拒绝安装或加载。

## 修改文件

- Electron 扩展生命周期、IPC 与 UI：`client/electron/main.js`、`client/electron/preload.js`、`client/electron/extension-manager.js`、`client/src/index.html`、`client/src/js/app.js`、`client/src/js/pages.js`。
- Schema、示例及本地包构建/验证：`docs/schemas/extension-manifest.schema.json`、`client/electron/extension-manifest.schema.json`、`docs/examples/extensions/*.json`、`client/tools/build-wallpaper-extension.js`、`client/tools/test-*.js`。
- Bridge 权威快照、部署和文档：`wechat-bridge/UPSTREAM.json`、`wechat-bridge/README.md`、`wechat-bridge/vm/runtime/*.bat`、`deploy/wechat/deploy_bridge_m4.py`、`docs/05-微信/P8.2-微信插件化迁移清单.md`、`docs/USER_GUIDE.md`、`docs/P9_extension_protocol.md`。
- 测试和 CI：`tests/test_p9_bridge_contract.py`、`tests/test_p9_contract_artifacts.py`、`.github/workflows/p9-extension-contract.yml`、若干 Electron 回归 fixture、`scripts/test_windows_candidate_lifecycle.py`、`scripts/build_release.py`（npm 不在 PATH 时回退到锁文件安装的 electron-builder）。

## 测试结果

通过：

- `python -m unittest discover -s tests -p 'test_*.py'`：972 项通过、11 项按既有标记跳过。
- P9 文档/Schema/Bridge 契约：12 项通过。
- Draft 2020-12 Manifest 示例及拒绝用例、扩展管理器安装/启停/重启/失败回滚/数据保留、实际构建 Wallpaper 包集成测试通过。
- Electron Chat-v2 回归通过，使用真实 Electron 主窗口与本地 fake host/extension package；覆盖普通聊天、会话、设置及 Wallpaper 页面/宿主状态。
- Electron 模型设置、插件市场、Agent Center（25 项，0 错误）、P72 工作区取消回归通过。Agent Center fixture 补齐了 API handler 的 `URL`/`connectionBase` 依赖和 Wallpaper 扩展 preload stub。
- Windows x64 候选 `Vigil.Setup.1.1.7.exe` 隔离验收通过：静默安装及包内文件 hash 校验、Electron/Brain ready、fake Provider 首次聊天、应用重启后同一会话与历史恢复、静默卸载、74 个用户数据文件及外置哨兵均保持原样。全程使用独立临时用户目录；无真实 API 请求。

失败或跳过：

- `test-wallpaper-v2-electron.js` 需要系统 `ffmpeg`，当前环境未安装，跳过真实 ffmpeg/视频路径验收。Electron fake host 的 UI/生命周期测试不代表真实 Steam Workshop 或 GPU/WebGL 首帧验收。
- 未连接真实 WeChat、未发送消息、未访问远端 Bridge/部署机、未调用模型 API；对应生产环境验收未执行。

## 尚未完成与安全边界

- Wallpaper 脚本当前在 Core renderer 中执行；IPC 操作受限，但脚本仍可见 preload 暴露的其他 Core API。Manifest、哈希和权限提示都不是沙箱，当前只应安装可信发行源。独立 renderer/process 隔离与签名发行验证仍需后续实施。
- Bridge 尚无 Core 侧独立安装/启停控制器、健康 RPC 与 fake process lifecycle harness；远端部署版本仍未知。转发模块与 Bridge 不合并。
- 旧 `.vigil/wallpaper/config.json` 仅有迁移文档约束，未实现自动探测/迁移；同路径的 `userData/wallpaper-engine` 资产不搬移、不删除。
- Bridge 快照有意不替换为 upstream 新版本；上线同步需在权威仓库按独立发布流程完成，并重新审查安全与部署兼容。
- npm 安装报告有既有依赖安全公告（1 critical、10 high、5 moderate）；本阶段未做无关升级。
- 旧版本 Windows 安装器在原工作区不存在，无法做前后 installer 文件大小的直接比较。构建清单记录本候选准确体积；Core 的 `app.asar` 确认 806 项内 Wallpaper 宿主/前端/vendor 为 0 项。拆出的旧壁纸前端与 vendor 当前源码共 4,349,940 字节（64 个 vendor 文件及两个前端文件），仅作未压缩源码体积参考，不冒充安装包节省量。

## 回滚

代码回滚使用本次 P9.1 提交的反向提交；旧 Brain Wallpaper 路由及 Bridge 快照仍保留。Wallpaper 扩展禁用后 UI 不加载，用户数据保留；升级失败时管理器保留旧版本并恢复旧 active version。候选安装包不发布正式 Release。构建源提交、SHA-256、字节数及隔离生命周期完整日志写入候选目录的 manifest 与验收报告。

## P9.2 建议最小范围

先为 Wallpaper 增加独立 renderer/process 与窄 IPC capability，固定发行仓库并验证签名，再实现可审计的 Bridge 管理/健康协议与本地 fake process lifecycle；在保持当前 Bridge 独立部署及 `pc`、`wechat`、`wechat_group` 隔离的前提下补兼容测试。任何远端微信验收应另行授权。
