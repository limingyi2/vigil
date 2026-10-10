# Vigil 评测记录与对照数据

本目录于 **2026-10-11（Asia/Shanghai）**归档已有结果，没有重新调用模型或执行任务。建议先读[对比报告](comparison.md)，再查看[2026-10-10 测试记录原文](vigil-evaluation-record-2026-10-10.md)。

## 阅读入口

| 材料 | 内容与证据范围 |
|---|---|
| [对比报告](comparison.md) | MiMo 的 A/B 同题对照、overall 与 Planner eligible subset；Qwen 环境修复和单题复测前后 |
| [逐题 A/B CSV](paired_runs.csv) | 8 对任务的通过情况、请求、tokens、工具、耗时及 B−A 差值 |
| [Qwen 已有轨迹 CSV](qwen_runs.csv) | 包含早期环境失败、单题试跑、已有六题汇总和独立 A03 复测；重复来源明确标注 |
| [汇总 JSON](comparison.json) | 可供程序读取的统计和来源说明 |
| [MiMo 第一批原报告](data/mimo-planner-first-batch/summary.md) | 16 个真实 task-run，附 [events.jsonl](data/mimo-planner-first-batch/events.jsonl) 和 [运行元数据](data/mimo-planner-first-batch/run_meta.json) |
| [Qwen 六题历史报告](data/qwen-agent/ollama-agent-smoke-20261011-002805/report.md) | 历史报告已合入 A03 复测；其定性评价和部分统计存在局限，以本目录的来源说明为准 |
| [Qwen 首轮异常记录](data/qwen-agent/ollama-agent-smoke-20261011-002805/failures.md) | numpy / pytest 环境缺口与 A05 Oracle 未通过的原因 |
| [任务与 Oracle 快照](datasets/) | 24 条原任务、6 条 A0x 实战任务、合成 fixture 和 Oracle 源码；这是归档材料，完整执行器尚未随本次数据归档发布 |
| [实验设计](methodology/08-phase2-experiment-plan.md) | MiMo 第一批 8 题 × A/B × 1 次的方案与熔断 |
| [来源与校验和](archive-manifest.json) | 每个来源文件和公开副本的 SHA-256，以及脱敏方式 |

## 结果的比较方式

MiMo 的 A_direct 与 B_current 使用同一批题、同一模型，是本目录唯一完整的 Planner A/B 对照。B_current 包含当前条件式 Checker；没有独立 Checker 组。整体两组都通过 3/8，5 条 Planner eligible 任务的两组都通过 2/5。不能据此宣称 Planner 提升了成功率，也不能用小样本断言它没有价值。

Qwen 的前后比较只展示同一道题的环境修复与复测。当前六题文件中的 A03 已被替换成复测结果，不能称为“原始首轮 5/6”。原 A03 的进度文件仍记录请求数熔断，但完整失败回答与工具轨迹未找到；这些缺口会在对比报告中保留。MiMo 与 Qwen 题集、工具和配置不同，**3/8 与 5/6 不能组成模型升级前后的成功率对比**。

Stub 的 `model_calls` 是模拟计数，真实模型请求数为 0。Stub 通过率只反映 Harness / Oracle 流程，不用于衡量 Agent 能力。原文中的 Qwen H1–H5、长上下文校准与 28K 诊断，目前只归档了用户提供的汇总记录；未找到相应逐次原始文件，不能独立复核这些章节。原文第七阶段的“待执行”描述已被 2026-10-11 的实战产物更新。

## 归档与复算

合成任务中的 `sk-test-12345-synthetic`、`evil.example.com` 和示例配置均为测试 fixture，不是生产凭据。公开副本将本机仓库根路径和用户目录替换为 `<REPO>` / `<USER_HOME>`；其余已有事件、回答和 Oracle 判定保留。用户提供的测试记录按原文件字节保存。生产配置、隔离 home、聊天记忆和模型权重不在本归档内。

运行以下命令只读取本目录数据并生成对比文件，不调用 API：

```powershell
python docs/evaluation/build_comparison.py
```

运行元数据中的 commit 是当时的 Git 基线。MiMo 还保存了相关源码散列；当时存在未提交代码，因此不能只凭基线 commit 认定可完全复现。Qwen 已有环境文件没有记录源码 commit，本次归档没有补造该字段。
