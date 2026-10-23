# P17.R 固定数据评测与验证

## 方法和复现

运行环境：Windows，Python 3.12.14。原始基线为未修改的
`5a86f47c0e24d42afdc67fc05eba6aa57dded186`，通过 git archive 导出到忽略目录，
不是在新实现中开一个“关闭 guard”的开关来冒充修改前版本。

固定数据：`tests/fixtures/p17r_retrieval.json`，16 条文档、14 个固定问题，加固定十轮
追问得到 24 个评测请求。包含不同人物的工资、关系陷阱、同名不同公司/ID、文件/版本、
金额/日期、无结果、不同 owner/persona/workspace 的污染候选和独立数据库备份查询。

BM25 路径使用 mode=none；混合路径使用确定性的 32 维 SHA-256 词特征向量与原有向量/RRF
代码，不调用真实 embedding 或生成 API。它验证混合链路行为，不代表真实 BGE/其他生产
embedding 的质量。每种模式单独进程、全新临时 DB，同一批文档，同一 scope、top_k=10。
索引建立不计入查询耗时；每个问题记录一次 cold latency，再重复 30 次，P50/P95 统计
720 次 warm request。四组最终测量顺序执行，见原始 JSON 的逐请求结果和 cold latency。

```powershell
git archive --format=zip --output=.local-dev/p17r-baseline.zip 5a86f47c0e24d42afdc67fc05eba6aa57dded186
python -m zipfile -e .local-dev/p17r-baseline.zip .local-dev/p17r-baseline
python scripts/evaluate_p17r.py --module-root .local-dev/p17r-baseline/brain --phase before --mode bm25 --output reports/p17r-before-bm25.json
python scripts/evaluate_p17r.py --module-root .local-dev/p17r-baseline/brain --phase before --mode hybrid --output reports/p17r-before-hybrid.json
python scripts/evaluate_p17r.py --module-root brain --phase after --mode bm25 --output reports/p17r-after-bm25.json
python scripts/evaluate_p17r.py --module-root brain --phase after --mode hybrid --output reports/p17r-after-hybrid.json
```

## 指标定义

- **Entity Accuracy**：18 个有预期实体名的请求，原始/消解查询是否准确包含预期唯一实体名。
  基线没有 Resolver，评估其原始 query 是否已携带显式实体；不能把它解释成旧系统有一个
  正确率为某值的 NER。这个指标也不是同名身份 ID 的认证率。
- **Recall@5**：19 个有 gold 文档的问题，其 gold 在前 5 条中的平均召回。
- **MRR@10**：同样 19 个问题，第一个 gold 文档排名倒数的均值。
- **Wrong-entity Answer Rate**：未测，原始数据为 null。没有运行真实生成模型，不编造实际
  回答错误率，也不把下面的证据指标冒充它。
- **Wrong-entity Evidence Rate（代理指标）**：24 个问题中，返回列表含非预期身份文档的
  请求占比；应为空的歧义/缺数据问题若返回任何资料也算错。使用固定文档身份标签判断，
  不用搜索 top-1 来决定代词含义。
- **十轮漂移率**：固定张三十轮工资追问中，混入其他实体证据的轮数 / 10。另报告
  chain_abstention_rate，避免用全空结果伪装“没有漂移”；不等同于真实模型长期对话评测。
- **P50/P95**：上述 720 次 warm request 的毫秒耗时；包含解析、范围检查、检索、过滤及
  config 读取，不含 UI、真实模型网络耗时和索引建立。

## 实测结果

四份原始 JSON 已一并提交，fixture SHA-256 均为
`b9a731823070a6bd783d2694668491dceea34e584bd3b05c08d90f0c29a776bf`。
以下为代码冻结、最终全仓测试结束后顺序运行四个进程的实际结果，无相对提升比例推算。

| 指标 | BM25 修改前 | BM25 修改后 | 混合修改前 | 混合修改后 |
| --- | ---: | ---: | ---: | ---: |
| Entity Accuracy（查询实体名锚点） | 5/18（27.78%） | 18/18（100%） | 5/18（27.78%） | 18/18（100%） |
| Recall@5 | 1.0000 | 1.0000 | 0.4211 | 1.0000 |
| MRR@10 | 0.9737 | 1.0000 | 0.3947 | 1.0000 |
| Wrong-entity Answer Rate | 未测 | 未测 | 未测 | 未测 |
| Wrong-entity Evidence Rate（代理） | 23/24（95.83%） | 0/24（0%） | 24/24（100%） | 1/24（4.17%） |
| 十轮漂移率（证据混入） | 10/10 | 0/10 | 10/10 | 0/10 |
| 十轮无结果率 | 0/10 | 0/10 | 0/10 | 0/10 |
| P50 warm request（ms） | 0.516 | 0.717 | 0.793 | 1.114 |
| P95 warm request（ms） | 0.623 | 0.922 | 0.933 | 1.416 |

BM25 原有 Recall@5 已为 1，但大量请求同时混入错误人物资料，说明“命中正确文档”和
“没有错误实体证据”是不同目标。混合修改后唯一剩余代理错误是无实体约束的独立查询
`数据库备份`：原有向量召回仍带来部分同 scope 的无关人物资料；它没有触发实体解析，
不属于十轮指代样例。该问题保留披露，不把本轮结果声称为全场景错误率 0。

解析/筛选有测得的额外耗时；小语料、warm cache 与伪向量下的毫秒数据不能外推到大库、
真实模型或冷启动网络延迟。

## 测试覆盖与兼容性

专项命令：

```powershell
python -m pytest tests/test_p17r_query_grounding.py -o addopts='' -q
python -m pytest -o addopts='' -q --tb=short --junitxml=.local-dev/p17r-release.xml
python -m compileall -q brain pet tests scripts/evaluate_p17r.py
git diff --check
```

专项包含原始/显式/歧义/主题切换/十轮追问、同名 ID/无身份元数据、薪资句段与人物关系陷阱、
文件名/版本/日期/金额边界、文件源定位、错误姓名文件、解析异常、scope 冲突、工作区切换及
授权交集、无历史独立查询、语义开关与向量异常、query embedding cache、纯 ContextVar 并发
与实际并发检索、旧 MemoryStore tuple/最近记忆回退、KB 旧向量绕过、历史回查、Agent
检索忽略模型自报 owner/persona/chan/workspace、记忆物理分区核对、不写入实体归属。

过程中发现并修复：完整陈述被误判为追问、普通旧 search 包装器签名不接受新参数、旧的
tuple 降级代码形状兼容、并发读取 InterfaceError、准确源文件名不在正文时误拒绝。
既有测试没有删除、跳过或放宽断言。

没有修改 `task_workflows.py`、workflow schema、主库 db.py、agent_runs.py、coding_harness.py
或 plugin_host.py；没有新增迁移。最终全仓包含 P18 的 53 项回归、v6→v7 数据兼容、P17
Run、原聊天、Coding Harness 与插件生命周期测试。

最终运行结果（Python 3.12.14 / Windows）：

| 验证 | 结果 |
| --- | --- |
| 新增 P17.R 专项 | **47 passed**，1.30 秒 |
| 实际并发检索压力，连续 5 次、每次 128 请求 | **5/5 passed**，640 次请求均匹配自己的实体 |
| 全仓最终回归 | **1829 passed, 21 skipped, 49 subtests passed**，174.68 秒，退出码 0 |
| compileall（brain/pet/tests/评测脚本） | 通过 |
| `git diff --check` | 通过 |
| P18/数据库 schema/Run/Harness/插件生命周期文件 diff | 无修改 |

21 项跳过沿用既有环境/客户端资源/符号链接权限/真实模型凭据等条件，并未为本轮新增跳过。
完整本机日志和 JUnit：`.local-dev/p17r-delivery.log`、`.local-dev/p17r-delivery.xml`；
专项：`.local-dev/p17r-specialist.log`、`.local-dev/p17r-specialist.xml`。可用上述命令重建。

## 结果的实际边界

固定小语料和伪向量不代表生产数据分布或真实 embedding 质量。对于没有实体约束的普通
独立查询，原有向量召回仍可能夹带主题不相关的资料；本轮不引入大型 reranker 来改变
所有独立搜索的语义。歧义/无身份资料的保守拒绝会降低部分真实场景召回。

旧历史重启后没有内存 provenance 标签、未认识的昵称/隐含对象、单个无身份同名片段仍
需显式澄清/结构化确认；并未宣称实现通用指代消解。完整限制见架构说明。
