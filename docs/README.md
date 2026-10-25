# 文档索引

这里只放**现在还有效**的文档。分四类，看之前先确认自己要哪一类。

写文档的规矩：新增文档要登记到本页；一次性的交接、验收、过程记录不往 `docs/` 根目录放，
写进 PR 描述或 `CHANGELOG.md`。历史文档在 `git log -- docs/` 里能翻到。

## 一、手册

| 文档 | 作用 |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | 总手册：装起来跑通、配模型、写插件和技能、知识库、打包、测试 |

## 二、契约（已冻结，改行为前必读）

契约描述"应该怎样"，和代码不一致时先改契约再改代码。

| 文档 | 管什么 |
|---|---|
| [VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md](VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md) | 主对话与 Agent 执行的统一协议：路由、预算、状态、事件、旧接口兼容 |
| [VIGIL_CREATOR_STAGE1_CONTRACT.md](VIGIL_CREATOR_STAGE1_CONTRACT.md) | Creator 的作业、草稿、流式与取消重连协议 |
| [VIGIL_TASK_RECOVERY_STAGE1.md](VIGIL_TASK_RECOVERY_STAGE1.md) | 任务恢复：检查点、显式继续、重启后在途任务的处置 |
| [P9_extension_protocol.md](P9_extension_protocol.md) | 扩展清单、权限、跨组件兼容规则 |
| [P9_data_migration.md](P9_data_migration.md) | 扩展的数据保护与回滚约定 |
| [P19_1_ROUTING_CONTRACT.md](P19_1_ROUTING_CONTRACT.md) | 路由收敛基线与兼容边界 |
| [P19_FINAL_TASK_ROUTING.md](P19_FINAL_TASK_ROUTING.md) | 任务分类、执行位置路由与流式行为的最终约定 |

## 三、设计与使用说明

**设计底稿**只解释"为什么这么设计"，不代表现状，状态行已标注：

| 文档 | 内容 |
|---|---|
| [VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md](VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md) | 执行优化的上层方案（P0/P1/P2 已实施） |
| [VIGIL_CREATOR_OPTIMIZATION_PLAN.md](VIGIL_CREATOR_OPTIMIZATION_PLAN.md) | 插件系统与 Creator 的上层方案 |
| [混合模式方案-云端本地模型协同.md](混合模式方案-云端本地模型协同.md) | 云端模型与本地模型协同的落地设计 |
| [RAG混合检索系统设计与优化.md](RAG混合检索系统设计与优化.md) | 知识库检索的混合策略与调参依据 |
| [04-大脑/设计与取舍.md](04-大脑/设计与取舍.md) | 能力构成、踩过的坑、以及故意不做的选择 |
| [03-客户端/客户端详细架构方案-v2.0.md](03-客户端/客户端详细架构方案-v2.0.md) | 客户端分层、进程边界与改版裁决 |

**使用与运维**：

| 文档 | 内容 |
|---|---|
| [04-插件市场使用指南.md](04-插件市场使用指南.md) | 插件市场来源、独立安装目录、装与卸 |
| [P21-可选插件独立分发清单.md](P21-可选插件独立分发清单.md) | 哪些插件随包、哪些必须单独安装 |
| [统一管理功能-合并方案与使用说明.md](统一管理功能-合并方案与使用说明.md) | 多节点监控页、心跳与 Token 配置 |
| [DEPLOY_ENVIRONMENT_VARIABLES.md](DEPLOY_ENVIRONMENT_VARIABLES.md) | 部署所需环境变量 |
| [KESTREL_HUB_BRIDGE.md](KESTREL_HUB_BRIDGE.md) | 可选 Hub Bridge 的只读能力与凭证 |
| [03-客户端/客户端打包与推送更新流程.md](03-客户端/客户端打包与推送更新流程.md) | 出包与推送更新 |
| [PC_CORE_LEGACY_WECHAT_CLEANUP.md](PC_CORE_LEGACY_WECHAT_CLEANUP.md) | 微信渠道与双 Brain 的退役边界 |
| [05-微信/P8.2-微信插件化迁移清单.md](05-微信/P8.2-微信插件化迁移清单.md) | 微信接入的迁移背景（历史提案，非现状） |

## 四、待办、评测与素材

| 位置 | 内容 |
|---|---|
| [02-开发指南/回归检查表.md](02-开发指南/回归检查表.md) | 发版前必过的检查项 |
| [03-客户端/客户端开发待办.md](03-客户端/客户端开发待办.md)、[05-微信/微信开发待办.md](05-微信/微信开发待办.md) | 各端未完成项 |
| [evaluation/](evaluation/) | 评测方法、数据集、对照数据 |
| [agent-improvement/](agent-improvement/) | 自动评测与改进闭环 |
| [releases/](releases/) | 历史版本发布说明 |
| [archive/](archive/) | 明确废弃的旧方案，只作查证用 |
| [assets/](assets/)、[\_shots/](_shots/)、[examples/](examples/)、[schemas/](schemas/) | 图片、界面截图、清单示例、JSON Schema |

> 本地专用、不入库的文档（`.gitignore` 里 `docs/*清单*.md`、`docs/*记忆*.md` 等）不在本索引内。
