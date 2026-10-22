# -*- coding: utf-8 -*-
"""混合检索端到端测试：生成数据 → 灌索引 → 建向量 → 跑检索。

用法：
    python test_hybrid_search.py [--count 1000] [--query "张伟 前端"]

生成 1000 条带人名/公司/职位/事件的模拟文档，灌进统一检索索引，
然后跑 BM25 / 向量 / RRF 三条通道的对比测试。
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from storage import search, vector, db

# ---------------------------------------------------------------------------
# 数据生成
# ---------------------------------------------------------------------------

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN = ["伟", "芳", "娜", "敏", "静", "强", "磊", "洋", "艳", "勇", "军", "杰",
         "娟", "涛", "明", "超", "秀英", "霞", "平", "刚", "桂英", "文", "辉", "鹏",
         "华", "飞", "鑫", "波", "宇", "浩", "凯", "健", "俊", "帆", "鹏", "晨",
         "子轩", "浩然", "雨桐", "诗涵", "梓萱", "子涵", "欣怡", "佳怡", "雨泽", "明轩"]

COMPANIES = ["字节跳动", "腾讯", "阿里巴巴", "百度", "美团", "京东", "拼多多",
             "网易", "小米", "华为", "OPPO", "vivo", "滴滴", "快手", "B站",
             "携程", "蚂蚁集团", "阿里云", "腾讯云", "百度智能云", "旷视", "商汤",
             "科大讯飞", "地平线", "蔚来", "小鹏", "理想", "比亚迪", "宁德时代"]

POSITIONS = ["前端工程师", "后端工程师", "全栈工程师", "算法工程师", "产品经理",
             "测试工程师", "运维工程师", "数据分析师", "UI设计师", "交互设计师",
             "项目经理", "技术总监", "架构师", "DBA", "安全工程师", "客户端工程师",
             "嵌入式工程师", "机器学习工程师", "推荐算法工程师", "NLP算法工程师"]

ACTIONS = [
    "负责{proj}项目的{part}开发",
    "主导了{proj}的技术方案设计",
    "参与{proj}的性能优化，QPS提升了{pct}%",
    "解决了{proj}中的{bug}问题",
    "推动{proj}从单体架构迁移到微服务",
    "搭建了{proj}的CI/CD流水线",
    "设计并实现了{proj}的缓存层",
    "重构了{proj}的核心模块，代码量减少{pct}%",
    "带领{team}人团队完成{proj}的交付",
    "在{proj}中引入了{tech}技术栈",
]
PROJECTS = ["直播系统", "电商平台", "社交App", "短视频后台", "支付系统",
            "推荐引擎", "搜索系统", "消息推送", "用户中心", "订单系统",
            "风控系统", "数据仓库", "BI平台", "客服系统", "广告投放"]
PARTS = ["前端", "后端", "数据库", "接口", "架构", "部署", "测试", "监控"]
BUGS = ["内存泄漏", "死锁", "数据不一致", "并发竞态", "慢查询", "连接池耗尽"]
TECHS = ["React", "Vue", "Go", "Rust", "Kafka", "Redis", "Elasticsearch",
         "Kubernetes", "gRPC", "GraphQL", "Flutter", "WebAssembly"]
CITIES = ["北京", "上海", "深圳", "杭州", "广州", "成都", "南京", "武汉", "西安", "苏州"]


def random_name():
    return random.choice(SURNAMES) + random.choice(GIVEN)


def random_doc(i):
    """生成一条模拟文档：人物简介 + 工作经历 + 项目经历。"""
    name = random_name()
    company = random.choice(COMPANIES)
    position = random.choice(POSITIONS)
    city = random.choice(CITIES)
    age = random.randint(22, 45)
    exp = random.randint(1, 20)
    salary = random.randint(15, 120)

    # 2-3 段项目经历
    projects = []
    for _ in range(random.randint(2, 4)):
        proj = random.choice(PROJECTS)
        action = random.choice(ACTIONS).format(
            proj=proj, part=random.choice(PARTS),
            pct=random.randint(10, 80), bug=random.choice(BUGS),
            team=random.randint(3, 20), tech=random.choice(TECHS))
        projects.append(action)

    title = f"{name} - {company} {position}"
    body = (
        f"{name}，{age}岁，{exp}年工作经验，现居{city}。"
        f"目前在{company}担任{position}，年薪约{salary}万。"
        + "".join(f"\n- {p}" for p in projects)
    )

    doc_id = "test_person_%05d" % i
    content_hash = hashlib.md5(body.encode("utf-8")).hexdigest()
    return {
        "doc_id": doc_id,
        "kind": "kb",
        "title": title,
        "body": body,
        "source": "test_dataset",
        "locator": f"test/{i}",
        "owner": "test",
        "src": "pc",
        "persona": "pc_assistant",
        "chan": "pc",
        "content_hash": content_hash,
        "size": len(body),
        "meta": {"name": name, "company": company, "position": position,
                 "city": city, "age": age, "exp": exp},
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500, help="生成文档数量")
    parser.add_argument("--query", type=str, default="", help="测试查询，不指定则用预设")
    parser.add_argument("--skip-vector", action="store_true", help="跳过向量索引（只测BM25）")
    args = parser.parse_args()

    print("=" * 60)
    print("混合检索端到端测试")
    print("=" * 60)

    # 1. 生成数据
    print(f"\n[1/4] 生成 {args.count} 条模拟文档...")
    random.seed(42)  # 固定种子，结果可复现
    docs = [random_doc(i) for i in range(args.count)]
    print(f"  生成完成，示例：")
    print(f"  {docs[0]['title']}")
    print(f"  {docs[0]['body'][:100]}...")

    # 2. 灌进 search_docs + FTS
    print(f"\n[2/4] 灌入检索索引（search_docs + FTS5）...")
    t0 = time.time()
    indexed = 0
    for d in docs:
        ok = search.upsert_doc(
            doc_id=d["doc_id"], kind=d["kind"], body=d["body"],
            title=d["title"], source=d["source"], locator=d["locator"],
            owner=d["owner"], src=d["src"], persona=d["persona"],
            chan=d["chan"], content_hash=d["content_hash"],
            size=d["size"], meta=d["meta"])
        if ok:
            indexed += 1
    dt = time.time() - t0
    print(f"  写入 {indexed}/{len(docs)} 条，耗时 {dt:.1f}s")

    # 验证 FTS
    conn = db.connection("main")
    fts_cnt = conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
    docs_cnt = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]
    print(f"  search_docs: {docs_cnt} 条")
    print(f"  search_fts:  {fts_cnt} 条")

    # 3. 建向量索引
    if not args.skip_vector:
        print(f"\n[3/4] 构建向量索引（bge-m3, 1024维）...")
        t0 = time.time()
        doc_ids = [d["doc_id"] for d in docs]
        # 分批，避免一次请求太大
        batch_size = 32
        total_indexed = 0
        for i in range(0, len(doc_ids), batch_size):
            batch = doc_ids[i:i + batch_size]
            result = search.index_embeddings(batch)
            if result.get("ok"):
                total_indexed += result.get("indexed", 0)
            if (i // batch_size) % 10 == 0:
                print(f"  进度: {i}/{len(doc_ids)} (last result: {result})")
        dt = time.time() - t0
        emb_cnt = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        print(f"  向量索引完成: {emb_cnt} 条，耗时 {dt:.1f}s")
    else:
        print("\n[3/4] 跳过向量索引（--skip-vector）")

    # 4. 跑检索测试
    print(f"\n[4/4] 混合检索测试...")
    queries = [
        args.query,
        "张伟 前端工程师",
        "字节跳动 算法",
        "支付系统 高并发",
        "React 前端架构",
    ]
    queries = [q for q in queries if q]  # 去掉空的

    scope = {"owner": "test", "src": "pc", "persona": "pc_assistant", "chan": "pc"}

    for q in queries:
        print(f"\n--- 查询: {q} ---")
        t0 = time.time()
        results, info = search.search(q, scope=scope, top_k=5,
                                       kinds=["kb"], budget_chars=2000)
        dt = time.time() - t0
        print(f"  方法: {info.get('method')} | 命中: {info.get('hits')} | "
              f"BM25候选: {info.get('lexical')} | 向量候选: {info.get('semantic')} | "
              f"耗时: {dt*1000:.0f}ms")
        if info.get("vector_reason"):
            print(f"  向量不可用原因: {info['vector_reason']}")
        for i, r in enumerate(results):
            origin = "+".join(r.origin) if isinstance(r.origin, list) else r.origin
            print(f"  [{i+1}] score={r.score:.4f} src={origin} | {r.title}")
            print(f"      {r.body[:80]}...")

    # 清理测试数据
    print(f"\n[清理] 删除测试数据...")
    deleted = search.delete_by_scope("kb", owner="test", src="pc", persona="pc_assistant")
    print(f"  删除 {deleted} 条测试文档")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
