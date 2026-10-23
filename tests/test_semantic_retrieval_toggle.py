# -*- coding: utf-8 -*-
"""Pure-BM25 mode: the independent semantic toggle must gate every embed path."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))


@pytest.fixture
def retrieval_off(tmp_path, monkeypatch):
    import paths
    from storage import unified
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"retrieval": {
        "unified": True, "semantic_enabled": False,
    }}), encoding="utf-8")
    monkeypatch.setattr(paths, "CONFIG_PATH", str(cfg_path))
    assert unified.unified_enabled()
    assert not unified.semantic_enabled()
    return cfg_path


def test_semantic_setting_is_independent_of_unified(retrieval_off):
    from storage import unified
    assert unified.unified_enabled() is True
    assert unified.semantic_enabled() is False
    assert unified._backend().mode == "none"
    assert unified.reindex_embeddings(doc_ids=["example"]) == {
        "ok": False, "reason": "semantic_disabled", "indexed": 0,
    }
    retrieval_off.write_text(json.dumps({"retrieval": {
        "unified": True, "semantic_enabled": True,
    }}), encoding="utf-8")
    assert unified.semantic_enabled() is True


def test_vector_paths_do_not_touch_embedding_when_off(retrieval_off):
    from storage import search

    class TrapBackend:
        def available(self):
            raise AssertionError("embedding backend must not be probed")
        def embed(self, texts):
            raise AssertionError("embedding backend must not be called")

    assert search.vector_search("semantic query", backend=TrapBackend()) == []
    result = search.index_embeddings(["doc:1"], backend=TrapBackend())
    assert result["reason"] == "semantic_disabled"
    assert result["indexed"] == 0


def test_unified_retrieve_keeps_bm25_entry_when_semantic_off(retrieval_off, monkeypatch):
    from storage import unified, search
    called = {}

    def capture(query, scope=None, kinds=None, top_k=6,
                budget_chars=2400, backend=None):
        called["query"] = query
        called["backend_mode"] = backend.mode
        return [], {"method": "bm25", "hits": 0, "vector_available": False}

    monkeypatch.setattr(search, "search", capture)
    results, info = unified.retrieve("keyword")
    assert results == []
    assert info["method"] == "bm25"
    assert called == {"query": "keyword", "backend_mode": "none"}


def test_kb_ingest_text_uses_no_embed_when_off(retrieval_off, monkeypatch):
    from kb import engine as kb_engine

    class FakeStore:
        def __init__(self):
            self.upserts = []
        def upsert_text(self, name, chunks):
            self.upserts.append((name, chunks))
            return len(chunks)
        def set_source_meta(self, *args, **kwargs):
            pass

    store = FakeStore()
    monkeypatch.setattr(kb_engine, "store_for", lambda *a: store)
    monkeypatch.setattr(kb_engine, "split_text", lambda *a: [{"text": "keyword paragraph"}])
    monkeypatch.setattr(kb_engine._cfg, "load", lambda: {
        "chunk_size": 500, "chunk_overlap": 0, "batch": 10,
        "sync_to_unified": True,
    })
    monkeypatch.setattr(kb_engine._emb, "embed", lambda *a: (_ for _ in ()).throw(
        AssertionError("semantic-disabled ingest must not embed")))
    monkeypatch.setattr(kb_engine, "_sync_chunks_to_unified", lambda *a, **kw: {
        "ok": True, "indexed": 1, "vectorized": 0,
    })

    result = kb_engine.ingest_text("keyword paragraph", "test.txt", "", "app")
    assert result["ok"]
    assert result["chunks"] == 1
    assert result["unified_sync"]["vectorized"] == 0
    assert store.upserts[0][0] == "test.txt"


def test_kb_text_only_persistence(tmp_path):
    pytest.importorskip("numpy")
    from kb.store import KBStore
    store = KBStore(str(tmp_path / "kb"))
    assert store.upsert_text("one.md", [{"text": "first paragraph"}]) == 1
    assert store.upsert_text("two.md", [{"text": "second paragraph"}]) == 1
    reopened = KBStore(str(tmp_path / "kb"))
    assert {item["source"] for item in reopened.chunks()} == {"one.md", "two.md"}
    assert reopened.stats()["count"] == 2
    assert reopened.stats()["dim"] == 0


def test_switching_semantic_back_on_preserves_bm25_only_documents(tmp_path):
    pytest.importorskip("numpy")
    from kb.store import KBStore
    root = str(tmp_path / "kb-mixed")
    store = KBStore(root)
    store.upsert_text("old.md", [{"text": "text indexed while BM25-only"}])
    assert store.upsert("new.md", [{"text": "new semantic document"}],
                        [[0.6, 0.8]], "fake-model:2") == 1
    restored = KBStore(root)
    assert {c["source"] for c in restored.chunks()} == {"old.md", "new.md"}
    assert restored.stats()["dim"] == 2
    restored.delete_source("old.md")
    assert [c["source"] for c in KBStore(root).chunks()] == ["new.md"]
