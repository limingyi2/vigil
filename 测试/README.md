# Vigil 测试报告

报告日期：2026-10-11。项目定位：独立桌面 Agent，云端提供可选同步与远程入口。

| 报告 | 范围 | 结果 |
|---|---|---|
| [混合模式效率优化 A/B/C 对照报告](2026-10-11-混合模式效率优化ABC对照报告.md) | A 云端在线PC / B 旧版委派 / C 优化版 三路重复对照 + think 开关 | C 14/14、委派 10/10 全核验；对 B 云 token −28%~−100%；离线可达 10/10 |
| [混合模式 A/B 对照评测报告](2026-10-11-混合模式AB对照评测报告.md) | 6 题 × cloud/hybrid 两组真实调用，含 2 个不应委派样本 | A 组 2/6、B 组 4/6；委派 3 次成 2 次，负样本零误派 |
| [Qwen 本地 Agent 实战报告](2026-10-11-Qwen本地Agent实战报告.md) | 六道隔离工具任务，含 A03 环境修复后复测 | 合入复测后的结果为 5/6；A05 保留失败判定 |
| [本地知识库验证报告](本地知识库验证报告.md) | 知识库模块回归、真实本地模型调用知识库工具 | 28 项回归通过；有资料与空库两项接线验证通过 |
| [历史 A/B 对照](../docs/evaluation/comparison.md) | MiMo 同题 Planner 对照与已有复测记录 | overall 3/8 对 3/8，Planner eligible subset 2/5 对 2/5 |

本目录保存正式报告与对应证据。[本次六份实验产物](原始数据/20261011-qwen-agent/)仅替换本机绝对路径并统一文件编码，原有回答、Oracle 判定和异常说明保留；[来源校验和](原始数据/20261011-qwen-agent/manifest.json)记录每份文件的来源与归档散列。原始目录未修改。

[混合模式对照产物](原始数据/20261011-hybrid-compare/)同样只做本机路径脱敏（`<PROJECT>` / `<REPO>`），12 轮事件、汇总 JSON 与自动报告原样归档，散列见该目录 [manifest.json](原始数据/20261011-hybrid-compare/manifest.json)。Oracle 为关键词级自动判定，正式结论以对照报告中的逐条语义复核为准。

[效率优化 A/B/C 产物](原始数据/20261011-hybrid-abc/)分三个运行目录归档（A/B/O 主批、C 终版、think-off 对照），本机运行目录与 worktree 名统一脱敏为 `<RUN_DIR>`/`<WT_OLD>`/`<REPO>`，散列见 [manifest.json](原始数据/20261011-hybrid-abc/manifest.json)，合并指标见 merged-summary.json。

六题目录中的 `summary.json`、`raw_events.jsonl` 和 `dataset_manifest.json` 已合入独立 A03 复测结果，因此不作为未经复测的首轮成绩。历史 `report.md` 中的主观评级、28 次工具与约 70 秒等表述不作为正式统计依据，正式报告采用事件数据复算结果。
