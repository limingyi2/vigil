# PC Core 与微信遗留边界

Core 的运行渠道只有 `pc`。普通聊天、流式聊天、Agent Run、工具和人设管理省略 `src` 时使用 PC；显式未知渠道返回 400，`app`、`wechat`、`wechat_group` 返回 410，不会改写成 PC 请求。

`GET/POST /api/route` 和 `GET /ws/brain` 保留为 410 退役响应。Core 不再装载 WebSocket 双大脑 Hub，不向本机转发微信消息，不执行迟到回复补写，不读取或更改 `wechat_route` / `wechat_enabled`，不自动收藏微信入站图片，也不注入微信群上下文。独立扩展需要单独的接入设计与授权，不能恢复这些硬编码入口。

`wechat-bridge/` 源码归档保留；原 Core 转发模块、Electron 转发器和专属验收脚本保存在 `wechat-bridge/archive/core-forward/`，不在 Core 启动和正式测试路径中。共享插件、Skill、Capability Registry 和本地工具权限检查继续保留。

## 人设与数据

`pc_context.persona_key` 是 Brain、管理接口、会话/工作区归属的共同解析入口。现有显式 `persona_by_src.pc` 绑定优先，否则使用用户配置的 `persona_file`。PC 人设页面的“应用”将选定模板写入 `persona_file` 并取消旧 PC 覆盖，保留其他渠道的配置。配置更新和热重载使用聊天锁；系统提示、历史路径、长期记忆和检索使用相同的人设键。

PC 历史仍按 `人设 × pc × conversation_id` 分文件，摘要和历史检索继续使用对应隔离键，长期记忆仍按 `人设 × pc` 分区。省略渠道和会话的内部聊天也会重置到默认 PC 会话，不继承上一轮的会话 ID。

旧版本曾把没有 PC 覆盖的人设的会话索引写在空人设键下。兼容读取只在该 ID 的**当前人设独立 PC 存档存在**时展示和解析原索引；不重写索引，不迁移、合并记录，不从共享文件推断归属。没有可验证存档的旧空键对象保留在原位置，不自动归给新人设。

显示记录与 Agent Run owner 包含有效人设及会话 ID。旧会话的未分人设 owner 仅在独立 PC 存档能证明唯一归属时用于恢复；同一旧 ID 存在多个人设存档时，各人设使用自己的新 owner，旧记录原位保留。无法证明归属的旧 `main`/`welcome` 显示记录也保留原位，不自动展示给任何人设。

存储层保留显式旧渠道路径规则，包括默认微信的 `chat_history.jsonl` 和 `memory.json`。这些是归档寻址兼容，不是活跃渠道。PC 不使用旧微信基础文件作回退；分区解析失败关闭读取。用户原有微信聊天、记忆、配置和 Bridge 文件均不迁移、不合并、不删除。

统一检索同步使用人设对应的历史和记忆分区，不把未分区的旧文件标成当前 PC 人设的数据。`conversation_context`、`history_retrieval` 和 `memory_store_v2` 保持可用。本次不删除 `recap_store`，待 PR #25 独立集成。

## Windows 客户端复验

源码测试覆盖 HTTP 退役响应、默认 PC 落盘、双人设切换、会话隔离、历史与记忆重启恢复、旧文件字节保留。发布前仍需在真实 Electron 客户端检查人设卡片与切换、历史恢复、工作区归属，以及 Coding Agent 的暂停/续跑、工具授权弹窗和 SSE 展示。无需构建 Windows 安装包。
