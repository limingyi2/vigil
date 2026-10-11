# 混合模式效率优化 P0：调用链审计与基线

日期：2026-10-11。依据任务书 `vigil-hybrid-efficiency-optimization-v1`，先审计后改造。
基线批次：`hybrid-compare-20261011-121938`（cloud 51.3s / 49,331+1,032 tokens，
hybrid 197.1s / 61,950+2,203 tokens，委派 3 次成 2 次）。

## 1. 真实生产调用链（混合模式一次委派）

```
用户消息
└─ server.py /api/chat → Brain.chat()（持聊天锁）
   └─ Brain.draft_native()
      ├─【P1 新增】delegation.router.classify() 确定性路由
      │   ├─ local_direct   → bridge.run_local_direct → Dispatcher → 直接回用户（0 云端请求）
      │   ├─ hybrid_delegate/cloud_direct → 继续原链路
      ├─ 云端模型请求 #1（system+历史+工具表，含 delegate_to_local 规格）
      │   └─ 模型返回 tool_call: delegate_to_local
      └─ tools.invoke_spec → bridge.delegate_to_local()
          ├─ is_delegation_enabled 门控（model_mode=hybrid + enabled + ollama 已配置）
          ├─ contracts.validate_contract（kind 白名单/工作区授权根/限额）
          └─ dispatcher.run_delegation（**同步阻塞**在云端这一轮 HTTP 内）
              ├─ Ollama 请求序列（≤6）× 沙箱工具（≤8）× 180s 墙钟
              ├─ validator 程序验收（失败带修正意见重试 1 次）
              └─ 结果落 data/delegations/*.json
      云端模型请求 #2（回灌文本作为 role:tool 消息）→ 模型改写为最终答复
```

关键事实：Dispatcher 是**进程内同步**调用，委派期间云端 HTTP 请求挂着；
云端至少 2 次请求（委派决策 + 回灌总结）。

## 2. 基线 tokens / 墙钟增长的具体原因（对照原始事件逐条核）

1. **回灌全文转储（最大头）**：旧版 `bridge._render` 把本地模型完整 `output`
   回灌云端。H03 本地输出 2,986 tokens（含大段 JSON `reason` 复述日志），
   云端第 2 次请求全量照收再改写 → H03 云端输入 10,347、输出 387。
2. **重复总结**：本地模型已经写了一遍完整结论，云端又写一遍（H03/H06 输出
   分别 387 / 1,195 字），两份内容语义重复。
3. **本地循环本身慢**：H03 本地 5 个模型请求、8 次工具、91.3s；其中两次
   `file_grep(path=build.log)` 把文件当目录，得到「不是目录」后原样重试，
   白白烧掉 2 次工具往返和 1 个模型轮次。
4. **H01 能力缺陷 + 预算计量**：file_discovery 合同只给 file_list/file_find，
   **没有 read_file**，模型即使找到 app.conf 也读不到 retry_count 的值；
   且单轮并行 6 个试探调用直接撞 6 次工具硬顶，随即判 failed，**没有最终
   回答机会**。
5. **H06 零证据通过**：plan_draft 无工具、不要求取证，模型幻觉出
   `app/api/contacts/routes.py` 等不存在路径，仅靠主模型降级标注兜住。
6. **无路由层**：简单本地查询也要「云端规划 + 本地 ReAct + 云端总结」整条链路，
   云端请求数最低也是 2。
7. H02 是云端安全分类器在委派发起前拦截（`non-reasoning model flagged
   tool call as unsafe`），与委派链路无关，需复测量化频率。

## 3. 本次最小补丁清单（不跨模块边界，可逐条回滚）

| # | 文件 | 改动 | 对应 |
|---|---|---|---|
| 1 | `delegation/router.py`（新） | 确定性三路路由（纯函数，只认真实授权路径） | P1 |
| 2 | `brain/delegation/dispatcher.py` | 紧凑输出 schema；工具预算用尽留最终回答轮（degraded 标记，不伪造）；read_file 内容/grep 命中行留档；分段计时；只读结果任务内缓存（文件版本失效） | P2/P3 |
| 3 | `delegation/validator.py` | answer/证据条数/摘录长度硬顶；evidence 逐条程序核验（path 真实存在 + excerpt 出自实读内容 ±2 行）；plan_draft 有工作区必须取证、无工作区标 generic | P2 |
| 4 | `delegation/contracts.py` | file_discovery 加 read_file、限额 5/8；plan_draft 工作区可选、带工作区时开放只读工具；router_enabled 配置 | P3 |
| 5 | `delegation/sandbox.py` | file_grep 的 path 允许传单文件（受控兼容+提示）；version_of 缓存版本戳 | P3 |
| 6 | `delegation/bridge.py` | `_render` 改为短证据回灌（≤1800 字，只用核验条目）；run_local_direct 入口（失败必回退云端） | P1/P2 |
| 7 | `brain/brain.py` | draft_native 前置 local_direct 挂点（仅 hybrid+开关）；云端请求数/模型耗时记账 | P1/P0 |
| 8 | `brain/model_hub.py`、`config.example.json` | delegation.router_enabled 白名单与样例 | P1 |
| 9 | `vigil-eval/run_hybrid_compare2.py`（新） | A 在线PC/B 旧版worktree/C 优化版三路对照、重复交错、P0 全指标 | P0/P4 |

**明确没做（守边界）**：不碰云端安全门、不提任何限额硬顶、不新增写/执行/
联网工具、不造第二个 Brain/Agent 框架、不让本地结果不经云端审核直接冒充
主模型回复（local_direct 只对可程序化校验的简单查询生效）。

## 4. 可运行基线命令

```powershell
# 单元回归（不联网）
python -m pytest tests/test_delegation.py tests/test_model_providers.py
python -m pytest                      # 全量：基线 26 failed 为既有失败集

# A/B/C/O 真实对照（需 Ollama 在线 + 云端 key）
python vigil-eval/run_hybrid_compare2.py --groups ABCO --repeats 3
# think 开关受控对照（P3.4）
python vigil-eval/run_hybrid_compare2.py --groups C --only H01,H03 --think false --tag thinkoff
```

## 5. 指标口径（P0.2）

每轮记录：`route`、`cloud_requests`、`cloud_input/output_tokens`、
`local_requests`、`local_input/output_tokens`、`pc_tool_calls`、
`local model_seconds`、`cloud_model_seconds`、`pc_tool_seconds`、
`validation_seconds`、`end_to_end_seconds`、`oracle_pass`、`verified`、
`degraded`、`fallback/run 错误原因`。本地 tokens 来自 Ollama usage，
云端 tokens 来自主模型 usage；墙钟为整轮端到端。中位数按任务分组统计，
成功率单列，不只对成功样本计时。
