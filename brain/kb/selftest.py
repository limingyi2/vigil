# -*- coding: utf-8 -*-
"""真嵌入自测：摄入一小段文档，问三个问题，看命中与分数对不对，然后清干净。

```bash
cd brain && python -m kb.selftest
```

和 tests/test_kb.py 的分工：那边用假向量验**逻辑**（不联网、跑得快），
这边用真嵌入验**链路**（模型、维度、阈值是不是真的通）。
第三个问题是**故意不相关的**，用来确认 min_score 门槛真的拦得住 ——
第一次接通时最容易出的问题不是"搜不到"，而是"什么都搜得到"。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
paths.ensure()
import kb
from paths import CONFIG_PATH

cfg = {}
try:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
except Exception as e:
    print("读不到配置：", e)

if not (cfg.get("api_key") or "").strip():
    print("配置里没有 api_key，跳过真实嵌入（框架本身不受影响）")
    sys.exit(0)

print("服务商：", cfg.get("provider"), "| 嵌入模型：", cfg.get("embed_model") or "BAAI/bge-m3")

DOC = """# 部署说明

## 反向代理
Nginx 里把 8788 转发到后端，WebSocket 走 upgrade 头。

## 定时任务
看门狗每 5 分钟拉一次桌宠心跳，超过 15 分钟没心跳就重启。
"""

r = kb.ingest_text(DOC, "部署说明.md")
print("摄入：", r)

for q in ("怎么配 nginx", "看门狗多久检查一次", "今天天气怎么样"):
    rows = kb.search(q, min_score=0.0, top_k=3)
    print("\n问：%s" % q)
    for x in rows[:3]:
        print("   %.3f  %s  %s" % (x["score"], x["source"], x["text"][:46].replace("\n", " ")))

print("\n统计：", {k: v for k, v in kb.stats().items()
                 if k in ("count", "docs", "dim", "model", "backend")})
kb.clear()
print("已清空测试数据：", kb.stats()["count"])
