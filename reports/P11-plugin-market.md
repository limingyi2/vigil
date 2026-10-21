# P11 插件市场安装修复与一级导航

基线：`origin/dev`，`46f7c5b`（2026-10-21）。主仓库分支：`codex/p11-market`。

## 根因证据

通过市场实际下载的 Wallpaper 3.0.1 ZIP 摘要为
`c51edf9886435c1b091184b2e5e15a2cafd95e438499481082642c3751dbcc85`，与当时远程市场索引一致。
ZIP 中有以下 6 个文件，均使用相同的 `wallpaper/` 前缀及正斜杠，无大小写或旧版本文件混入：

```text
wallpaper/README.md
wallpaper/__init__.py
wallpaper/frontend.css
wallpaper/frontend.js
wallpaper/manifest.json
wallpaper/version.json
```

Manifest 只声明前四个负载文件，漏掉了 `version.json`；四个已声明文件的 SHA-256 全部正确。
未声明文件的 SHA-256 为 `c1ea5024b495e33f2021035051bad14c620ba492a3fd0ac88c90cb8898634b19`。
因此严格文件集合校验正确地拒绝安装，本次实测失败的直接原因是发布包不一致，而非镜像返回错误 ZIP。

## 发布修复

独立插件仓库已发布修复，版本继续为 **3.0.1**。
使用明确源码提交 `c196b3db5ef7294a907a34c6b746111fdbfd1986` 的 Git blob 重建，
未修改插件源码、功能、权限、协议或 UI。Git blob 使用 LF，与原先由 Windows 工作区生成的
CRLF 包字节不同，所以重建后逐文件摘要重新计算，不能沿用旧包摘要。

- 制品提交：`c1c9b63703c6d733688640784a0be55421c67909`。
- 新 ZIP SHA-256：`4e551ae17783870872be7c2635d1a396fe5269fa032b514aff7e536940b22616`。
- 市场索引已固定到上述制品提交的 Raw URL，避免 `main` 的镜像缓存将索引和制品混用。
- 新打包工具从明确提交生成完整清单和确定性 ZIP，排除历史 ZIP，验证源码/Manifest/市场版本一致。
- 独立仓库增加发布一致性 CI，重建后检查 Manifest、ZIP、市场摘要无差异；本机重建校验通过。
- 未改动其他版本制品；历史制品提交仍可按原提交号访问。

## 主仓库修改

保留 Electron + Python Brain、Manifest/逐文件 SHA-256 校验、权限检查、通用安装器和事务提交。
哈希不匹配的镜像只能回退到下一个下载源，全部失败即拒绝安装。可变市场索引保持实时源优先，
不会因为某个制品 CDN 可达就优先读取它的旧索引。错误返回包含下载、ZIP、Manifest、文件摘要或事务阶段，
文件集合不一致时明确列出未声明和缺失文件。

主导航将市场紧接在聊天后，入口图标为 24px，点击区域为 48px，设置保留底部单一入口。
修复聊天 CSS 隐藏一级栏的问题，移除设置首页的重复市场入口，复用 `renderPluginMarket` 和 `/api/market/*`。
市场使用主内容区与响应式卡片网格，无额外 BrowserWindow。

聊天切换到市场时保留连接中的原始 DOM、会话/工作区选择、输入草稿和流式订阅，返回恢复同一视图。
安装/卸载不再触发 Wallpaper 专用的自动 Brain 重启和整窗刷新，仍按现有机制提示待重启生效。
安装中的插件按名称锁定，刷新市场或离开再返回也不会发出第二个并行安装请求。
异步市场请求完成时不会覆盖已切换到的聊天视图。

真实 Agent 事件测试发现流式消息模板的思考区域少一个闭合标签，导致无思考内容时清理思考块会一起删掉正文；
补齐闭合标签，以确保从市场返回后最终回复可见。

## 验证

1. 实际远程市场索引、真实 HTTPS 下载地址、全部制品校验，通过临时运行时目录完成安装 → 刷新已安装状态 → 卸载 → 重装。
2. 使用插件加载器加载真实 `__init__.py`，并核对 JS/CSS 内容；安装器本身仍不 import 新插件。
3. 将真实下载制品的 JS/CSS 注入隔离 Electron，确认插件注册单一壁纸入口、能够打开 `#wp-library`，保持单窗口。
4. Python：**56 passed / 3 skipped**。跳过项均为 Windows 环境不能创建符号链接的既有事务测试。
5. Electron：一级入口可见、图标尺寸、同窗口、原输入节点和草稿保留、会话选择保留、安装失败可重试、跨导航防并行安装、刷新后已安装状态保留。
6. 真实 renderer + preload IPC 流式 Agent 事件：市场打开期间完成回复，返回聊天后回复和会话仍在，输入恢复可用。事件使用隔离 fixture，不调用真实模型。
7. 未声明文件、错误文件摘要、重复 Manifest 路径、制品摘要错误、下载断网均被拒绝；旧插件和注册表保持原样，无临时安装残留。
8. 720px / 1200px Electron 窗口网格无横向溢出，截图已人工查看。
9. JS 语法检查和 `git diff --check` 通过。未执行客户端打包或真实 Steam 素材播放测试。

复现命令（仓库根目录）：

```powershell
$env:PYTHONPATH='brain'
python -m pytest brain/tests/test_plugin_market_state.py brain/tests/test_plugin_manifest.py brain/tests/test_plugin_transactions.py brain/tests/test_plugin_structure.py -ra
python client/tools/test-plugin-market-live.py
# 使用 Electron 运行，清除 ELECTRON_RUN_AS_NODE；Windows 可使用 Start-Process -Wait 获取退出码。
client/node_modules/electron/dist/electron.exe client/tools/test-plugin-market-electron.js --wallpaper-assets=F:/zhixia/reports/p11-wallpaper-assets.json
```

真实网络脚本仅将安装目录、注册表和副作用钩子隔离到临时环境，不替换市场索引读取、制品下载、校验或事务安装路径。
执行完成后移除临时安装目录，不改变用户插件和待重启状态。
