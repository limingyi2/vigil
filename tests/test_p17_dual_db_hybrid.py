# -*- coding: utf-8 -*-
"""P17 双库与统一混合检索的测试。

覆盖任务要求的八项：
  1. 数据库迁移        2. 增删改查        3. 中文 BM25      4. 语义检索
  5. RRF               6. 来源隔离        7. 索引一致性      8. 故障降级

测试全部跑在**临时目录**里（VIGIL_HOME 隔离），不会碰到真实数据。
"""
import importlib
import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))


@pytest.fixture()
def home(monkeypatch):
    """隔离的 VIGIL_HOME。"""
    tmp = tempfile.mkdtemp(prefix="vigil-p17-test-")
    monkeypatch.setenv("VIGIL_HOME", tmp)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def store(home, monkeypatch):
    """干净的 storage 模块（重置连接与迁移状态）。

    🔴 **不要删 `sys.modules["paths"]`**。删掉再 import 会造出第二个模块实例：
    别处（`conversations` / `workspaces` / 测试）`import paths` 拿到的还是老对象，
    而 `storage.db` 拿到的是新对象 —— 两边 `DATA_DIR` 不是同一个变量，
    patch 完一个、另一个还指着真实用户目录。表现就是"单跑通过、全量失败"。
    正确做法：保留 `paths` 这一个实例，直接 patch 它的目录属性。
    """
    # 先把上一轮留下的连接关掉（旧模块对象里的句柄不算在本轮 close_all 里）
    previous = sys.modules.get("storage.db")
    if previous is not None:
        try:
            previous.close_all()
        except Exception:
            pass
    for name in list(sys.modules):
        if name.startswith("storage"):
            del sys.modules[name]

    import paths
    data_dir = os.path.join(home, "data")
    monkeypatch.setattr(paths, "ROOT", home, raising=False)
    monkeypatch.setattr(paths, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(paths, "CONFIG_DIR", os.path.join(home, "config"), raising=False)
    monkeypatch.setattr(paths, "CONFIG_PATH",
                        os.path.join(home, "config", "config.json"), raising=False)
    monkeypatch.setattr(paths, "MEMORY_DIR", os.path.join(data_dir, "memory"), raising=False)
    monkeypatch.setattr(paths, "KB_DIR", os.path.join(data_dir, "kb"), raising=False)
    monkeypatch.setattr(paths, "CHAT_LOG",
                        os.path.join(data_dir, "chat_history.jsonl"), raising=False)

    import storage.db as db
    db.set_dir(os.path.join(data_dir, "db"))
    yield db
    db.close_all()
    db.set_dir(None)


# --------------------------------------------------------------------------
# 1. 数据库迁移
# --------------------------------------------------------------------------

def test_main_db_creates_all_tables(store):
    conn = store.connection("main")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
    # `conversations` / `workspaces` / `runs` / `calls` / `call_ids` / `events` /
    # `jobs` / `turns` 是 5 个旧模块的原表（P17 收编），列序必须与旧 DDL 一致。
    for table in ("conversations", "messages", "memories", "kb_documents", "kb_chunks",
                  "workspaces", "runs", "calls", "call_ids", "events", "jobs", "turns",
                  "search_docs", "embeddings", "index_state",
                  "migration_ledger", "settings", "schema_version"):
        assert table in names, "主库缺表 %s" % table


def test_logs_db_has_diagnostic_tables(store):
    conn = store.connection("logs")
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for table in ("error_events", "tool_calls", "model_requests",
                  "agent_diagnostics", "cleanup_ledger"):
        assert table in names, "诊断库缺表 %s" % table


def test_two_databases_are_separate_files(store):
    assert store.main_db_path() != store.logs_db_path()
    # SQLite 首次写入才落盘，所以先打开两个连接
    store.connection("main")
    store.connection("logs")
    assert os.path.exists(store.main_db_path())
    assert os.path.exists(store.logs_db_path())


def test_wal_mode_enabled(store):
    for kind in ("main", "logs"):
        mode = store.connection(kind).execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", "%s 库不是 WAL 模式（实际 %s）" % (kind, mode)


def test_schema_version_recorded(store):
    assert store.current_version("main") >= 3
    assert store.current_version("logs") >= 3


def test_migration_is_idempotent(store):
    """重复连接不应重复应用迁移，也不该报错。"""
    store.close_all()
    for _ in range(3):
        conn = store.connection("main")
        conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()
    versions = store.connection("main").execute(
        "SELECT version, COUNT(*) FROM schema_version GROUP BY version").fetchall()
    for version, count in versions:
        assert count == 1, "版本 %s 被重复应用了 %s 次" % (version, count)


def test_backup_creates_consistent_snapshot(store):
    conn = store.connection("main")
    conn.execute("INSERT INTO settings (key,value,updated_at) VALUES ('k','v',0)")
    conn.commit()
    dest = store.backup("main")
    assert os.path.exists(dest)
    import sqlite3
    snap = sqlite3.connect(dest)
    try:
        row = snap.execute("SELECT value FROM settings WHERE key='k'").fetchone()
        assert row and row[0] == "v"
    finally:
        snap.close()


def test_backup_of_logs_db(store):
    dest = store.backup("logs")
    assert os.path.exists(dest)


# --------------------------------------------------------------------------
# 2. 增删改查
# --------------------------------------------------------------------------

def test_crud_search_docs(store):
    from storage import search
    search.upsert_doc("d1", "chat", "第一版内容", title="标题一")
    conn = store.connection("main")
    row = conn.execute("SELECT title,body FROM search_docs WHERE id='d1'").fetchone()
    assert row[0] == "标题一" and row[1] == "第一版内容"

    search.upsert_doc("d1", "chat", "第二版内容", title="标题二")
    row = conn.execute("SELECT title,body FROM search_docs WHERE id='d1'").fetchone()
    assert row[1] == "第二版内容", "更新没生效"

    count = conn.execute("SELECT COUNT(*) FROM search_docs WHERE id='d1'").fetchone()[0]
    assert count == 1, "重复写产生了副本"

    search.delete_doc("d1")
    assert conn.execute("SELECT COUNT(*) FROM search_docs WHERE id='d1'").fetchone()[0] == 0


def test_fts_syncs_with_updates(store):
    """改内容后旧词不该再命中，新词该命中。"""
    from storage import search
    search.upsert_doc("d1", "chat", "苹果派很好吃")
    results, _ = search.search("苹果派")
    assert any(r.doc_id == "d1" for r in results)

    search.upsert_doc("d1", "chat", "汽车轮胎很好用")
    results, _ = search.search("苹果派")
    assert not any(r.doc_id == "d1" for r in results), "旧内容仍能搜到（FTS 没同步）"
    results, _ = search.search("汽车轮胎")
    assert any(r.doc_id == "d1" for r in results)


def test_delete_removes_from_fts(store):
    from storage import search
    search.upsert_doc("d1", "chat", "独特词元紫貂")
    results, _ = search.search("紫貂")
    assert results
    search.delete_doc("d1")
    results, _ = search.search("紫貂")
    assert not results, "删除后 FTS 仍能命中"


def test_crud_agent_run_audit_survives_logs_cleanup(store):
    """任务状态与执行审计在主库，不能被日志清理波及。"""
    from storage import diag
    conn = store.connection("main")
    conn.execute("INSERT INTO runs (id,owner,request_id,fingerprint,data)"
                 " VALUES ('r1','u','q1','fp','{\"status\":\"running\"}')")
    conn.commit()
    diag.log_error("一些错误", category="test")
    diag.cleanup(force=True, retention_days=0, max_rows=0)
    row = conn.execute("SELECT data FROM runs WHERE id='r1'").fetchone()
    assert row and "running" in row[0], "日志清理把任务审计冲掉了"


# --------------------------------------------------------------------------
# 3. 中文 BM25
# --------------------------------------------------------------------------

def test_chinese_bm25_finds_two_char_query(store):
    """unicode61 会把中文当一整个 token —— 自己分词后两字词必须能搜到。"""
    from storage import search
    search.upsert_doc("d1", "chat", "我想知道北京天气怎么样")
    results, _ = search.search("北京")
    assert any(r.doc_id == "d1" for r in results), "两字中文搜不到"


def test_chinese_bm25_ranks_better_match_first(store):
    from storage import search
    search.upsert_doc("d1", "chat", "上海天气很好，适合出门")
    search.upsert_doc("d2", "chat", "北京天气很好，适合出门")
    results, _ = search.search("北京天气", top_k=5)
    assert results
    assert results[0].doc_id == "d2", "更相关的排后面了"


def test_chinese_mixed_with_ascii(store):
    from storage import search
    search.upsert_doc("d1", "chat", "用 Python 写自动化的脚本")
    results, _ = search.search("Python")
    assert any(r.doc_id == "d1" for r in results)


def test_file_path_search(store):
    """路径要能整体搜到，不能被打散。"""
    from storage import search
    search.upsert_doc("d1", "kb", "见 brain/memory_store_v2.py 的说明")
    results, _ = search.search("brain/memory_store_v2.py")
    assert any(r.doc_id == "d1" for r in results), "路径整体搜不到"


def test_path_component_search(store):
    """路径的组成部分也要能搜到。"""
    from storage import search
    search.upsert_doc("d1", "kb", "见 brain/memory_store_v2.py 的说明")
    results, _ = search.search("memory_store_v2")
    assert any(r.doc_id == "d1" for r in results)


def test_code_symbol_search(store):
    from storage import search
    search.upsert_doc("d1", "kb", "调用 MemoryStore.retrieve 方法")
    results, _ = search.search("MemoryStore.retrieve")
    assert any(r.doc_id == "d1" for r in results), "代码符号整体搜不到"


def test_version_number_search(store):
    from storage import search
    search.upsert_doc("d1", "kb", "发布版本 v1.2.3 修复了这个问题")
    results, _ = search.search("v1.2.3")
    assert any(r.doc_id == "d1" for r in results), "版本号整体搜不到"


def test_sha_search(store):
    from storage import search
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    search.upsert_doc("d1", "kb", "提交 %s 引入了这个改动" % sha)
    results, _ = search.search(sha)
    assert any(r.doc_id == "d1" for r in results), "SHA 搜不到"


def test_sha_with_prefix_search(store):
    from storage import search
    search.upsert_doc("d1", "kb", "sha256:abcdef0123 是摘要")
    results, _ = search.search("sha256:abcdef0123")
    assert any(r.doc_id == "d1" for r in results)


# --------------------------------------------------------------------------
# 4. 语义检索 + 8. 故障降级
# --------------------------------------------------------------------------

class FakeBackend(object):
    """假向量后端：按关键词给固定向量。用来测向量通道而不调真模型。"""

    def __init__(self, dim=4, fail=False, tag="fake:4"):
        self.dim = dim
        self.fail = fail
        self._tag = tag
        self.calls = 0

    def available(self):
        return (not self.fail), ("" if not self.fail else "embedding_unavailable")

    def tag(self):
        return self._tag

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise ValueError("embedding_unavailable")
        out = []
        for text in texts:
            low = str(text).lower()
            vec = [0.0] * self.dim
            if "苹果" in low or "apple" in low:
                vec[0] = 1.0
            elif "汽车" in low or "car" in low:
                vec[1] = 1.0
            else:
                vec[2] = 1.0
            out.append(vec)
        return out, self._tag


def test_vector_channel_contributes(store):
    from storage import search
    search.upsert_doc("d1", "kb", "完全无关的词句")
    backend = FakeBackend()
    # 先手动塞向量
    conn = store.connection("main")
    from storage import vector
    conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                 ("d1", backend.tag(), 4, vector.pack([1.0, 0.0, 0.0, 0.0]), 0))
    conn.commit()
    results, info = search.search("苹果", backend=backend)
    assert info["vector_available"] is True
    assert any(r.doc_id == "d1" for r in results)


def test_embedding_failure_degrades_to_bm25(store):
    """Embedding 挂了必须自动降级 BM25，不能影响检索与聊天。"""
    from storage import search
    search.upsert_doc("d1", "kb", "苹果派很好吃")
    backend = FakeBackend(fail=True)
    results, info = search.search("苹果派", backend=backend)
    assert results, "Embedding 挂了检索就完全不可用"
    assert info["method"] == "bm25"
    assert any(r.doc_id == "d1" for r in results)


def test_no_backend_still_returns_bm25(store):
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "kb", "苹果派很好吃")
    results, info = search.search("苹果派", backend=vector.EmbeddingBackend(mode="none"))
    assert results and info["method"] == "bm25"


def test_unified_retrieve_never_raises(store):
    """检索层任何内部错误都不能抛给聊天链路。"""
    from storage import unified
    results, info = unified.retrieve("随便什么", backend=FakeBackend(fail=True))
    assert isinstance(results, list)
    assert isinstance(info, dict)


def test_dimension_mismatch_is_not_mixed(store):
    """维度对不上的向量不参与计算（不能混用不兼容向量）。"""
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "kb", "文档")
    conn = store.connection("main")
    conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                 ("d1", "other:8", 8, vector.pack([1.0] * 8), 0))
    conn.commit()
    backend = FakeBackend(dim=4)
    hits = search.vector_search("苹果", backend=backend)
    assert hits == [], "不同维度的向量参与了计算"


def test_model_mismatch_is_not_mixed(store):
    """模型标识对不上的向量不参与计算。"""
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "kb", "文档")
    conn = store.connection("main")
    conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                 ("d1", "different-model:4", 4, vector.pack([1.0, 0.0, 0.0, 0.0]), 0))
    conn.commit()
    backend = FakeBackend(dim=4)
    hits = search.vector_search("苹果", backend=backend)
    assert hits == [], "不同模型的向量参与了计算"


def test_remote_embedding_requires_explicit_config(store):
    """远程 Embedding 必须显式配置，否则不发请求。"""
    from storage import vector
    backend = vector.EmbeddingBackend(mode="api", model="m", base="https://api.example.com",
                                      key="k", remote_enabled=False)
    ok, reason = backend.available()
    assert ok is False
    assert "remote" in reason or "not_enabled" in reason


def test_local_embedding_is_default(store):
    from storage import vector
    backend = vector.backend_from_config({})
    assert backend.mode == "local"
    ok, _ = backend.available()
    assert ok is True


# --------------------------------------------------------------------------
# 5. RRF
# --------------------------------------------------------------------------

def test_rrf_fuses_two_rankings(store):
    from storage import search
    fused = search.rrf([["a", "b", "c"], ["b", "a", "d"]])
    order = [d for d, _ in fused]
    assert set(order[:2]) == {"a", "b"}
    assert order[-1] == "d", "两边都没命中的排最后"


def test_rrf_rewards_agreement(store):
    from storage import search
    fused = dict(search.rrf([["x", "y"], ["x", "z"]]))
    assert fused["x"] > fused["y"]
    assert fused["x"] > fused["z"]


def test_rrf_deduplicates(store):
    from storage import search
    fused = search.rrf([["a", "b"], ["a", "b"]])
    ids = [d for d, _ in fused]
    assert len(ids) == len(set(ids)), "RRF 没去重"


def test_rrf_only_one_source_preserves_order(store):
    from storage import search
    fused = [d for d, _ in search.rrf([["a", "b", "c"]])]
    assert fused == ["a", "b", "c"]


def test_hybrid_method_reported(store):
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "kb", "苹果派很好吃")
    conn = store.connection("main")
    conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                 ("d1", "fake:4", 4, vector.pack([1.0, 0.0, 0.0, 0.0]), 0))
    conn.commit()
    results, info = search.search("苹果派", backend=FakeBackend(dim=4))
    assert info["method"] == "bm25+vector+rrf"
    assert results


# --------------------------------------------------------------------------
# 6. 来源隔离
# --------------------------------------------------------------------------

def test_scope_isolation_by_src(store):
    from storage import search
    search.upsert_doc("d1", "chat", "微信里的秘密", owner="u", src="wechat", persona="p")
    search.upsert_doc("d2", "chat", "APP 里的秘密", owner="u", src="pc", persona="p")
    results, _ = search.search("秘密", scope={"owner": "u", "src": "wechat", "persona": "p"})
    ids = [r.doc_id for r in results]
    assert "d1" in ids
    assert "d2" not in ids, "跨渠道泄漏了"


def test_scope_isolation_by_persona(store):
    from storage import search
    search.upsert_doc("d1", "chat", "人设A 的记忆", owner="u", src="wechat", persona="A")
    search.upsert_doc("d2", "chat", "人设B 的记忆", owner="u", src="wechat", persona="B")
    results, _ = search.search("记忆", scope={"owner": "u", "src": "wechat", "persona": "A"})
    ids = [r.doc_id for r in results]
    assert "d1" in ids and "d2" not in ids, "跨人设泄漏了"


def test_scope_isolation_by_owner(store):
    from storage import search
    search.upsert_doc("d1", "chat", "用户一的记忆", owner="u1", src="wechat", persona="p")
    search.upsert_doc("d2", "chat", "用户二的记忆", owner="u2", src="wechat", persona="p")
    results, _ = search.search("记忆", scope={"owner": "u1", "src": "wechat", "persona": "p"})
    ids = [r.doc_id for r in results]
    assert "d1" in ids and "d2" not in ids, "跨身份泄漏了"


def test_scope_isolation_by_chan(store):
    from storage import search
    search.upsert_doc("d1", "memory", "主渠道的记忆", owner="u", src="wechat",
                       persona="p", chan="wechat")
    search.upsert_doc("d2", "memory", "别的渠道的记忆", owner="u", src="wechat",
                       persona="p", chan="pc")
    results, _ = search.search("记忆", scope={"owner": "u", "src": "wechat",
                                              "persona": "p", "chan": "wechat"})
    ids = [r.doc_id for r in results]
    assert "d1" in ids and "d2" not in ids, "跨渠道分区泄漏了"


def test_workspace_authorization_boundary(store):
    """没授权的工作区文件一条都不能返回。"""
    from storage import search
    search.upsert_doc("d1", "workspace", "授权工作区的内容", owner="u", src="wechat",
                       persona="p", workspace_id="ws-ok")
    search.upsert_doc("d2", "workspace", "未授权工作区的内容", owner="u", src="wechat",
                       persona="p", workspace_id="ws-no")
    results, _ = search.search("内容", scope={"owner": "u", "src": "wechat",
                                              "persona": "p", "workspace_ids": ["ws-ok"]})
    ids = [r.doc_id for r in results]
    assert "d1" in ids
    assert "d2" not in ids, "未授权的工作区内容泄漏了"


@pytest.mark.parametrize("vector_enabled", [False, True], ids=["bm25-only", "bm25-vector"])
def test_empty_workspace_list_excludes_only_workspace_docs(store, vector_enabled):
    """无工作区授权时仍可检索当前身份的聊天、记忆和知识库。"""
    from storage import search
    from storage import vector
    scope = {"owner": "u", "src": "pc", "persona": "p", "workspace_ids": []}
    docs = [
        ("chat-current", "chat", "空授权隔离探针 当前会话聊天", "u", "pc", "p", ""),
        ("memory-current", "memory", "空授权隔离探针 长期记忆", "u", "pc", "p", ""),
        ("kb-current", "kb", "空授权隔离探针 知识库", "u", "pc", "p", ""),
        ("workspace-ok", "workspace", "空授权隔离探针 已授权工作区", "u", "pc", "p", "ws-ok"),
        ("workspace-other", "workspace", "空授权隔离探针 其他工作区", "u", "pc", "p", "ws-other"),
        ("chat-other-persona", "chat", "空授权隔离探针 其他人设", "u", "pc", "other", ""),
        ("chat-other-user", "chat", "空授权隔离探针 其他用户", "other-user", "pc", "p", ""),
        ("chat-other-session", "chat", "空授权隔离探针 其他会话", "u", "pc", "p", ""),
    ]
    for doc_id, kind, body, owner, src, persona, workspace_id in docs:
        search.upsert_doc(doc_id, kind, body, owner=owner, src=src,
                          persona=persona, workspace_id=workspace_id)
    if vector_enabled:
        conn = store.connection("main")
        for doc_id, *_ in docs:
            conn.execute(
                "INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                (doc_id, "fake:4", 4, vector.pack([0.0, 0.0, 1.0, 0.0]), 0))
        conn.commit()
        backend = FakeBackend()
    else:
        backend = vector.EmbeddingBackend(mode="none")
    results, info = search.search("空授权隔离探针", scope=scope, backend=backend, top_k=20)
    ids = {item.doc_id for item in results}
    assert {"chat-current", "memory-current", "kb-current"} <= ids
    assert not {"workspace-ok", "workspace-other", "chat-other-persona", "chat-other-user"} & ids
    if vector_enabled:
        assert info["semantic"] > 0 and info["method"] == "bm25+vector+rrf"
    else:
        assert info["method"] == "bm25"


def test_scope_isolation_applies_to_vector_channel(store):
    """向量通道同样要隔离 —— 不能只过滤 BM25。"""
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "chat", "甲的私有内容", owner="u1", src="wechat", persona="p")
    search.upsert_doc("d2", "chat", "乙的私有内容", owner="u2", src="wechat", persona="p")
    conn = store.connection("main")
    for did in ("d1", "d2"):
        conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                     (did, "fake:4", 4, vector.pack([1.0, 0.0, 0.0, 0.0]), 0))
    conn.commit()
    hits = search.vector_search("苹果", scope={"owner": "u1"}, backend=FakeBackend(dim=4))
    assert [h[0] for h in hits] == ["d1"], "向量通道泄漏了别的身份的内容"


# --------------------------------------------------------------------------
# 7. 索引一致性
# --------------------------------------------------------------------------

def test_incremental_index_skips_unchanged_files(home, store):
    from storage import indexer
    root = os.path.join(home, "ws")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "a.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("第一次的内容")
    updated, doc_id = indexer.index_file(path, workspace_id="ws1")
    assert updated is True
    updated2, doc_id2 = indexer.index_file(path, workspace_id="ws1")
    assert updated2 is False, "没变的文件又被索引了一次（不是增量）"
    assert doc_id == doc_id2


def test_incremental_index_detects_modification(home, store):
    from storage import indexer
    root = os.path.join(home, "ws")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "a.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("原始内容")
    indexer.index_file(path, workspace_id="ws1")
    time_gap = 1.1
    import time
    time.sleep(time_gap)
    with open(path, "w", encoding="utf-8") as f:
        f.write("改过的内容")
    updated, _ = indexer.index_file(path, workspace_id="ws1")
    assert updated is True, "改过的文件没被重新索引"


def test_deleted_file_removed_from_index(home, store):
    """文件删了，索引里不能还留着。"""
    from storage import indexer
    root = os.path.join(home, "ws")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "a.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("待删除的内容")
    indexer.index_file(path, workspace_id="ws1")
    conn = store.connection("main")
    assert conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0] == 1
    os.remove(path)
    result = indexer.sync_workspace("ws1", root, allowed_roots=[root])
    assert result["removed"] == 1, "删掉的文件还在索引里"
    assert conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0] == 0


def test_sync_workspace_only_touches_allowed(home, store):
    """授权边界外的目录不能被索引。"""
    from storage import indexer
    root = os.path.join(home, "root")
    allowed = os.path.join(root, "allowed")
    denied = os.path.join(root, "denied")
    os.makedirs(allowed, exist_ok=True)
    os.makedirs(denied, exist_ok=True)
    with open(os.path.join(allowed, "ok.txt"), "w", encoding="utf-8") as f:
        f.write("授权内容")
    with open(os.path.join(denied, "no.txt"), "w", encoding="utf-8") as f:
        f.write("未授权内容")
    # 从根扫描，但白名单只授权了 allowed 子目录
    indexer.sync_workspace("ws1", root, allowed_roots=[allowed])
    conn = store.connection("main")
    bodies = [r[0] for r in conn.execute("SELECT body FROM search_docs").fetchall()]
    assert any("授权内容" in b for b in bodies), "授权目录的内容没被索引"
    assert not any("未授权内容" in b for b in bodies), "索引了未授权目录"


def test_sync_workspace_rejects_path_outside_allowlist(home, store):
    """直接调用时也要再校验一次路径（双保险）。"""
    from storage import indexer
    denied = os.path.join(home, "denied")
    os.makedirs(denied, exist_ok=True)
    with open(os.path.join(denied, "no.txt"), "w", encoding="utf-8") as f:
        f.write("未授权内容")
    allowed = os.path.join(home, "allowed")
    os.makedirs(allowed, exist_ok=True)
    indexer.sync_workspace("ws1", denied, allowed_roots=[allowed])
    conn = store.connection("main")
    bodies = [r[0] for r in conn.execute("SELECT body FROM search_docs").fetchall()]
    assert not any("未授权内容" in b for b in bodies), "越权目录被索引了"


def test_chat_index_is_incremental(home, store):
    """聊天存档追加后只索引新增部分。"""
    from storage import indexer
    chat = os.path.join(home, "chat.jsonl")
    with open(chat, "w", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "text": "第一条消息"}) + "\n")
    first = indexer.sync_chat(chat, src="wechat", persona="p")
    assert first["indexed"] == 1
    with open(chat, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "assistant", "text": "第二条消息"}) + "\n")
    second = indexer.sync_chat(chat, src="wechat", persona="p")
    assert second["indexed"] == 1, "第二次把老消息也重排了"
    conn = store.connection("main")
    assert conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0] == 2


def test_search_doc_hash_tracks_content(store):
    from storage import search
    search.upsert_doc("d1", "kb", "内容一", content_hash="h1")
    conn = store.connection("main")
    row = conn.execute("SELECT content_hash FROM search_docs WHERE id='d1'").fetchone()
    assert row[0] == "h1"
    search.upsert_doc("d1", "kb", "内容二", content_hash="h2")
    row = conn.execute("SELECT content_hash FROM search_docs WHERE id='d1'").fetchone()
    assert row[0] == "h2"


def test_embedding_index_and_search_stay_consistent(store):
    from storage import search
    from storage import vector
    search.upsert_doc("d1", "kb", "苹果派")
    conn = store.connection("main")
    conn.execute("INSERT INTO embeddings (doc_id,model,dim,vector,created_at) VALUES (?,?,?,?,?)",
                 ("d1", "fake:4", 4, vector.pack([1.0, 0, 0, 0]), 0))
    conn.commit()
    hits = search.vector_search("苹果", backend=FakeBackend(dim=4))
    assert hits
    # 删除文档时向量也要一起删
    search.delete_doc("d1")
    assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE doc_id='d1'").fetchone()[0] == 0


# --------------------------------------------------------------------------
# 诊断库：脱敏、清理、不阻塞业务
# --------------------------------------------------------------------------

def test_diag_redacts_api_keys(store):
    from storage import diag
    text = diag.redact("我的 key 是 sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "sk-abcdefghijklmnop" not in text
    assert "sk-***" in text


def test_diag_redacts_bearer_tokens(store):
    from storage import diag
    assert "eyJhbGciOiJIUzI1NiJ9abcdef" not in diag.redact(
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdef")


def test_diag_keeps_sha_digest(store):
    """SHA 是要留的 —— 脱敏不能把它也抹掉。"""
    from storage import diag
    sha = "a" * 40
    text = diag.redact("commit sha %s 已推送" % sha)
    assert sha in text, "SHA 被误脱敏了"


def test_diag_redacts_phone(store):
    from storage import diag
    assert "13800138000" not in diag.redact("电话 13800138000")


def test_diag_write_does_not_raise_when_db_broken(store, monkeypatch):
    """诊断库故障不能抛异常 —— 聊天链路不能被它拖下水。"""
    from storage import diag
    store.close_all()
    monkeypatch.setattr(store, "connection",
                        lambda kind="main": (_ for _ in ()).throw(
                            sqlite3_error()))
    diag.log_error("坏了")
    diag.log_tool_call("t", state="ok")
    diag.log_model_request("p", "m", duration_ms=1)
    diag.log_agent_event("r1", "started")


def sqlite3_error():
    import sqlite3
    return sqlite3.OperationalError("database is locked")


def test_diag_cleanup_removes_old_rows(store):
    from storage import diag
    conn = store.connection("logs")
    conn.execute("INSERT INTO error_events (created_at,level,category,message,origin,traceback,scope,fingerprint)"
                 " VALUES (0,'error','x','旧错误','','','{}','fp')")
    conn.commit()
    diag.cleanup(force=True, retention_days=1, max_rows=1000)
    count = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    assert count == 0, "过期日志没被清掉"


def test_diag_respects_max_rows(store):
    from storage import diag
    conn = store.connection("logs")
    import time
    for i in range(20):
        conn.execute("INSERT INTO error_events (created_at,level,category,message,origin,traceback,scope,fingerprint)"
                     " VALUES (?,'error','x',? ,'','','{}','fp')", (time.time() + i, "e%d" % i))
    conn.commit()
    diag.cleanup(force=True, retention_days=3650, max_rows=5)
    count = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    assert count <= 5, "行数上限没生效"


def test_diag_error_does_not_block_main_db(store):
    """诊断库故障时主库照常读写。"""
    from storage import diag
    store.close_all()
    import sqlite3
    orig = store.connection

    def broken(kind="main"):
        if kind == "logs":
            raise sqlite3.OperationalError("logs broken")
        return orig(kind)
    store.connection = broken
    diag.log_error("诊断库坏了")
    conn = store.connection("main")
    conn.execute("INSERT INTO settings (key,value,updated_at) VALUES ('k','v',0)")
    conn.commit()
    assert conn.execute("SELECT value FROM settings WHERE key='k'").fetchone()[0] == "v"
    store.connection = orig


# --------------------------------------------------------------------------
# Token 预算
# --------------------------------------------------------------------------

def test_budget_limits_result_length(store):
    from storage import search
    search.upsert_doc("d1", "kb", "很长的内容" * 2000)
    results, info = search.search("很长的内容", budget_chars=500, top_k=5)
    assert results
    assert len(results[0].body) <= 500
    assert info["truncated"] is True


def test_budget_respected_across_results(store):
    from storage import search
    for i in range(5):
        search.upsert_doc("d%d" % i, "kb", "关键词内容 %d" % i + "填充" * 300)
    results, info = search.search("关键词内容", budget_chars=800, top_k=5)
    total = sum(len(r.body) for r in results)
    assert total <= 800, "总长度超预算：%d" % total


def test_snippet_is_capped(store):
    from storage import search
    search.upsert_doc("d1", "kb", "长" * 5000)
    results, _ = search.search("长")
    assert len(results[0].body) <= search.MAX_SNIPPET


# --------------------------------------------------------------------------
# 迁移（legacy → 新库）
# --------------------------------------------------------------------------

def test_legacy_chat_migration(home, store):
    from storage import legacy_import
    import paths
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    chat = os.path.join(data_dir, "chat_history.jsonl")
    with open(chat, "w", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "text": "老数据里的消息", "t": "2026-01-01 10:00:00"}) + "\n")
    result = legacy_import.run(kinds=["chat"])
    assert result["migrated"] >= 1
    conn = store.connection("main")
    count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 1, "老聊天记录没迁过来"
    # 原文件必须还在
    assert os.path.exists(chat), "迁移把原文件删了"


def test_legacy_migration_is_idempotent(home, store):
    from storage import legacy_import
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    chat = os.path.join(data_dir, "chat_history.jsonl")
    with open(chat, "w", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "text": "消息"}) + "\n")
    legacy_import.run(kinds=["chat"])
    legacy_import.run(kinds=["chat"])
    conn = store.connection("main")
    count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 1, "重复迁移产生了副本"


def test_legacy_memory_migration(home, store):
    from storage import legacy_import
    mem_dir = os.path.join(home, "data", "memory")
    os.makedirs(mem_dir, exist_ok=True)
    with open(os.path.join(mem_dir, "memory.json"), "w", encoding="utf-8") as f:
        json.dump({"items": [{"id": "m1", "text": "记得他喜欢猫", "type": "event"}]}, f)
    legacy_import.run(kinds=["memory"])
    conn = store.connection("main")
    row = conn.execute("SELECT text FROM memories WHERE id='m1'").fetchone()
    assert row and "猫" in row[0]


def test_legacy_agent_runs_consolidated_into_main_db(home, store):
    """Agent 任务状态与执行审计必须进主库，且旧文件一个字节不动。"""
    from storage import legacy_sqlite
    import sqlite3
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    old = os.path.join(data_dir, "agent_runs.sqlite3")
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, owner TEXT, request_id TEXT,"
                 " fingerprint TEXT, data TEXT, UNIQUE(owner,request_id))")
    conn.execute("CREATE TABLE calls (run_id TEXT, signature TEXT, state TEXT, result TEXT,"
                 " step_id INTEGER, PRIMARY KEY(run_id,signature))")
    conn.execute("CREATE TABLE call_ids (run_id TEXT, call_id TEXT, signature TEXT,"
                 " PRIMARY KEY(run_id,call_id))")
    conn.execute("CREATE TABLE events (run_id TEXT, seq INTEGER, data TEXT, PRIMARY KEY(run_id,seq))")
    conn.execute("INSERT INTO runs VALUES ('r1','u','q1','fp','{\"status\":\"running\"}')")
    conn.execute("INSERT INTO calls VALUES ('r1','sig','dispatched','ok',1)")
    conn.execute("INSERT INTO call_ids VALUES ('r1','call_1','sig')")
    conn.execute("INSERT INTO events VALUES ('r1',0,'{}')")
    conn.commit()
    conn.close()
    before = open(old, "rb").read()

    # 第一次打开主库就会跑 v6，把旧库的行搬过来 —— 不需要人工触发。
    main = store.connection("main")
    stats = legacy_sqlite.import_legacy_files(main, data_dir=data_dir, force=True)
    # 再搬一次是空操作：内容已经在了，幂等靠主键 + OR IGNORE。
    assert stats["rows"] == 0, stats

    assert main.execute("SELECT data FROM runs WHERE id='r1'").fetchone()[0].find("running") > 0
    assert main.execute("SELECT COUNT(*) FROM calls WHERE run_id='r1'").fetchone()[0] == 1
    assert main.execute("SELECT COUNT(*) FROM call_ids WHERE run_id='r1'").fetchone()[0] == 1
    assert main.execute("SELECT COUNT(*) FROM events WHERE run_id='r1'").fetchone()[0] == 1
    assert open(old, "rb").read() == before, "迁移改动了旧库文件"


def test_legacy_sqlite_import_is_idempotent(home, store):
    """重复迁移不能产生重复行。"""
    from storage import legacy_sqlite
    import sqlite3
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    old = os.path.join(data_dir, "conversations.sqlite3")
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE conversations (owner TEXT NOT NULL, src TEXT NOT NULL,"
                 " persona TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,"
                 " created REAL NOT NULL, updated REAL NOT NULL,"
                 " legacy INTEGER NOT NULL DEFAULT 0, workspace_id TEXT,"
                 " PRIMARY KEY(owner,src,persona,id))")
    conn.execute("INSERT INTO conversations VALUES ('o','pc','p','c1','标题',1.0,2.0,0,NULL)")
    conn.commit()
    conn.close()

    main = store.connection("main")
    for _ in range(3):
        legacy_sqlite.import_legacy_files(main, data_dir=data_dir, force=True)
    assert main.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1


def test_migration_plan_lists_sources(home, store):
    from storage import legacy_import
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "chat_history.jsonl"), "w", encoding="utf-8") as f:
        f.write("")
    items = legacy_import.plan()
    assert any(i["kind"] == "chat" for i in items)


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def test_unified_retrieve_applies_scope(store):
    from storage import unified
    from storage import search
    search.upsert_doc("d1", "chat", "甲的记录", owner="u1", src="wechat", persona="p")
    search.upsert_doc("d2", "chat", "乙的记录", owner="u2", src="wechat", persona="p")
    results, info = unified.retrieve("记录", owner="u1", src="wechat", persona="p")
    assert [r.doc_id for r in results] == ["d1"]


def test_unified_retrieve_can_be_disabled(store, monkeypatch, home):
    from storage import unified
    import json as _json
    cfg_path = os.path.join(home, "config", "config.json")
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump({"retrieval": {"unified": False}}, f)
    results, info = unified.retrieve("任何东西")
    assert info["method"] == "legacy"
    assert not results


def test_status_reports_both_databases(store):
    from storage import unified
    status = unified.status()
    assert status["db"]["main"]["healthy"] is True
    assert status["db"]["logs"]["healthy"] is True


# --------------------------------------------------------------------------
# 9. 旧 SQLite 库收编（P17 第二阶段）
# --------------------------------------------------------------------------

def test_legacy_modules_write_only_into_main_db(home, store):
    """5 个旧模块不再各自建 .sqlite3，全部落在主库一个文件里。"""
    import conversations
    import workspaces
    import display_turns

    data_dir = os.path.join(home, "data")
    folder = os.path.join(home, "proj")
    os.makedirs(folder, exist_ok=True)

    ws = workspaces.create("owner", "pc", "default", folder)
    conv = conversations.create("owner", "pc", "default", ws["id"])
    display_turns.begin("owner", "req_abcdefgh", "hi")

    assert conv["id"]
    for stale in ("conversations.sqlite3", "workspaces.sqlite3",
                  "agent_runs.sqlite3", "creator_jobs.sqlite3",
                  "display_turns.sqlite3"):
        assert not os.path.exists(os.path.join(data_dir, stale)), \
            "%s 还在单独建库 —— 收编没生效" % stale
    assert os.path.exists(os.path.join(data_dir, "db", "vigil.db"))

    # 写进去的确实能读回来（同一份数据，不是写了个空文件）
    assert [r["id"] for r in workspaces.list_for("owner", "pc", "default")] == [ws["id"]]
    assert [r["id"] for r in conversations.list_for("owner", "pc", "default")] == [conv["id"]]
    assert display_turns.find_request("owner", "req_abcdefgh")["status"] == "running"


def test_display_turns_update_inside_another_write_transaction(home, store):
    """creator_jobs 在自己写事务里镜像状态时，必须借用同一个连接。

    两个模块并库后写的是同一个文件。另开连接会在写锁上互等，
    等满 busy_timeout 抛 `database is locked`，而调用方是 `except: pass` ——
    表现就是镜像无声无息地不再更新。
    """
    import creator_jobs
    import display_turns

    display_turns.begin("owner", "req_abcdefgh", "hi")
    with creator_jobs.LOCK, creator_jobs._db() as conn:
        display_turns.update("owner", "req_abcdefgh", _conn=conn, content="事务内写入")
    assert display_turns.find_request("owner", "req_abcdefgh")["content"] == "事务内写入"


def test_creator_jobs_mirror_reaches_display_turns(home, store):
    """作业状态镜像真的落到了显示记录里（不是被 except 吞掉）。"""
    import creator_jobs
    import display_turns

    display_turns.begin("owner", "req_abcdefgj", "hi")
    creator_jobs._mirror_update("owner", "req_abcdefgj", content="部分输出")
    assert display_turns.find_request("owner", "req_abcdefgj")["content"] == "部分输出"


def test_legacy_table_column_order_matches_old_modules(home, store):
    """并库后主库里的旧表列序必须与旧模块的 DDL 一字不差。

    旧模块用的是位置插入（`INSERT INTO runs VALUES (?,?,?,?,?)`），
    列序错一位就整体错位 —— 而且 SQLite 不报错，只会静默写错列。
    """
    conn = store.connection("main")
    expected = {
        "runs": ["id", "owner", "request_id", "fingerprint", "data"],
        "calls": ["run_id", "signature", "state", "result", "step_id"],
        "call_ids": ["run_id", "call_id", "signature"],
        "events": ["run_id", "seq", "data"],
        "jobs": ["id", "owner", "request_id", "fingerprint", "data"],
        "turns": ["owner", "request_id", "created", "data"],
        "conversations": ["owner", "src", "persona", "id", "title", "created",
                          "updated", "legacy", "workspace_id"],
        "workspaces": ["owner", "src", "persona", "id", "path", "key", "name",
                       "created", "updated"],
    }
    for table, columns in expected.items():
        got = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
        assert got == columns, "%s 列序变了：%r" % (table, got)


def test_unified_sync_kb_indexes_chunks(home, store):
    """知识库块要能进统一索引 —— 这条链路以前是断的。"""
    from storage import unified

    class _FakeStore(object):
        def chunks(self):
            return [{"source": "手册.md", "chunk": 0, "heading": "安装",
                     "text": "把电源插上再按开关，指示灯变绿表示就绪"}]

    class _FakeKb(object):
        @staticmethod
        def store_for(persona_key="", src="app"):
            return _FakeStore()

    stats = unified.sync_kb("", "pc", "default", "pc")
    # 真实 kb 里没有这份语料，所以先确认调通不抛；再用手工块验证索引写入
    assert isinstance(stats, dict)

    from storage import indexer
    result = indexer.sync_kb_chunks(_FakeStore().chunks(), owner="", src="pc",
                                    persona="default", chan="pc")
    assert result["indexed"] == 1
    hits, _ = unified.retrieve("指示灯变绿", src="pc", persona="default", chan="pc",
                               kinds=["kb"], top_k=3)
    assert hits, "知识库块没进索引"
    assert "指示灯" in hits[0].body


# --------------------------------------------------------------------------
# 10. 分词与融合的回归（实测踩过的两个真 bug）
# --------------------------------------------------------------------------

def test_four_char_chinese_query_matches_document(store):
    """四字中文查询必须能搜到 —— 这条曾经是坏的。

    bug：`match_query` 把查询侧也展开成「单字 + 全部相邻双字」再用 AND 串。
    「烟台天气」产出 `烟台` `台天` `天气` 三个双字，其中 `台天` 是跨边界拼
    出来的假词，任何文档里都不存在 → AND 永假 → 整条查询静默返回空。
    实测表现：两字查询能搜到，四字查询一定搜不到，而索引里明明有。
    """
    from storage import search, tokenizer
    search.upsert_doc("c1", search.SCOPE_CHAT, "烟台今天晴，22度",
                      title="assistant", owner="u", src="pc", persona="p", chan="pc")
    hits = search.bm25_search("烟台天气", kinds=["chat"], limit=5)
    assert hits, "四字中文查询搜不到已索引的文档"
    assert hits[0][0] == "c1"

    # 真正的修法是运算符：查询侧仍然会产出 `台天` 这种跨边界双字
    # （这是 bigram 的固有产物，消不掉），但它必须**不参与 AND**。
    terms = tokenizer._query_terms("烟台天气")
    assert "烟台" in terms and "天气" in terms
    assert "烟" not in terms, "查询侧不该放单字进去（歧义太大）"
    assert " OR " in tokenizer.match_query("烟台天气"), \
        "含中文时必须用 OR，否则跨边界双字会把查询变成永假"


def search_hits_prefixed_sha(store):
    """带前缀的 SHA 必须能搜到 —— 上面那个永假短语项不能把结果带走。"""
    from storage import search
    sha = "sha256:abcdef0123456789abcdef0123456789abcdef01"
    search.upsert_doc("s1", search.SCOPE_WORKSPACE, "commit " + sha,
                      title="log", owner="u", src="pc", persona="p", chan="pc")
    hits = search.bm25_search(sha, kinds=["workspace"], limit=5)
    return bool(hits) and hits[0][0] == "s1"


def test_pure_ascii_query_stays_precise(store):
    """纯 ASCII/路径/版本号查询保持 AND —— 不能被中文的 OR 规则带松。"""
    from storage import tokenizer
    # 整体词元在前，AND 串起来 —— 精确匹配，不放松
    assert tokenizer.match_query("deploy.sh") == '"deploy.sh" AND "deploy" AND "sh"'
    assert " OR " not in tokenizer.match_query("v1.2.3")
    # `:` 是 FTS5 语法字符，quote() 会把它换成空格 —— 于是整词变成短语
    # 「sha256 abcdef0123」（要求两个 token 相邻），在只存了单个
    # `sha256:abcdef0123` 词元的文档里匹配不上。**拆分项仍然 AND 在后面**，
    # 所以前缀 SHA 照样搜得到，只是多一个永假项。
    assert tokenizer.match_query("sha256:abcdef0123") == \
        '"sha256 abcdef0123" AND "sha256" AND "abcdef0123"'
    assert search_hits_prefixed_sha(store)
    # 含中文时才切 OR
    assert " OR " in tokenizer.match_query("烟台 天气")


def test_origin_is_a_list_not_a_string(store):
    """`SearchResult.origin` 必须是列表。

    bug：一边给 `"+".join(...)` 的字符串，一边按集合 `"＋".join(origin)`，
    字符串被逐字拆开 → 界面上出现 `【聊天记录｜user｜2＋4＋b＋m】`。
    """
    from storage import search, unified
    search.upsert_doc("c1", search.SCOPE_CHAT, "烟台天气",
                      title="user", owner="u", src="pc", persona="p", chan="pc")
    rows, _ = unified.retrieve("烟台天气", owner="u", src="pc", persona="p",
                               chan="pc", top_k=3)
    assert rows
    assert isinstance(rows[0].origin, list), type(rows[0].origin)
    text = unified.format_results(rows)
    assert "bm25" in text and "＋b＋" not in text


# --------------------------------------------------------------------------
# 11. 合并前收尾修掉的三个问题（2026-10-22）
# --------------------------------------------------------------------------

def _make_agent_runs_db(path):
    """造一个旧 agent_runs.sqlite3，runs / calls 各一行。"""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, owner TEXT, request_id TEXT,"
                 " fingerprint TEXT, data TEXT, UNIQUE(owner,request_id))")
    conn.execute("CREATE TABLE calls (run_id TEXT, signature TEXT, state TEXT, result TEXT,"
                 " step_id INTEGER, PRIMARY KEY(run_id,signature))")
    conn.execute("INSERT INTO runs VALUES ('r1','u','q1','fp','{}')")
    conn.execute("INSERT INTO calls VALUES ('r1','sig','dispatched','ok',1)")
    conn.commit()
    conn.close()


def _ledger(main, mig_id, column="status"):
    return main.execute("SELECT %s FROM migration_ledger WHERE id=?"
                        % column, (mig_id,)).fetchone()


def test_table_failure_is_not_recorded_as_file_ok(home, store):
    """一张表搬失败，绝不能被记成"整个文件 ok"。

    曾经的 bug：表级错误先写进 detail，紧接着被 `dict(moved, skipped=...)`
    整体覆盖，然后无条件记 `ok`。台账显示成功 → 下次直接跳过 →
    失败的表永远不会重试，记录被静默吞掉。
    """
    from storage import legacy_sqlite
    import sqlite3
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    old = os.path.join(data_dir, "agent_runs.sqlite3")

    # 🔴 先建主库连接（此刻 v6 迁移跑完，data_dir 里还没有旧文件），
    # 再放旧文件 —— 顺序反了台账里已经是 ok，这个测试就变成空跑。
    main = store.connection("main")
    _make_agent_runs_db(old)

    real_copy = legacy_sqlite._copy_table

    def flaky(src, dst, src_table, dst_table, columns):
        if dst_table == "calls":
            raise sqlite3.OperationalError("database is locked")
        return real_copy(src, dst, src_table, dst_table, columns)

    legacy_sqlite._copy_table = flaky
    try:
        stats = legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    finally:
        legacy_sqlite._copy_table = real_copy

    mig_id = "sqlite:agent_runs.sqlite3"
    assert stats["partial"] == ["agent_runs.sqlite3"], stats
    assert _ledger(main, mig_id)[0] == "partial", "部分失败不能记 ok"
    detail = json.loads(_ledger(main, mig_id, "detail")[0])
    assert "calls" in detail.get("errors", {}), detail     # 错误必须留着
    assert detail.get("runs") == 1, detail                 # 成绩不能被盖掉
    assert main.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert main.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0

    # 关键：**不 force** 再跑，partial 必须被重试，然后才能记 ok
    legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    assert main.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    assert _ledger(main, mig_id)[0] == "ok"


def test_unopenable_legacy_file_records_failed_and_retries(home, store):
    """旧文件打不开 → 记 failed 并可重试，不是静默跳过。"""
    from storage import legacy_sqlite
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    main = store.connection("main")

    # 同名目录：sqlite3.connect 在连接阶段就抛，走 failed 分支
    blocked = os.path.join(data_dir, "creator_jobs.sqlite3")
    os.makedirs(blocked, exist_ok=True)
    stats = legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    assert "creator_jobs.sqlite3" in stats["partial"], stats
    assert _ledger(main, "sqlite:creator_jobs.sqlite3")[0] == "failed"

    # 换成能打开的真库，不 force 再跑 → 必须重试成功
    os.rmdir(blocked)
    import sqlite3
    conn = sqlite3.connect(blocked)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, owner TEXT, request_id TEXT,"
                 " fingerprint TEXT, data TEXT, UNIQUE(owner,request_id))")
    conn.execute("INSERT INTO jobs VALUES ('j1','u','q1','fp','{}')")
    conn.commit()
    conn.close()
    legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    assert main.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert _ledger(main, "sqlite:creator_jobs.sqlite3")[0] == "ok"


def test_corrupt_legacy_file_failure_is_visible_and_retryable(home, store):
    """损坏的旧文件：错误要看得见，且换成正版后能补上。"""
    from storage import legacy_sqlite
    data_dir = os.path.join(home, "data")
    os.makedirs(data_dir, exist_ok=True)
    main = store.connection("main")
    old = os.path.join(data_dir, "agent_runs.sqlite3")
    with open(old, "wb") as f:
        f.write(b"this is definitely not a sqlite database")

    legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    mig_id = "sqlite:agent_runs.sqlite3"
    assert _ledger(main, mig_id)[0] != "ok", "损坏文件不能记 ok"
    detail = json.loads(_ledger(main, mig_id, "detail")[0])
    assert detail.get("errors"), "错误被吞了：%r" % detail

    os.remove(old)                      # 坏文件还在盘上，直接连会报 not a database
    _make_agent_runs_db(old)
    legacy_sqlite.import_legacy_files(main, data_dir=data_dir)
    assert main.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert main.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    assert _ledger(main, mig_id)[0] == "ok"

def test_migration_step_failure_rolls_back_whole_step(store):
    """一步失败 → 整步回滚，版本号不前进，半截 DDL 不留。"""
    conn = __import__("sqlite3").connect(":memory:")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL,"
                 " applied_at REAL NOT NULL, note TEXT)")
    saved_mig, saved_ver = store.MIGRATIONS, store.SCHEMA_VERSION

    def ok_step(c):
        """第一步：成功"""
        c.execute("CREATE TABLE t_ok (x TEXT)")

    def bad_step(c):
        """第二步：建了表再抛"""
        c.execute("CREATE TABLE t_bad (x TEXT)")
        raise RuntimeError("boom")

    store.MIGRATIONS = {("main", 1): ok_step, ("main", 2): bad_step}
    store.SCHEMA_VERSION = 2
    try:
        raised = False
        try:
            store._migrate(conn, "main")
        except RuntimeError:
            raised = True
        assert raised, "失败的一步必须往外抛，不能咽下去"
    finally:
        store.MIGRATIONS, store.SCHEMA_VERSION = saved_mig, saved_ver

    def table_count(name):
        return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?",
                            (name,)).fetchone()[0]

    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1
    assert table_count("t_ok") == 1, "前一步的成果应该在"
    assert table_count("t_bad") == 0, "失败那一步的 DDL 必须被回滚掉"


def test_no_migration_step_commits_internally(store):
    """迁移步骤内部不许自己结束事务 —— 否则上面那条回滚语义是假的。

    曾经的 bug：8 个 step 末尾都有一句 `conn.commit()`，把 `_migrate`
    开的 `BEGIN IMMEDIATE` 提前结束。之后这步再失败，`rollback()`
    已经没有活动事务可回，前半段 DDL 已经落盘 → "表建了、版本号没记"
    的半截状态，下次重跑还可能撞 `duplicate column`。
    """
    import inspect
    for (kind, version), step in store.MIGRATIONS.items():
        source = inspect.getsource(step)
        assert ".commit(" not in source, "%s v%d 在步骤内部提交了事务" % (kind, version)


def _make_link(root, name, target):
    """建符号链接；环境不允许时返回 False（调用方 skip）。"""
    link = os.path.join(root, name)
    try:
        os.symlink(target, link)
    except OSError:
        return False
    return os.path.islink(link)      # 沙箱会静默丢弃链接，必须复查


def test_walk_authorized_skips_symlink_escape(home):
    """真实符号链接：指向授权范围外的文件/junction 不许进索引。"""
    from storage import indexer
    root = os.path.join(home, "ws")
    outside = os.path.join(home, "outside")
    os.makedirs(root, exist_ok=True)
    os.makedirs(outside, exist_ok=True)
    with open(os.path.join(root, "inside.md"), "w", encoding="utf-8") as f:
        f.write("inside")
    with open(os.path.join(outside, "secret.md"), "w", encoding="utf-8") as f:
        f.write("SECRET-CANARY")

    link = os.path.join(root, "escape.md")
    if not _make_link(root, "escape.md", os.path.join(outside, "secret.md")):
        pytest.skip("当前环境不允许创建符号链接")

    # 先证明漏洞是真的：os.walk(followlinks=False) 会把**文件**符号链接
    # 原样列进 filenames（它只挡目录递归，不挡文件链接）。
    walked = [os.path.join(dp, n)
              for dp, _dn, fn in os.walk(root, followlinks=False) for n in fn]
    assert link in walked, "os.walk 行为变了，这个测试的前提不成立"

    got = [os.path.basename(p) for p in indexer.walk_authorized(root, [root])]
    assert "inside.md" in got
    assert "escape.md" not in got, "符号链接逃逸：界外文件被放进索引了"


def test_walk_authorized_checks_resolved_paths(home, monkeypatch):
    """解析后的真实路径越权必须被挡，且不能误杀"授权根自己是链接"。

    这里不依赖真符号链接（CI/沙箱常不让建），直接安排 realpath 的返回值：
    - `evil.md`  解析到界外    → 挡
    - `jdirlink` 解析到界外    → 挡（它下面的 leak.txt 也不许出现）
    - root 自己解析到别处      → 正常文件照旧放行（不能误杀）
    """
    from storage import indexer
    root = os.path.join(home, "ws")
    outside = os.path.join(home, "outside")
    elsewhere = os.path.join(home, "elsewhere")
    for d in (root, outside, elsewhere):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(root, "a.md"), "w", encoding="utf-8") as f:
        f.write("ok")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    with open(os.path.join(root, "sub", "b.md"), "w", encoding="utf-8") as f:
        f.write("ok")
    with open(os.path.join(outside, "secret.md"), "w", encoding="utf-8") as f:
        f.write("SECRET-CANARY")
    with open(os.path.join(root, "evil.md"), "w", encoding="utf-8") as f:
        f.write("x")
    os.makedirs(os.path.join(root, "jdirlink"), exist_ok=True)
    with open(os.path.join(root, "jdirlink", "leak.txt"), "w", encoding="utf-8") as f:
        f.write("x")

    real = os.path.realpath

    def fake(p):
        ap = os.path.abspath(p)
        if ap == os.path.abspath(root):
            return real(elsewhere)                       # 授权根自己是个链接
        base = os.path.basename(ap)
        if base == "evil.md":
            return real(os.path.join(outside, "secret.md"))
        if base == "jdirlink":
            return real(outside)
        return real(p)

    monkeypatch.setattr(os.path, "realpath", fake)
    got = sorted(os.path.basename(p)
                 for p in indexer.walk_authorized(root, [root]))
    assert "a.md" in got and "b.md" in got, "误杀了授权范围内的正常文件：%r" % got
    assert "evil.md" not in got, "文件符号链接逃逸没挡住"
    assert "leak.txt" not in got, "目录符号链接/junction 逃逸没挡住"


def test_agent_runs_read_and_write_share_one_db(home, store):
    """Agent Run 的读写路径必须同源。

    曾经出过一次真实的分裂：写路径换成了主库（vigil.db），读路径还硬编码
    旧的独立文件 `data/agent_runs.sqlite3` —— 于是"写进主库、读旧文件"，
    SSE 读不到刚写的增量，任务看起来一直没动。

    这里钉两件事：
      1. 运行时 `_db_file()` 返回的就是主库路径，读写都从它来；
      2. 源码里不再出现硬编码的旧库文件名（注释除外）。
    """
    import ast
    import agent_runs

    assert agent_runs._db_file(create=True) == store.main_db_path()

    # 源码级守卫：解析 AST，只看真实的字符串常量（注释不在 AST 里）。
    src_path = agent_runs.__file__
    with open(src_path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    for lit in literals:
        assert "agent_runs.sqlite3" not in lit, \
            "agent_runs 里又硬编码了旧库文件名：%r" % lit
