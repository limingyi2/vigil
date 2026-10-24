# MiMo 只读工具协议与错误状态修复

基线：`dev` 合并提交 `ac09d7eaf2f07011d945f0e3e5740988bcb829f4`。

## 根因与实际出错链

`/api/chat/stream` → `Brain.stream_chat(tool_policy="readonly")` → `_stream_readonly_tool_chat()` → `completion_events()`。

1. `completion_events()` 正确收集原生参数增量，但 `normalize()` 在 `reason=tool_calls` 时保留了默认 `complete=False`。真实原生工具响应会被只读 Brain 当作模型请求中断。旧的只读单测直接提供 `complete=True`，没有经过实际 SSE 解码，因此未发现该问题。
2. 只读流式入口把所有 `text_delta` 当作正文发送，未复用已有 `ToolTextBoundary`。MiMo 返回的 XML 标签因此进入客户端正文；没有原生调用时，还可能作为回答进入历史，影响下一轮能力判断。
3. 真实 `mimo-v2.6-flash` 响应同时包含不完整原生参数 `{"query": ` 和跨多个 Chunk 的完整 XML。仅仅接受 `tool_calls` 或仅检查 `supports_tools` 都不够。修复后的选择顺序是：完整合法原生调用优先；显式启用 XML 的提供商在原生调用缺失或参数不完整时，使用完整且通过校验的 XML。
4. 能力、协议、执行错误没有分开；模型可能把失败说明解释成工具不存在。现在每轮重建授权快照，执行失败不会改变提供商能力或下一轮工具清单。

## 协议对照

| 项目 | 原生 FC | MiMo XML 兼容路径 |
|---|---|---|
| 能力标志 | `supports_tools` | 必须显式 `supports_xml_tools` 且 XML 开关启用 |
| 请求 | 已授权只读 `tools` schema | 同一组已授权 schema；MiMo 接受该请求格式 |
| 响应 | `delta.tool_calls`，参数增量合并 | `content` 中 `<tool_call><function=...><parameter=...>` |
| 展示边界 | 普通正文按原样流式显示，不从正文额外执行 XML | 复用 `ToolTextBoundary`，跨 Chunk 标签缓冲并隐藏；Markdown 中的代码示例保持字面显示 |
| 执行条件 | 完整参数 JSON、必需字段与类型校验 | 所有标签闭合、严格结构、无重复参数、整批参数校验 |
| 回灌 | `assistant.tool_calls` + 配对 `role:tool`；按 MiMo 适配约定保留 `reasoning_content` | 复用既有 XML 的 `user` 工具结果消息；不回灌原始 XML 或无效原生参数 |

小米当前[公开 API 文档](https://mimo.mi.com/docs/api/text-generation/openai-api)描述标准原生工具调用；[推理字段回传说明](https://platform.xiaomimimo.com/docs/en-US/usage-guide/passing-back-reasoning_content)要求保留相关推理上下文。这里的 XML 兼容处理依据本仓库已有提供商显式开关以及下面的真实响应证据，不将 XML 认作所有模型的默认协议。

原生调用有效时不额外执行伴随 XML；仅支持原生协议的提供商永远不会因正文 XML 获得执行路径。整批 XML 或参数有一个不完整，整批不执行。白名单、schema 可见性、渠道功能开关复核、最多四次调用和相同参数去重继续生效。

## 错误与能力状态

| 状态 | 含义与 SSE |
|---|---|
| `tool_unavailable` | 未授权、未出现在当前 schema 或渠道关闭；调用被拒绝 |
| `provider_unsupported` | 已授权工具存在，但当前模型没有可用调用协议；与工具授权分开报告 |
| `tool_protocol_error` | 标签、参数结构、长度或调用次数限制错误；安全拒绝 |
| `tool_execution_failed` | 实际工具执行失败；`tool/returned` 保留 `error=true` 并携带该 class |
| `model_request_failed` | 模型请求异常或流中断；终态 `error` 保留底层 reason |

`model_request_started` 增加 `tool_status/available_tools`，其事件类型保持不变。工具失败仍按既有结果协议传给模型。搜索未得到任何成功结果时，最终正文固定为：**“联网搜索调用失败，本次没有成功获取搜索结果。”** 后续失败解释不会再把工具执行失败说成没有联网功能；下一轮仍按配置重建相同工具能力。

## SSE 修复前后

修复前的复现：`text("<tool_call>...")` → 原始 XML 显示/可能入历史，或原生 `tool_calls` → `complete=False` → 请求报错。

修复后真实 MiMo 成功链：`model_request_started(available)` → XML 分片被边界隐藏 → `tool(search,started)` → `tool(search,returned,error=false)` → 自然语言 `text` 增量 → `done`。真实终态及保存历史均不含工具 XML。

可运行 `python vigil-eval/mimo_sse_comparison.py`，用同一组逐条 SSE 对比基线与当前实现。[前后对照记录](evaluation/mimo-sse-before-after.json)显示：XML 分片场景基线发生正文与历史泄漏、原生 FC 基线终止为错误、混合协议基线发生正文泄漏并终止；修复后这三组都执行一次工具、以自然语言完成且无 XML 泄漏。泄漏检查会先拼接全部文本增量，覆盖标签跨 Chunk 的情况。

离线 SSE 测试使用真实 `completion_events()` 消费逐条 SSE，而非直接伪造完成状态，覆盖单次 XML、连续两次搜索、逐字符拆分标签、原生参数分片、混合原生残片与完整 XML、结果回灌、搜索失败、模型中断、不支持工具、未授权工具、关闭工具、非法参数、未闭合/超长 XML，以及同一 Brain 连续两轮能力保持一致。非流式只读入口同样使用严格 XML 和参数校验，并覆盖混合协议与失败状态。

## 真实 MiMo 调用证据

仅发送独立的公开测试问题“请联网搜索 Xiaomi MiMo 的官方网站，返回一个真实来源链接和一句简短说明。”，不发送用户历史或人设，不保存密钥与推理正文。

共进行四次有界定位/验收运行：前三次各一条模型请求，在参数校验处安全停止，未执行工具；最后一次是修复后的成功闭环。合计 **5 次模型请求、1 次真实搜索、0 次传输重试**；输入 **2,642 Token**，输出 **403 Token**，合计 **3,045 Token**。没有循环重试。

最终闭环使用 **2 次模型请求、1 次搜索**，输入 1,031 Token、输出 239 Token；总耗时 15.594 秒，其中搜索 3.687 秒。第一条响应实际携带一个原生参数残片和一个完整 XML 调用，第二条响应生成自然语言来源说明。SSE 与历史 XML 泄漏计数均为零。

- [总请求与用量记录](evaluation/mimo-readonly-live-summary.json)
- [修复后完整脱敏记录](evaluation/mimo-readonly-live-evidence.json)
- [混合协议定位记录](evaluation/mimo-readonly-live-mixed-before.json)
- [初始定位记录](evaluation/mimo-readonly-live-initial.json)
- [原生参数不完整定位记录](evaluation/mimo-readonly-live-native-invalid.json)

前两次定位未捕获 XML 计数，记录为 `null`；第三次计数由已记录的正文分片计算，避免把未测量误写成零。所有协议失败、权限拒绝与中断场景的覆盖来自离线回归，未声称已对真实服务逐项制造故障。

## 回归结果

Python 全仓采集 2,011 项：1,990 通过、21 跳过、0 失败/错误，耗时 214.553 秒。新增协议定向回归通过。Node CI 38/38、实际 Electron 流式 Markdown/失败/取消/恢复生命周期通过，compileall 与 diff 空白检查通过。GitHub Actions 最终结果在 PR 描述中记录。

未修改 P19 分类、路由、Agent Run 执行框架或客户端 Markdown 实现。
