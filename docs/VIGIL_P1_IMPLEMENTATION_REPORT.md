# Vigil P1 实施与针对性验收记录

基线：远程 `origin/dev` 的 `248f3ea025f56159c9a575ca39bd8115e76b5450`。
实施分支：`codex/vigil-p1`，独立受管 worktree；开始时主工作区无未提交修改。

## 交付范围

1. 模型适配（提交 `cef1715`）：增加按服务商、规范化端点和模型隔离的能力缓存；配置变化失效，不缓存密钥。移除主对话 600/4096 分类和 Creator 固定输出额度，统一处理输出参数及完成原因。正常、截断、仅思考、空正文、结构错误和连接中断分别处理；未知异常不去掉工具表重新计费。工具参数必须完整且为对象才执行。
2. Creator 流式作业（提交 `2650ebf`）：SQLite 原子请求去重、快照、只读 SSE 订阅和取消。创作与修改复用主对话流式 DOM 组件、生产 preload 和已有主进程代理。无原生流时展示真实阶段及完整返回，不模拟 token。完成解析后调用既有文件/静态检查，未通过不宣称成功，不执行生成代码。
3. 展示记录恢复（本报告随第三步提交）：主对话新增独立 SQLite 展示存档，Creator 保留作业快照；客户端保存按连接身份、渠道和会话隔离的待确认项及有限部分正文。刷新、重开和断流只查询，不重发模型或工具。展示记录不作为模型历史或记忆输入。清除保留请求墓碑，避免迟到结果重新回显或重放请求；不删除草稿或取消任务。

取消与成功按同一作业锁串行提交；生成内容、思考内容的总接收大小受资源闸门限制。思考增量仅在有限内存事件缓存中，不写入数据库。持久 usage 只接受 token 计数字段；错误只含安全类别、提示和异常类型，不保存原始提供者异常报文。

## API 与兼容

- 新接口：`POST /api/creator/jobs`，`GET /api/creator/jobs`，`GET /api/creator/jobs/<id>`，`GET /api/creator/jobs/<id>/events?after=<seq>`，`POST /api/creator/jobs/<id>/cancel`。
- 创建支持 `request_id`、`kind`、`requirement`；修改额外支持 `draft_id`、`feedback`。同 ID 不同内容拒绝，同 ID 同内容返回原作业。
- 所有作业按认证主体、渠道和 session_id 校验归属。没有放宽设备接口白名单、管理员要求、文件类型或安装审批。
- 主对话新请求带 request_id；已有聊天 API 兼容，历史接口增加 `display_turns` 字段，旧 `items` 字段保留。
- 旧 POST draft/revise 同步返回 `{ok,draft}` / `{ok:false,err}`，内部复用生成作业；其余旧接口保持职责。
- SSE 每次订阅有界，客户端仅重新 GET 订阅/快照；事件缺口由快照说明，不重建思考或谎称补回 token。
- Brain 重启后未结束记录标记 interrupted，不自动续跑。

## 高级配置

现有 config.json 可增加 model_capabilities 对象，字段为 `max_output_tokens`、`input_tokens`、`context_tokens`、`output_parameter`、`requires_output_limit`、`compat_output_tokens`。输入与共享上下文空间采用保守字节估算，不宣称精确 tokenizer 计数。output_parameter 只接受三个已知输出额度字段。

能力未知时默认省略输出参数；明确要求必填时使用管理员兼容值，缺失为 4096。没有用一次成功请求或模型自述推断最大能力。当前未自动接入所有服务商的模型元数据接口，未知就标为 unknown；管理员应依据实际服务端限制配置。

`model_stream: false` 可用于不提供原生 SSE 的 OpenAI 兼容端点；Ollama 原生 NDJSON 当前按非流式处理。`display_records` 集中配置展示存档的 `max_records`（默认 200）、`retention_days`（30）、`max_content_chars`（65536）；客户端待确认项另有限额 100 条、每条 16384 字、30 天。

## 验证证据

全部模型调用使用模拟提供者或本机 HTTP fixture；真实服务商调用数为 0。

| 验收组 | 结果 |
|---|---|
| 模型参数、结束原因、未知模式与断流 | `tests/test_p1_models.py`，6 项 |
| 作业去重、取消、JSON 错误、截断、只读重连、重启及保留上限墓碑 | `tests/test_p1_creator_jobs.py`，8 项 |
| 展示存档、部分正文、身份/会话隔离、去重、清除与保留上限 | `tests/test_p1_display.py`，6 项 |
| 旧模型提供者、原生工具循环、记忆污染与 Creator 审批事务 | 62 项 |
| Creator 旧接口及新增真实 HTTP/SSE，主对话失败不污染历史 | `brain/tests/test_creator_mode.py`，23 项 |
| 新增生产 renderer + preload Electron 验收 | `client/tools/test-p1-electron.js`，12 项 |
| 既有 Electron 界面、历史、图片、模型入口、壁纸及窗口回归 | `client/tools/test-chat-v2-electron.js`，47 项，renderer errors = 0 |
| Python 编译、JS 语法及 git diff --check | 通过 |

Python 主验收组共 105 项；新增及既有 Electron 验收共 59 项。Electron 测试只使用隔离 userData 与服务替身；生产主进程网络代理改动另经代码与语法检查，不把 IPC fixture 误称真实云端端到端验收。

组合测试修正了既有提供者测试将 httpx.post 错误恢复为整个模块的桩污染。既有 Electron 测试同步了 request_id、新的模型选择器/独立设置入口，以及独立场景的本地存储隔离。

额外回归发现 `tests/test_secret_not_leaked.py` 两条旧断言要求末四位和省略号，但 dev 当前掩码为末三位及星号；已在未修改 dev 基线上复现同样失败。本轮不改变密钥掩码行为，也不将这两条列为通过。

## 边界与未验证项

- 没有执行真实服务商、付费模型、真实账号或已有 Codex 会话验收。
- 未重新打包或安装桌面程序；本轮交付源码合并，不自动修改安装中的 1.0.1。
- 未实施 P2 通用执行器、追加预算继续服务或 P3 分文件生成/自动修复；通用恢复工作线继续暂停。
- 不承诺任意模型都公开准确上下文或输出上限；不支持的能力保持 unknown 或明确拒绝。
- 事件缓存有限且思考不持久化，刷新只恢复安全快照及部分正文。历史中的“不完整”不能当成可安装草稿。
- 网络断开不能证明在途副作用已经停止；本期不自动重放、不承诺跨重启续跑。
