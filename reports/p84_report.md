# P8.4 — Vigil 主对话上下文修复与动态压缩

日期：2026-10-19　基线：dev `66282cf`　测试集：新增 `brain/_test_p84_context.py`（18 项，全离线，不碰 30 题）

## 1. 根因与精确位置

**流式聊天历史遗漏**（修改前基线实测复现：`reports/p84_baseline.txt`，1/18 通过）：

- `brain/brain.py` `stream_chat()`（原 1325–1419 行）：流式成功后只调 `_log_turn()` 写**磁盘存档**，**从不写 `self.hist`（内存历史）**。`chat()`（原 1606–1627 行）两条都写。
- 后果：`recent_hist()` 只从内存取最近 12 条 → 流式轮次在重启前对后续所有轮次不可见（"流式聊完立刻问我刚才说什么，答不上来"）；重启后被 `seed_history()` 灌回，症状时有时无，极难定位。
- 次要：流式成功后也不更新 `last_active`。

## 2. 修改清单

| 文件 | 修改 |
|---|---|
| `brain/brain.py` | 新增 `_commit_turn()`（普通/流式统一落账：内存 hist → `_trim_hist_offer()` 裁剪+滑出消息交压缩器 → 生活流水 → 磁盘存档）与 `_trim_hist_offer()`；`chat()` 普通轮改走 `_commit_turn`（主动搭话分支保留原语义）；`stream_chat()` 成功收尾才落账（进内存 hist + 存档），error/截断/断开路径一律不入账；`draft_native()` 与 `stream_chat()` 历史段统一走 `conversation_context.build_context()`（失败回退旧路径）；`clear_src_history()` 同步清压缩状态；新增 `last_context_info` 观测字段 |
| `brain/conversation_context.py` | **新增**：上下文压缩器（见 §3） |
| `brain/server.py` | `_new_agent_run()` 改走同一 `build_context()`（`allow_summarize=False`，创建 run 不额外发摘要请求） |
| `brain/paths.py` | 新增 `CONTEXT_COMPACTION_DIR`（`data/context_compactions/`）并加入启动建目录清单 |
| `brain/_test_p84_context.py` | **新增**：18 项验收测试（FakeProvider 脚本化响应 + 临时 VIGIL_HOME，零网络、零真实数据写入） |

**新增配置**（`config.json`，全部可缺省）：

```json
"context_compression": {
  "enabled": true,                // 总开关，false = 完全回到旧行为
  "recent_keep": 12,              // 最近完整保留的消息条数（默认=CTX_TURNS）
  "summarize_after_dropped": 12,  // 滑出累计多少条后触发一次摘要折叠
  "summary_max_chars": 900,       // 摘要正文上限
  "summarize_timeout": 45,        // 摘要请求独立超时（秒）
  "min_keep": 4,                  // 超预算裁剪时最近对话保底条数
  "pending_max_messages": 500,    // pending 暂存上限（超限丢最旧，原文仍在存档）
  "default_capacity": 8192        // 容量完全未知时的保守默认
}
```

## 3. 压缩器设计（brain/conversation_context.py）

- **隔离键 = `persona_key + src + conversation_id`**（legacy 会话 conversation_id=None → `"legacy"` 键），每键一个状态文件 `data/context_compactions/<sha1>.json`，原子写（临时文件 + `os.replace`，写失败旧文件原样保留）。新会话不继承旧摘要；换人设/渠道/会话/模型均不串。
- **触发**：① 当前上下文超预算（硬性）；② 滑出窗口的消息累计 ≥ `summarize_after_dropped`（§3 的"逐步合并"——窗口放得下不等于旧事实还在，不折叠就永远悬在 pending 里）。短对话两条都不满足 → 零额外请求。
- **预算**：`容量 − 输出预留 − system − 工具表 − 256 余量`。容量：本地 Ollama 用真实 `num_ctx`（source=`ollama_num_ctx`）；云端用配置的 `context_tokens/input_tokens`（source=`configured`）；都没有退保守 8192 并标 `unknown_default`。
- **Token 纪律**：决策一律用启发式估算（CJK≈0.9 tok/字、其它≈0.3 tok/字符，宁高勿低），**字节数从不当 token**；摘要请求的真实 `usage` 单独记进 stats（`tokens_in/tokens_out`），两者不混用。
- **摘要生成**：**直连 provider 的单次请求**（temperature=0、独立超时、每轮至多 1 次），绝不递归进 `Brain.chat`（不拿聊天锁、不写历史、不可能死循环）。provider 按当前 `model_mode` 取 —— 本地模式只发本地，不偷偷调付费云端。
- **摘要内容要求**（写进摘要 system 提示）：精确数值/日期/版本号/Git SHA/文件路径一字不改；已确认的决定、项目状态、未完成任务与下一步必留；冲突以新对话为准。
- **失败安全**：摘要失败 → 旧摘要与 pending 一字不动、水位线不推进、失败计数+1；本轮退化为"仅裁剪历史"。原始聊天记录永远只在存档里追加，压缩器无删除路径。
- **FC 协议**：压缩只作用于进入 `self.hist` 的纯 user/assistant 消息；工具调用的 `assistant.tool_calls`/`role:tool` 配对只存在于 `draft_native` 循环内部，从不进 hist，结构不可能被压坏（有测试断言）。
- **单条消息超长**：不截断（任务指令一个字不丢），`info["user_overflow"]=True` 明确报告并照发。

## 4. 修改前后对照（同一测试集）

| # | 验收项 | 修改前 | 修改后 |
|---|---|---|---|
| 1 | 流式成功进内存历史并影响下一轮 | **FAIL** | PASS |
| 2 | 连续三轮流式可引用前两轮 | **FAIL** | PASS |
| 3 | 流式失败/截断不写历史 | PASS | PASS（行为保持） |
| 4 | 流式成功写磁盘存档 | PASS | PASS（行为保持） |
| 5 | 短对话不触发压缩、零额外请求 | — | PASS |
| 6 | 40 条长对话第 3 句关键事实（数值/SHA/路径）可召回 | — | PASS |
| 7 | 20/50/100 条长对话稳定 | — | PASS |
| 8 | 摘要超长安全截断（段落边界） | — | PASS |
| 9 | 两个 PC 会话互不串话、渠道不混用 | — | PASS |
| 10 | 重启（新 Brain 实例）后摘要恢复，不重新摘要 | — | PASS |
| 11 | FC 工具配对不破坏 | — | PASS |
| 12 | 摘要失败/provider 不可用：原文与 pending 完好、水位线不推进 | — | PASS |
| 13 | 清空会话后摘要不复活 | — | PASS |
| 14 | 本地模式摘要走本地 provider（容量=真实 num_ctx） | — | PASS |
| 15 | 单条消息超长明确报告不截断 | — | PASS |
| 16 | 开关关闭完全回旧行为 | — | PASS |
| 17 | 三入口（draft_native/stream_chat/agent run）同一构建函数 | — | PASS |
| 18 | 摘要真实 token 计量来自 usage | — | PASS |

基线留档 `reports/p84_baseline.txt`（1/18）；修改后 `reports/p84_after.txt`（18/18）。

**全量回归**：`reports/p84_regression2.txt`（tests/ + brain/ 全量，结果见文末记录）。

## 5. Token 与新增请求成本

- 摘要请求：仅在有滑出/超预算时发生，**每轮至多 1 次**。真实 usage 落在状态文件 stats（`requests/tokens_in/tokens_out/failures/last_error`），可在 `data/context_compactions/*.json` 与 `Brain.last_context_info` 观测。
- 实测数字：本验证为 FakeProvider 离线验收，**真实模型下的摘要 token 成本 unavailable**（不编造）。量级参考：一次摘要 ≈ pending 全文（≤500 条）+ 摘要提示词 ≈ 数千 token 输入 + ≤900 字输出；按"滑出 12 条才折叠一次"摊销，常态长对话每 6 轮一次额外请求，短对话为零。

## 6. 已知边界与回退

- 压缩摘要发生在聊天锁内（`build_context` 在 `draft_native`/`stream_chat` 调用链上）：触发折叠的那一轮最多多等 `summarize_timeout`（默认 45s）。若不可接受，把 `summarize_timeout` 调小或 `enabled=false`。
- 混合模式（hybrid）主模型走云端，摘要同样走主模型链路（与聊天同价，不偷跑本地/云端）；本地委派的 dispatcher/contracts/router/compact 与 30 题评测集未动。
- 回滚路径：`config.json` 设 `"context_compression": {"enabled": false}` 即恢复旧行为（`_commit_turn` 的流式历史修复仍在，属于 bugfix 不受开关控制）；或 revert 提交。

## 7. 交付

- dev 提交：见 git log（本次 P8.4 提交 SHA）
- 候选安装包：`Vigil.Setup.1.1.4.exe`（源提交、大小、SHA-256 见 manifest 与文末）
