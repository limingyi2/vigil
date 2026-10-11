# 混合模式 A/B 对照评测（P3）

- A 组：cloud（无委派工具，云端主模型直答）
- B 组：hybrid（同一云端主模型 + delegate_to_local 有界只读委派）
- 主模型两组相同，差异仅在委派能力；无 PC 客户端轮询，本地文件任务只能靠委派

## 总览

| 指标 | A cloud | B hybrid |
|---|---|---|
| Oracle 通过 | 2/6 | 4/6 |
| 总墙钟(s) | 51 | 197 |
| 主模型输入 tokens | 49331 | 61950 |
| 主模型输出 tokens | 1032 | 2203 |
| 主模型工具调用次数 | 8 | 4 |
| 委派次数/成功 | 0/0 | 3/2 |
| 本地模型 tokens(in/out) | 0/0 | 9317/3941 |
| 委派决策分布 | {'miss': 3, 'correct_skip': 2, 'optional_self': 1} | {'hit': 2, 'miss': 1, 'correct_skip': 2, 'optional_delegated': 1} |

> Oracle 是关键词级自动判定；**模型语义正确但措辞不同可能判 FAIL**，逐条见下表备注与 raw_events.jsonl。

## 逐任务

| 任务 | 类别 | 应委派 | A 结果 | B 结果 | B 委派决策 | B 委派状态 | 备注 |
|---|---|---|---|---|---|---|---|
| H01 | 文件发现（应委派） | 是 | ❌ | ❌ | hit | failed | Oracle 缺关键词：3（可能是表述差异，见 semantic_hint） |
| H02 | 跨文件根因（应委派） | 是 | ❌ | ❌ | miss | — | Oracle 缺关键词：user_id（可能是表述差异，见 semantic_hint） |
| H03 | 日志首因（应委派） | 是 | ❌ | ✅ | hit | succeeded | 第一个真错误是 import smoke test 的 ModuleNotFoundError: No module named 'crypto'；后续 enc |
| H04 | 实时信息（不应委派） | 否 | ✅ | ✅ | correct_skip | — | 未误派且要点齐全 |
| H05 | 内联文本处理（不应委派） | 否 | ✅ | ✅ | correct_skip | — | 未误派且要点齐全 |
| H06 | 计划草拟（委派可选） | 可选 | ❌ | ✅ | optional_delegated | succeeded | plan_draft 可委派也可主模型自己草拟。答案含 user_id/uid 字段统一相关步骤、且是有先后的多步计划（≥3 步或含测试/回归环节）即过。 |

## 委派明细（B 组）

### H01 · file_discovery
- 状态：failed（重试=False）｜本地模型请求 1｜沙箱工具 6｜tokens in/out 789/290｜用时 10s
- 结构化结果：`null`
- 错误：[{'category': 'tool_limit'}]

### H03 · log_root_cause
- 状态：succeeded（重试=False）｜本地模型请求 5｜沙箱工具 8｜tokens in/out 7852/2986｜用时 91s
- 结构化结果：`{"root_cause": "缺少依赖 crypto，import smoke test 抛 ModuleNotFoundError: No module named 'crypto'", "file": "build.log", "line": 39, "reason": "这是构建中第一条真正的错误（traceback 起始于第36行）：烟测执行 import crypto 失败。后续 build.log 42行的 ImportError('encrypt')/AttributeError 是因为 synthetic_core 依赖 crypto 而导入失败导致符号缺失，44-45行的 `
- 错误：[{'category': 'not_found', 'tool': 'file_grep', 'detail': '不是目录：build.log'}, {'category': 'not_found', 'tool': 'file_grep', 'detail': '不是目录：build.log'}]

### H06 · plan_draft
- 状态：succeeded（重试=True）｜本地模型请求 2｜沙箱工具 0｜tokens in/out 676/665｜用时 21s
- 结构化结果：`{"steps": ["在工作区日志目录（如 logs/ 或 *.log 文件）中搜索 user_id 相关报错关键词，定位报错堆栈", "检查 app/api/contacts/routes.py 中 get_contacts_by_user_id 函数，确认其使用 WHERE user_id = ? 精确匹配且无 LOWER() 转换", "检查数据库 schema 定义（如 migrations/ 或 models/ 中的文件），确认 user_id 字段的存储类型和约束", "对比请求参数中的 user_id 值与数据库实际存储值的差异（大小写是否不一致）", "修改 get_cont`
- 错误：无
