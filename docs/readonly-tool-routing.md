# 智能只读工具路由与流式 Markdown 回归记录

## 根因

原有关键词分类同时承担任务分类和工具可见性控制：`task_intent.classify()` 将普通聊天送到 `chat_no_tools`，把历史检索单独送到 `history_retrieval`；`server._chat_with_tools()` 又对 `chat` 和历史 RAG 设置 `no_tools=True`。SSE 入口则让普通聊天和历史检索走 `Brain.stream_chat()`，而该入口原先不支持 Function Calling。因而模型没有机会根据完整自然语言自行选择只读工具。

分类规则仍离线区分 `chat / retrieval / simple_execution / complex_execution`，负责保护执行任务入口；关键词不再决定普通聊天是否能看见只读工具。普通聊天和检索都进入 Brain 的只读 Function Calling 策略，历史记忆仍由 Brain 现有的会话上下文与向量记忆检索补充。

## 新调用链

1. HTTP 路由保留现有 P19.1 分类和 Agent Run 判定。复杂执行仍进入 Agent Run；简单执行继续走现有入口与权限 Broker。
2. `chat / retrieval` 的流式请求进入 `Brain.stream_chat(..., tool_policy="readonly")`。文本增量仍按 SSE 发出；Function Calling 返回的工具参数只在白名单与渠道功能开关复核后执行。
3. 非流式 Brain 调用使用同一只读白名单和执行复核。
4. 普通聊天与历史检索不调用额外分类模型。未调用工具、工具失败、工具关闭或当前模型不支持 Function Calling 时，系统提示禁止声称已完成实时核实。
5. 只读工具单轮最多执行 4 次；同名工具和完全相同的规范化参数在一轮内只执行一次。超限后不再执行额外工具调用。

## 只读工具审计清单

白名单为精确工具名集合。插件自报的“只读”描述、工具名称或模型返回的 schema 都不会扩大它。

| 工具 | 审计结论 | 实现与副作用范围 |
|---|---|---|
| `search` | 允许 | `brain/plugins/websearch.py::_t_search` 向搜索服务发起查询；结果缓存只写进程内存。不会写文件、配置或安装插件，不执行命令。失败返回明确的未核实说明。 |
| `weather` | 允许 | `brain/tools.py::_t_weather` 读取天气服务；不带城市时复用现有 IP 城市查询和天气缓存。只查询并返回天气，不修改用户设置或持久化记录。 |
| `tracking` | 允许 | `brain/tools.py::_t_tracking` 按单号查询一次物流状态；实现说明明确不会建立持续监控，不写用户数据。 |
| `calc` | 允许 | `brain/plugins/calc.py::_t_calc` 只接受算术字符白名单，以禁用内建对象执行计算；无网络、文件或命令访问。 |

其他工具均不进入普通聊天 schema，包括 `local_file_*`、`local_exec`、`reminder_*`、`capability_call`、`delegate_to_local`、项目修复/审查、插件工具和动态技能。Brain 在调用执行器前再次核对白名单，并通过 `static_tool_enabled()` 复核 `tools` 及对应渠道功能开关。功能状态读取失败时按关闭处理。

## 离线语义评测

样本位于 `vigil-eval/tool_selection_cases.json`，共 46 条，覆盖：搜索 8、天气 4、普通聊天 8、历史 RAG 5、模糊指代 5、简单执行 5、复杂执行 5、权限绕过尝试 6。离线 pytest 验证分类契约、chat/retrieval 共用只读 Brain 入口、搜索/天气工具在启用时可见、历史问题仍具备记忆检索条件，以及执行工具从只读 schema 中排除。

离线结果：样本类别与测试预期 46/46 匹配；12/12 条搜索或天气样本在开关开启时拥有对应只读工具 schema；6 条权限绕过样本不会扩大只读白名单。额外 Function Calling 测试验证未授权执行工具调用被拒、关闭的 `websearch` 工具在执行阶段再次被拒、重复搜索只执行一次。这里的结果衡量路由、工具可见性和权限门禁，不代表模型语义选择准确率。

### 真实模型对照

此工作区未配置 Brain `config/config.json`，也没有发现常见模型 API Key 环境变量。因此没有发起真实模型或联网搜索 API 请求；真实请求数为 0，工具调用数为 0，真实 Token 数为 0，没有重试。真实模型的漏搜率、误搜率、正确工具率和响应延迟尚无可报告数据，需在接入测试凭据后以有限请求补测。离线模拟的两轮 Function Calling 测试仅用于校验 Token 统计与工具执行链，不作为真实模型结果。

## 流式 Markdown 前后对照

- 修复前：token 到达时按动画帧写入纯文本 `textContent`，生成中的标题、列表和代码块不成形；只在 `done` 时渲染 Markdown。
- 修复后：token 合并为最多每 100ms 一次安全 `U.md()` 渲染，再用一个动画帧完成 DOM 更新和自动滚动；完成、失败和中断时会先取消待绘帧并刷新最终 Markdown。SSE 事件、会话身份过滤、页面切换清理和停止/续跑状态保持原协议。
- Electron 生命周期回归实际观察到生成中标题与列表已渲染，终态标题和代码块正确显示，最终快照替换预览且没有重复正文；取消、错误、刷新恢复和订阅清理用例均通过。

## 验证与限制

离线工具链模拟显示一次只读工具往返需要两次模型请求；测试夹具各返回 8 个输入 Token 和 3 个输出 Token，合计 16 输入、6 输出 Token、1 次工具调用。该结果来自本地模拟，不代表真实延迟、真实 Token 或成本。

最终全仓 Python 回归（`tests/` 与 `brain/`）为 1,972 项通过、0 失败、21 跳过，用时 216.89 秒。Node CI 测试为 38/38 通过；Electron 实际流式生命周期回归通过；Python compileall、客户端脚本 `node --check`、GitHub Actions workflow YAML 读取及 `git diff --check` 均通过。工作区权限、插件权限、P19 分类与路由、Agent Run/Workflow、Coding Harness、搜索/HTTP Function Calling 和历史 RAG 都包含在 Python 全仓用例中。

当前变更不修改 P19.1 本地/云端/混合模型路由，也不放宽工作区授权、P19 执行权限或 Agent Run 权限。
