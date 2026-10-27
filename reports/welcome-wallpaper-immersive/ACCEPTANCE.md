# 冷启动欢迎页：壁纸布局与普通主题验收

## 本轮范围

- 基线提交：`1e447c071a21d233ab86fb5670d9409e2b0fda1e`
- 分支：`codex/welcome-wallpaper-immersive`
- 本轮未提交源码修改；未生成安装包。
- 视觉要求按用户最新说明执行：普通模式只改排版并保留原白色/深色主题；壁纸激活时才显示玻璃材质，参考图只用于布局。

## 实际 Electron 截图

- 普通模式欢迎页（浅色主题）：`evidence/welcome-light.png`（历史截图，当前仓库未保存）
- 已有聊天历史（浅色主题）：`evidence/history-light.png`（历史截图，当前仓库未保存）

截图由 `client/tools/test-chat-v2-electron.js` 启动实际 Electron 窗口后生成，窗口尺寸为 2100×1350。壁纸回归场景在该测试环境中使用纯色 Wallpaper fixture，因此不把该场景截图作为实际动态壁纸观感证据。

## 验证结果

命令：

```powershell
$env:VIGIL_UI_SHOTS_DIR = "$env:TEMP\vigil-welcome-wallpaper-ui-corrected"
$env:VIGIL_UI_EVIDENCE_DIR = "$env:VIGIL_UI_SHOTS_DIR\evidence"
npm exec --prefix F:\zhixia\client -- electron F:\zhixia\client\tools\test-chat-v2-electron.js
```

- **通过：49 项。** 包括浅色欢迎页、历史消息、A/B 会话隔离、Creator 草稿恢复、renderer 重启、附件/视频选项、窗口控制、设置和 Wallpaper 布局/宿主检查。
- **追加导航恢复验证：39 项通过、1 项失败。** 新增的窄图标导航断言通过。Wallpaper 回归在 `#vwp-pause` 点击触发 renderer eval 错误处中断；保留的运行目录：`C:\Users\limingyi\AppData\Local\Temp\vigil-chat-v2-EAQpoq`。之前一次运行在末尾等待 `#retryHistory` 时超时（`client/tools/test-chat-v2-electron.js:664`，保留目录：`C:\Users\limingyi\AppData\Local\Temp\vigil-chat-v2-gzMY6O`）。两次运行中 P6 会话、草稿、附件和视频选项检查通过；Wallpaper 集成回归仍不稳定。
- **通过：** `node --check` 检查 `app.js`、`pages.js`、`test-chat-v2-electron.js`；`git diff --check`。
- **未执行：** 打包、安装包升级/卸载、真实用户 Wallpaper 素材下的视觉验收。

## 变更摘要

- 欢迎页普通模式不再覆写聊天主题颜色或绘制蓝色渐变；浅色主题保持白色，深色主题沿用原深色 token。
- 欢迎页排版仍使用紧凑侧栏、欢迎内容和底部 composer；消息区与 composer 保持独立布局。
- 玻璃侧栏、控件及输入框样式只应用于 `.vwp-active.welcome-home`，不会影响普通模式。
- Wallpaper 集成首帧测试断言未修改。
