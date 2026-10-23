# PC Core 微信遗留清理验收记录

日期：2026-10-23。分支：`codex/remove-legacy-wechat-core`，基于 GitHub 最新 `dev` 的 `886639b`。不修改 master，不合并其他功能分支，不处理 P18 Todo/Checkpoint 或创作模式改版。

## 清理与保留

- 移除 Core 微信 cloud/local 路由开关、WebSocket Hub 接管、消息转发、超时替代回复、迟到回复补写、入站图片自动收藏和群聊前情注入。相关转发源码与旧专属测试归档至 `wechat-bridge/archive/core-forward/`，不再参与 Core 启动和正式测试。
- PC 客户端移除微信人设页签、旧渠道历史入口；本机人设可编辑和切换。共享插件钩子、Skill、Capability Registry、工具权限、Coding Harness、Agent Run、上下文压缩和历史检索继续保留。
- 保留旧配置、聊天、记忆和 Bridge 归档，不迁移、合并或删除。存储层仍能显式计算旧渠道路径。旧 `/api/route`、`/ws/brain` 及微信渠道请求返回 410；未知渠道返回 400，不静默落到 PC。
- `pc_context` 统一运行渠道和有效人设：已有显式 PC 绑定优先，否则使用 `persona_file`。应用新 PC 人设会更新该文件项并取消旧 PC 覆盖，其他渠道配置保留。历史、记忆、系统提示、会话与检索采用同一人设标识。
- 会话按 PC、人设和 ID 隔离，显示记录与 Agent Run owner 也包含人设。旧空人设索引仅凭当前人设的独立 PC 存档验证后兼容读取；旧 Agent owner 仅在唯一归属可证明时恢复。无归属证据的旧 main/welcome 显示记录原位保留，不自动分配给任何人设。
- 工作区元数据按验证所得 owner/persona 查找，支持恢复旧 PC 工作区并显式重命名、删除或用于新会话。工作区注记使用请求隔离的 ContextVar，Agent 上下文在锁内设置并清理。
- 不纳入 PR #25 的 recap_store 删除；当前 dev 中仍在运行的 recap 路径保留，后台活跃渠道改为 PC。

## 测试

在隔离 `VIGIL_HOME` 下运行：

```text
python -m pytest tests brain/tests brain/test_planner_execution_auth.py -rA --tb=short
1718 passed, 17 skipped in 158.31s
```

跳过项包括真实模型流式验收所需凭据、当前环境不可用的 symlink/junction/mkfifo，以及仓库不包含的历史 UI 资产。没有把这些跳过项算作通过。

新增 `tests/test_pc_core_context.py` 共 23 项验收通过，覆盖省略 src 的实际 HTTP 落盘、退役入口无执行/写入、双人设与同 ID 隔离、默认系统提示、切换恢复、长期记忆重新打开、旧数据字节保留、统一检索默认分区、旧 PC 索引及工作区恢复、旧 Agent Run 恢复与并发工作区注记。

独立 ChatGPT 只读代码验收已完成；提出的跨人设 owner、旧工作区及共享注记问题均修复，独立复跑上述 23 项通过，未发现残留阻断问题。

`node --check` 检查 `client/src/js/pages.js` 和 `_devstub.js` 通过；`git diff --check` 通过。没有构建 Windows 安装包。

测试过程中曾因只替换 CONFIG_PATH 而未替换 CONFIG_DIR，误写本机未跟踪配置的 persona_file。已按用户确认恢复为 `personas/preset_group.json`，并补齐隔离；所有后续测试均使用独立临时 home。该本机配置不提交。

## 待合并 PR 预检

使用 `git merge-tree --write-tree --messages` 进行只生成 Git 对象的合并预检，没有修改分支或工作区：

| PR | 结果 |
|---|---|
| #22 RAG 验收修复 | 无文本冲突 |
| #23 语义检索开关（基于 #22 分支） | 无文本冲突；未纳入其功能 |
| #24 工具参数诊断 | 无文本冲突 |
| #25 recap_store 清理 | `brain/paths.py` 内容冲突；`tests/test_recap_scope.py` modify/delete 冲突 |
| #26 壁纸透明度修复 | 无文本冲突 |
| #27 Agent 故障检查 | 无文本冲突 |
| #21 Wallpaper work 集成 | 两处壁纸文件冲突；同样存在于 dev 与 work 的基线预检，本次未修改这些文件 |

#25 集成时需保留本次 PC 活跃渠道与默认路径规则，同时采用 #25 对旧摘要目录及测试的删除。无文本冲突不代表集成后的语义回归已验证；本次没有合入任何待合并 PR。

## 仍需真实 Windows 客户端验证

Electron 人设卡片、切换后的历史恢复、工作区选择与显示；Coding Agent 暂停/续跑；工具授权弹窗；SSE 思考、工具事件与正文显示。源码测试不替代真实模型、客户端和扩展生命周期的交互验证。
