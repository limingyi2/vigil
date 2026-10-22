# -*- coding: utf-8 -*-
"""RAG acceptance tests"""
import sys
import os
import time
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "brain"))

TEST_DIR = tempfile.mkdtemp(prefix="vigil_rag_test_")
os.environ["VIGIL_DATA_DIR"] = TEST_DIR
os.environ["VIGIL_CONFIG_DIR"] = TEST_DIR

from storage import db as _db
_db.set_dir(TEST_DIR)

from storage import db, search, vector, unified
from kb import engine as kb_engine
from kb import config as kb_config

TEST_BACKEND = vector.EmbeddingBackend(mode="local", model="BAAI/bge-small-zh-v1.5")

print("Test dir:", TEST_DIR)
try:
    import faiss
    print("FAISS:", faiss.__version__)
except ImportError:
    print("FAISS: not installed")
    faiss = None

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, detail)


def setup_db():
    db.ensure_schema("main")
    conn = db.connection("main")
    conn.execute("DELETE FROM search_docs")
    conn.execute("DELETE FROM embeddings")
    conn.commit()


def test_p0_scope_isolation():
    print("\n=== P0: ANN scope isolation ===")
    if faiss is None:
        print("  [SKIP] FAISS not installed")
        return
    setup_db()
    docs_a = [("sA:1", "A1", "machine learning deep learning AI"),
              ("sA:2", "A2", "natural language processing NLP")]
    docs_b = [("sB:1", "B1", "cooking food recipe"),
              ("sB:2", "B2", "travel scenery vacation")]
    for did, title, body in docs_a + docs_b:
        owner = "ownerA" if did.startswith("sA") else "ownerB"
        persona = "personaA" if did.startswith("sA") else "personaB"
        search.upsert_doc(did, "memory", body, title=title,
                          owner=owner, src="app", persona=persona, chan="")
    all_ids = [d[0] for d in docs_a + docs_b]
    result = search.index_embeddings(all_ids, backend=TEST_BACKEND)
    check("index ok", result.get("ok"), str(result))

    scope_a = {"owner": "ownerA", "persona": "personaA", "src": "app", "chan": ""}
    scope_b = {"owner": "ownerB", "persona": "personaB", "src": "app", "chan": ""}

    ra, _ = search.search("machine learning", scope=scope_a, top_k=10, backend=TEST_BACKEND)
    check("scopeA no scopeB docs", all(r.doc_id.startswith("sA") for r in ra),
          "got: %s" % [r.doc_id for r in ra])

    rb, _ = search.search("cooking", scope=scope_b, top_k=10, backend=TEST_BACKEND)
    check("scopeB no scopeA docs", all(r.doc_id.startswith("sB") for r in rb),
          "got: %s" % [r.doc_id for r in rb])

    for i in range(3):
        ra, _ = search.search("AI", scope=scope_a, top_k=5, backend=TEST_BACKEND)
        rb, _ = search.search("food", scope=scope_b, top_k=5, backend=TEST_BACKEND)
        check("alt %d A isolated" % (i+1), all(r.doc_id.startswith("sA") for r in ra))
        check("alt %d B isolated" % (i+1), all(r.doc_id.startswith("sB") for r in rb))

    vector._ANN_INDEXES.clear()
    ra2, _ = search.search("machine learning", scope=scope_a, top_k=10, backend=TEST_BACKEND)
    check("after reload scopeA isolated", all(r.doc_id.startswith("sA") for r in ra2),
          "got: %s" % [r.doc_id for r in ra2])


def test_p1_kb_delete():
    print("\n=== P1: KB delete special chars ===")
    setup_db()
    kb_config.set_config({"enabled": True})
    names = ["normal.md", "with%pct.md", "with_under.md",
             "chinese_name.md", "prefix_1.md", "prefix_2.md"]
    for n in names:
        kb_engine.ingest_text("content of %s keyword testsearch" % n, n,
                              persona_key="", src="app")
    for n in names:
        results = kb_engine.search("testsearch", persona_key="", src="app", top_k=20)
        sources = {r.get("source") for r in results}
        check("ingested %s" % n, n in sources, "sources: %s" % sources)

    kb_engine.delete_doc("with%pct.md", persona_key="", src="app")
    results = kb_engine.search("testsearch", persona_key="", src="app", top_k=20)
    sources = {r.get("source") for r in results}
    check("delete pct no residue", "with%pct.md" not in sources, "residue: %s" % sources)
    check("delete pct others remain", "normal.md" in sources)

    kb_engine.delete_doc("prefix_1.md", persona_key="", src="app")
    results = kb_engine.search("testsearch", persona_key="", src="app", top_k=20)
    sources = {r.get("source") for r in results}
    check("prefix_2 remains", "prefix_2.md" in sources)
    check("prefix_1 no residue", "prefix_1.md" not in sources)

    kb_engine.ingest_text("personaB content", "cross_persona.md",
                          persona_key="personaB", src="app")
    kb_engine.delete_doc("cross_persona.md", persona_key="", src="app")
    rb = kb_engine.search("personaB", persona_key="personaB", src="app", top_k=5)
    check("delete other persona not affected", len(rb) > 0)

    kb_engine.clear(persona_key="", src="app")
    results = kb_engine.search("testsearch", persona_key="", src="app", top_k=20)
    check("clear default no residue", len(results) == 0)
    rb = kb_engine.search("personaB", persona_key="personaB", src="app", top_k=5)
    check("clear not affect other persona", len(rb) > 0)


def test_p1_cache_identity():
    print("\n=== P1: cache key includes backend identity ===")
    setup_db()
    search._QUERY_CACHE.clear()
    b1 = vector.EmbeddingBackend(mode="local", model="BAAI/bge-small-zh-v1.5")
    b2 = vector.EmbeddingBackend(mode="local", model="BAAI/bge-small-en-v1.5")
    search._cached_query_embed("testquery", b1)
    k1 = set(search._QUERY_CACHE.keys())
    search._cached_query_embed("testquery", b2)
    k2 = set(search._QUERY_CACHE.keys())
    check("different backend different key", len(k2) > len(k1),
          "k1=%d k2=%d" % (len(k1), len(k2)))
    allk = " ".join(k2)
    check("cache key has model info",
          "bge" in allk.lower() or "BAAI" in allk, "keys: %s" % list(k2)[:2])


def test_p1_ann_update():
    print("\n=== P1: ANN same doc_id update ===")
    if faiss is None:
        print("  [SKIP] FAISS not installed")
        return
    setup_db()
    search.upsert_doc("upd:1", "memory", "original: apple iphone launch",
                      title="orig", owner="u1", src="app", persona="p1", chan="")
    search.index_embeddings(["upd:1"], backend=TEST_BACKEND)
    search.upsert_doc("upd:1", "memory", "updated: android new phone release",
                      title="updated", owner="u1", src="app", persona="p1", chan="")
    search.index_embeddings(["upd:1"], backend=TEST_BACKEND)
    results, _ = search.search("android", scope={"owner": "u1", "persona": "p1"},
                               top_k=5, backend=TEST_BACKEND)
    check("updated content searchable",
          any("android" in (r.body or "").lower() for r in results),
          "results: %s" % [(r.doc_id, r.body[:30]) for r in results])


def test_p1_ann_consistency():
    print("\n=== P1: ANN vs brute force consistency ===")
    if faiss is None:
        print("  [SKIP] FAISS not installed")
        return
    setup_db()
    docs = [("c:1", "python", "python programming tutorial"),
            ("c:2", "ml", "machine learning supervised unsupervised"),
            ("c:3", "food", "cooking recipe delicious"),
            ("c:4", "dl", "deep learning neural network"),
            ("c:5", "travel", "beijing travel guide")]
    for did, title, body in docs:
        search.upsert_doc(did, "memory", body, title=title,
                          owner="u", src="app", persona="p", chan="")
    search.index_embeddings([d[0] for d in docs], backend=TEST_BACKEND)
    scope = {"owner": "u", "persona": "p", "src": "app", "chan": ""}
    results, _ = search.search("artificial intelligence", scope=scope,
                               top_k=5, backend=TEST_BACKEND)
    check("results sorted desc",
          all(results[i].score >= results[i+1].score for i in range(len(results)-1)),
          "scores: %s" % [r.score for r in results])
    top2 = [r.doc_id for r in results[:2]]
    check("semantic relevant ranked high",
          "c:2" in top2 or "c:4" in top2, "top2: %s" % top2)


def test_p2_idle_watcher():
    print("\n=== P2: idle unload watcher ===")
    w = vector._IDLE_WATCHER
    check("watcher thread started", w is not None and w.is_alive(),
          "watcher: %s" % w)
    b = vector.EmbeddingBackend(mode="local", model="BAAI/bge-small-zh-v1.5",
                                idle_unload_seconds=1)
    b.embed(["test"])
    check("model loaded", b._local is not None)
    b._last_used = time.time() - 2
    b._check_idle_unload()
    check("idle timeout unloads", b._local is None)
    vecs, _ = b.embed(["reload test"])
    check("reload after unload", len(vecs) > 0 and b._local is not None)


def test_windows_build():
    print("\n=== Windows build: productName ===")
    import json
    pkg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client", "package.json")
    if os.path.exists(pkg_path):
        pkg = json.loads(open(pkg_path, encoding="utf-8").read())
        pn = pkg.get("productName")
        check("package.json has productName", pn is not None, "productName: %s" % pn)
        check("productName is Kestrel", pn == "Kestrel", "actual: %s" % pn)
    else:
        print("  [SKIP] package.json not found")


if __name__ == "__main__":
    print("=" * 60)
    print("RAG Acceptance Tests")
    print("=" * 60)
    try:
        test_p0_scope_isolation()
        test_p1_kb_delete()
        test_p1_cache_identity()
        test_p1_ann_update()
        test_p1_ann_consistency()
        test_p2_idle_watcher()
        test_windows_build()
    finally:
        shutil.rmtree(TEST_DIR, ignore_errors=True)
    print("\n" + "=" * 60)
    print("Results: %d passed, %d failed" % (PASS, FAIL))
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)
