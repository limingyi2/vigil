# 管理 Agent Token 轮换清单

当前代码的中心验证端从 `VIGIL_MANAGEMENT_AGENT_TOKENS` 环境变量读取节点 Token。`server-b` 与 `local-pc` 应使用不同的新值。文件从 Git 索引移除只阻止后续版本跟踪，**不能让已提交的旧 Token 失效**。不要把 Token 粘贴到 Git、聊天、工单或报告。

## 节点和配置位置

| 节点 | 当前 Agent 配置入口 | 中心映射键 |
| --- | --- | --- |
| `server-b` | 节点进程环境变量 `VIGIL_MANAGEMENT_TOKEN`（独立 Agent）；若由服务托管，在服务的受限权限环境文件/密钥存储中设置 | `server-b` |
| `local-pc` | 本地 Brain `config.json` 的 `management.token`；若以独立 Agent 运行则用 `VIGIL_MANAGEMENT_TOKEN` | `local-pc` |
| 中心管理服务 | `VIGIL_MANAGEMENT_AGENT_TOKENS` JSON 环境变量；节点名称可由 `VIGIL_MANAGEMENT_AGENT_NAMES` 设置 | 两个节点分别配置 |

## 建议轮换顺序

1. 管理员先确认节点维护窗口、服务管理器和回滚联系人。用密码管理器生成两枚互不相同的高熵 Token；不要复用 Brain Token 或设备 Token。
2. 在中心管理服务的受限密钥注入环境里同时配置新映射，检查服务用户和文件 ACL，然后重启中心服务，使其新进程加载新值。
3. 先更新 `server-b` 的 `VIGIL_MANAGEMENT_TOKEN`，重启该 Agent 服务；确认中心管理页中该节点出现新的在线心跳。
4. 再更新 `local-pc` 的 `management.token`（或其独立 Agent 环境变量），重启本地 Brain/Agent；确认本地节点出现新的在线心跳。
5. 通过运维环境中的授权验证手段确认两台节点均在线；再用维护者控制的旧值做一次**负向验证**，应返回 HTTP 401。旧值仅在运维终端短暂使用，不记入命令日志或工单。
6. 从中心和节点的旧环境配置、安全文件、备份及部署变量中移除旧值。再重启中心和节点服务，并确认新心跳持续正常、旧值仍被拒绝。
7. 将新 Token 存入组织批准的系统凭据存储；不把实际值写入本地项目 JSON。`brain/config/management_agent_tokens.example.json` 仅用于字段说明。

如果新值部署后节点不上线，优先修正新配置并恢复服务；不要重新启用已泄露的旧值。该清单只描述管理员操作，本次没有轮换、验证或连接任何线上凭据。
