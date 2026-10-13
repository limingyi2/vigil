# 设置、模型与微信改版交接

分支：`feature/settings-model-wechat`

## P0

- `/api/chat/stream` 对可能需要工具的请求转入 Brain 原生非流式 ReAct 循环，避免流式正文把 XML 风格内容直接展示给用户。
- XML-like 工具文本默认不再执行；只有 provider 明确声明且配置显式允许时才可走兼容路径。普通模型返回这种格式时，用户收到能力提示，不会把未执行内容当作结果。
- 新增 `/api/models/list`，模型密钥放在 JSON body，不再由客户端拼进 URL；新增 `/api/models/test` 和 `/api/models/key/delete`，错误按 Brain 版本、权限、网络和服务商拒绝区分。

## 设置与微信

设置中心增加“微信聊天集成”入口，展示启用状态、云端/本机路由、Bridge 状态、实际当前模型、私聊/群聊说话档位、连接检测和最近 Brain 日志。微信引用模型中心的配置，不重复存密钥；`/api/route` 同时接受旧的 `wechat_route` 和客户端曾使用的 `route` 字段。

微信渠道插件化方案见 `docs/CHANNEL_INTEGRATION_PLUGIN_DESIGN.md`。本轮不迁移现有 Bridge 进程，不自动执行插件文档命令。

## 验证范围

本轮只运行 P0/P1/P2 相关的语法检查、模型中枢掩码与配置单测、原生工具协议针对性测试和客户端静态检查。真实服务商连接与 Windows Electron 视觉检查需要使用隔离凭据和本机环境后再验收，不能用模拟结果代替。
