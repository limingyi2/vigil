# 文档

安装版功能以 [使用说明](USER_GUIDE.md) 和对应 Release 为准。开发源码位于 `dev` 分支；接口契约、设计稿与历史记录分开列出。

## 使用与维护

| 文档 | 内容 |
| --- | --- |
| [使用说明](USER_GUIDE.md) | 模型、对话、工具、专家、资料和扩展 |
| [架构](ARCHITECTURE.md) | 进程、模块、权限、数据与执行流程 |
| [运维](OPERATIONS.md) | 数据目录、备份、恢复和故障排查 |
| [客户端开发](../client/README.md) | 环境、目录、IPC、调试和检查 |
| [打包与发布](03-客户端/客户端打包与推送更新流程.md) | 版本、安装验收与更新文件 |
| [参与开发](../CONTRIBUTING.md) | 提交、测试和文档约定 |

## 功能专题

- [插件市场](04-插件市场使用指南.md)、[插件开发](04-大脑/插件系统.md)、[JS 技能](04-大脑/JS技能支持.md)。
- [上下文与记忆](04-大脑/记忆与上下文.md)、[混合检索](RAG混合检索系统设计与优化.md)。
- [视频分析接入](EGOAGENT_VIGIL_INTEGRATION.md)、[外部应用与本地补丁](local-integrations-and-patches.md)。
- [Kestrel Hub Bridge](KESTREL_HUB_BRIDGE.md)：可选的只读设备信息连接。
- [可选插件分发](P21-可选插件独立分发清单.md)、[客户端设计系统](03-客户端/客户端设计系统.md)。

## 接口与设计契约

这些文件用于说明接口、兼容规则或阶段约定。契约中的计划范围不应直接当作当前已交付功能。

| 文档 | 范围 |
| --- | --- |
| [Agent 执行契约](VIGIL_AGENT_EXECUTION_STAGE1_CONTRACT.md) | 模型、工具、状态、事件、预算和兼容 |
| [Creator 契约](VIGIL_CREATOR_STAGE1_CONTRACT.md) | 作业、草稿、安装和流式协议 |
| [任务恢复契约](VIGIL_TASK_RECOVERY_STAGE1.md) | 检查点、显式继续和重启处置 |
| [扩展协议](P9_extension_protocol.md) | 清单、权限和组件兼容 |
| [数据迁移约定](P9_data_migration.md) | 数据保留、迁移和回滚 |
| [P19 路由基线](P19_1_ROUTING_CONTRACT.md) | 该阶段的路由兼容约束 |

## 设计与历史资料

[archive/](archive/) 保存早期方案；[Agent 评测记录](agent-improvement/README.md)、[评测方法与数据](evaluation/README.md) 和 [reports/](../reports/README.md) 对应各自记录的提交与环境。历史性能数字不代表当前版本的通用表现。

以下文件保留用于追溯，不能直接用作当前操作步骤：

- [客户端早期架构](03-客户端/客户端详细架构方案-v2.0.md)、[客户端开发待办](03-客户端/客户端开发待办.md)。
- [早期推理方案](04-大脑/推理架构方案.md)、[早期设计取舍](04-大脑/设计与取舍.md)。
- [云端与本地模型协同方案](混合模式方案-云端本地模型协同.md)、[统一管理早期方案](统一管理功能-合并方案与使用说明.md)。
- [P19 流式路由记录](P19_FINAL_TASK_ROUTING.md)、[旧微信清理记录](PC_CORE_LEGACY_WECHAT_CLEANUP.md)。
- [Agent 优化方案](VIGIL_AGENT_EXECUTION_OPTIMIZATION_PLAN.md)、[Creator 优化方案](VIGIL_CREATOR_OPTIMIZATION_PLAN.md)。

新增或修改使用步骤时，同步更新相关入口。涉及接口行为变化时，同时核对实现、契约和测试；仅调整文字时保留字段名、状态值、指标来源与第三方署名。
