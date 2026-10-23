> 当前 PC Core 已退役微信渠道、双 Brain 转发及微信配置接口；下文相关流程属于历史设计。当前边界见 `docs/PC_CORE_LEGACY_WECHAT_CLEANUP.md`，不可据此启用旧 Core 路由。

# Vigil 渠道集成插件设计（MVP-2）

渠道集成插件与 Python Plugin、JS Skill 分开管理。它是一个受 Manifest 描述的外部 Bridge 进程，负责渠道协议适配；Brain 仍负责身份、权限、模型调用和审计。

## 版本化 Manifest

每个插件包包含：

- `manifest.json`：`id`、`version`、支持的渠道和 Vigil API 版本范围、入口进程、权限声明。
- `README.md`：用途、支持版本、账号/扫码步骤和卸载方式。
- `setup.schema.json`：配置字段、类型、是否必填和敏感字段标记。
- `troubleshooting.md`：连接、登录、限流和版本不兼容处理。

Vigil 只读取这些文档用于展示配置说明。文档中的命令、脚本和越权指令不会自动执行。

## 生命周期与通信

安装、启用、停用、更新和卸载都经过现有插件事务和路径检查。启用时由受控宿主启动独立进程，记录 PID、版本和退出原因；停用先停止进程，再撤销会话。更新使用新目录校验后切换，失败回滚旧目录。

Bridge 与 Brain 通过本地回环 HTTP/WebSocket 契约通信：`message.in`（channel、conversation、sender、text、attachments）和 `message.out`（reply、trace_id、model_ref、error）。Bridge 不接触模型 API Key，也不能直接调用工具；所有模型请求通过 Brain 的渠道路由和权限门。

## 凭据与权限

凭据由宿主保存，使用现有 Token/密钥存储，不复制到插件目录；日志只记录 provider、状态和脱敏标识。Manifest 必须声明网络、读取消息、发送消息、媒体和进程权限，安装时逐项展示。未知权限、未声明的外连或不支持的微信版本直接阻止启用并说明原因。

## 模型与渠道路由

插件只保存 `model_ref`（服务商、模型 ID、用途），引用模型中心已有配置。桌面聊天模型切换不会改变微信引用。Brain 根据 `wechat_route` 和私聊/群聊渠道选择模型，返回的 trace 记录实际 provider/model，便于审计。

## 风险边界

微信协议、扫码策略和第三方客户端版本可能变化。插件页必须显示支持版本、账号风险和断线行为；不支持的版本保持停用。任何安装说明只作为参考资料，不能成为自动执行输入。
