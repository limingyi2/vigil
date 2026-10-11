# P2 验收证据

所有任务、线程、审批与事件均来自隔离测试夹具，不是生产会话或真实模型成果。

- `electron-results.json`：真实 Electron 28.3.3 的 18 场景断言、仅路径的 HTTP 轨迹、原生通知尝试与 `show` 回调。
- `events.json`：确定性 P1 Adapter 事件轨迹，保留连接、轮次关联、运行、审批、完成、失败、输入及重连；省去用于制造 gap 的 2005 条重复 rollover 填充事件。序号跳跃与页面 gap 测试对应，不能把它理解为完整日志。
- `regression.txt`：Python/Node/真实 Electron 回归的退出码和输出。
- `chat-regression.json`：现有聊天 UI 实际 Electron 回归结果摘要。
- `source-manifest.json`：本次运行对应的源文件 SHA256。
- PNG：实际 Electron capturePage 截图，无生产凭据、对话或项目内容。Wallpaper 图只验证 P2 页面材质。

线上模型调用量为 0。Python 的真实 Codex CLI 回归使用认证 loopback 模型夹具和独立 CODEX_HOME；Electron 测试不读取生产配置、登录、记忆或钥匙串。完整验证入口为 `python tools/verify_agent_center_p2.py --include-chat`。
