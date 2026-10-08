# -*- coding: utf-8 -*-
"""M2 云端权威源 + 同步层：账本 seq / 墓碑 / 幂等 upsert / 人设 version / 行为哈希。

全部用临时家（VIGIL_HOME）跑，不碰真实数据。
"""
import json
import os
import sys
import tempfile
import time

import pytest

BRAIN = os.path.dirname(os.path.abspath(__file__)) + os.sep + ".." + \
    os.sep + "brain"
BRAIN = os.path.normpath(BRAIN)
sys.path.insert(0, BRAIN)


@pytest.fixture()
def home(monkeypatch):
    """一个全新的临时家，import 前设好 VIGIL_HOME（paths 模块级读它）。"""
    d = tempfile.mkdtemp(prefix="vigil_sync_")
    monkeypatch.setenv("VIGIL_HOME", d)
    for m in [m for m in list(sys.modules)
              if m in ("paths", "memory_store_v2", "persona_store",
                       "sync_outbox", "sync_client", "features", "chatty")]:
        sys.modules.pop(m, None)
    import paths as _p
    _p.ensure()
    yield d
    for m in [m for m in list(sys.modules)
              if m in ("memory_store_v2", "persona_store",
                       "sync_outbox", "sync_client")]:
        sys.modules.pop(m, None)


def _ms():
    import memory_store_v2
    return memory_store_v2


def _ps():
    import persona_store
    return persona_store


def _so():
    import sync_outbox
    return sync_outbox


# ============================================================================
# 记忆账本
# ============================================================================

def test_memory_seq_increments_and_persists(home):
    ms = _ms()
    st = ms.open_store_by_name("memory")
    st.add("第一条记忆", mtype="event")
    st.add("第二条记忆", mtype="event")
    seqs = sorted(int(i["seq"]) for i in st.list_items())
    assert seqs == [1, 2]
    # 重开（新实例走同一缓存）账本号不回退
    st2 = ms.open_store_by_name("memory")
    assert st2.ledger()["last_seq"] == 2


def test_changes_after_returns_increment_only(home):
    ms = _ms()
    st = ms.open_store_by_name("memory")
    st.add("老的", mtype="event")
    d = st.changes_after(0)
    assert d["last_seq"] >= 1 and len(d["items"]) == 1
    after = d["last_seq"]
    st.add("新的", mtype="event")
    d2 = st.changes_after(after)
    assert [i["text"] for i in d2["items"]] == ["新的"]
    assert d2["deleted"] == []


def test_old_items_without_seq_get_backfilled(home):
    """存量记忆（没有 seq 的老数据）第一次对账时补号，否则永远同步不出去。"""
    ms = _ms()
    p = os.path.join(home, "data", "memory", "memory.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"items": [{"id": "mold1", "type": "event", "text": "旧条目",
                              "time": "2026-01-01 10:00"}],
                   "meta": {"created": "x"}}, f, ensure_ascii=False)
    st = ms.open_store_by_name("memory")
    d = st.changes_after(0)
    assert len(d["items"]) == 1 and d["items"][0]["seq"] == 1


def test_delete_records_tombstone_and_changes_after(home):
    ms = _ms()
    st = ms.open_store_by_name("memory")
    st.add("会被删的", mtype="event")
    iid = st.list_items()[0]["id"]
    st.delete(iid)
    assert st.list_items() == []
    d = st.changes_after(0)
    assert any(t["id"] == iid for t in d["deleted"])
    assert st.ledger()["deleted"] >= 1


def test_upsert_many_idempotent(home):
    """重推同一批不重复 —— M2 验收的核心一条。"""
    ms = _ms()
    st = ms.open_store_by_name("memory")
    item = {"id": "mabc123", "type": "event", "text": "本地新增的记忆",
            "time": "2026-10-09 12:00"}
    r1 = st.upsert_many([item])
    r2 = st.upsert_many([item])
    assert r1["added"] == 1
    assert r2["added"] == 0 and r2["same"] == 1
    assert len(st.list_items()) == 1


def test_upsert_same_id_different_text_updates(home):
    ms = _ms()
    st = ms.open_store_by_name("memory")
    base = {"id": "mabc123", "type": "event", "text": "第一版"}
    st.upsert_many([base])
    st.upsert_many([dict(base, text="第二版")])
    items = st.list_items()
    assert len(items) == 1 and items[0]["text"] == "第二版"
    assert items[0]["seq"] == 2


def test_upsert_delete_flow(home):
    ms = _ms()
    st = ms.open_store_by_name("memory")
    st.upsert_many([{"id": "mx", "text": "推上来的"}])
    r = st.upsert_many([], deletes=["mx"])
    assert r["deleted"] == 1
    assert st.list_items() == []
    # 重推删除也幂等（条目已经没了，不新增墓碑）
    r2 = st.upsert_many([], deletes=["mx"])
    assert r2["deleted"] == 0


def test_apply_pull_keeps_cloud_seq(home):
    """拉取落盘保留云端的账本号，本地计数器跟着抬，不产生错位。"""
    ms = _ms()
    st = ms.open_store_by_name("memory")
    with _so().suppress():
        st.apply_pull([{"id": "mcloud1", "text": "云端来的", "seq": 41,
                        "type": "event"}],
                      [{"id": "mcloud_old", "seq": 40}])
    assert st.list_items()[0]["seq"] == 41
    assert st.ledger()["last_seq"] == 41
    # 本地再写新记忆，seq 从 42 继续，不会跟云端的号撞
    st.add("本地写的", mtype="event")
    assert st.list_items()[-1]["seq"] == 42


def test_sanitize_name_blocks_traversal(home):
    ms = _ms()
    assert ms.MemoryStore.sanitize_name("../../etc/passwd") is None
    assert ms.MemoryStore.sanitize_name("..") is None
    assert ms.MemoryStore.sanitize_name("linzhixia.app.memory") == \
        "linzhixia.app.memory"
    assert ms.MemoryStore.sanitize_name("memory") == "memory"
    assert ms.open_store_by_name("../config") is None


# ============================================================================
# outbox
# ============================================================================

def test_outbox_records_local_writes_only_when_enabled(home):
    ms, so = _ms(), _so()
    st = ms.open_store_by_name("memory")
    st.add("没启用同步时不记账", mtype="event")
    assert so.pending_count() == 0        # flusher 没注册 = 同步没启用
    so.set_flusher(lambda e: True)        # 启用
    st.add("启用了要记账", mtype="event")
    iid = [i["id"] for i in st.list_items() if i["text"] == "启用了要记账"][0]
    st.delete(iid)
    kinds = [e["kind"] for e in so.load_pending()]
    assert kinds == ["memory_add", "memory_del"]


def test_outbox_suppress_blocks_pull_echo(home):
    ms, so = _ms(), _so()
    so.set_flusher(lambda e: True)
    st = ms.open_store_by_name("memory")
    with so.suppress():
        st.apply_pull([{"id": "m1", "text": "拉下来的", "seq": 5}], [])
        st.upsert_many([{"id": "m2", "text": "推上来的也走 apply 的别路"}])
    assert so.pending_count() == 0


def test_outbox_rewrite_pending_removes_done(home):
    so = _so()
    so.set_flusher(lambda e: True)
    so.record("memory_add", {"store": "memory", "item": {"id": "a"}})
    so.record("memory_add", {"store": "memory", "item": {"id": "b"}})
    entries = so.load_pending()
    so.rewrite_pending(entries[1:])
    assert [e["payload"]["item"]["id"] for e in so.load_pending()] == ["b"]


# ============================================================================
# 人设 version
# ============================================================================

def test_persona_save_bumps_version_and_records(home):
    ps, so = _ps(), _so()
    so.set_flusher(lambda e: True)
    ps.save({"name": "测试人设", "background": "b"}, "default")
    assert ps.version_of("default") == 1
    ps.save({"name": "测试人设2"}, "default")
    assert ps.version_of("default") == 2
    kinds = {e["kind"] for e in so.load_pending()}
    assert "persona" in kinds


def test_persona_write_pull_sets_cloud_version(home):
    ps = _ps()
    ps.write_pull({"name": "云端版本"}, "default", 7)
    assert ps.version_of("default") == 7      # 抄云端的号，不自己涨
    # 之后本地自己改，从 8 往上
    ps.save({"name": "本地改"}, "default")
    assert ps.version_of("default") == 8


def test_persona_push_apply_idempotent_and_conflict(home):
    ps = _ps()
    ps.save({"name": "v1"}, "default")            # version 1
    s1, v1 = ps.push_apply("default", {"name": "v1"}, 1)
    assert (s1, v1) == ("same", 1)                # 重推
    s2, v2 = ps.push_apply("default", {"name": "本地新版"}, 3)
    assert s2 == "applied" and v2 == 3
    s3, v3 = ps.push_apply("default", {"name": "旧推送"}, 2)
    assert s3 == "conflict" and v3 == 3           # 云端已有更高版本
    assert ps.read_fields("default")["name"] == "本地新版"


def test_persona_delete_tombstone(home):
    ps = _ps()
    ps.save({"name": "会删的"}, "tmp_p")
    ps.remove("tmp_p", current_key="")
    assert ps.deleted_versions().get("tmp_p", 0) >= 2
    snap = ps.sync_snapshot()
    assert "tmp_p" in snap["deleted"]
    assert all(p["key"] != "tmp_p" for p in snap["personas"])


def test_persona_sync_file_not_listed_as_template(home):
    ps = _ps()
    ps.save({"name": "A"}, "default")
    keys = [k for k, _ in ps.list_personas()]
    assert keys == ["default"]
    assert ps.template_count() == 1


# ============================================================================
# 行为段
# ============================================================================

def test_behavior_version_stable_and_sensitive(home):
    import sync_client
    d1 = {"features": {"weather": True}, "chatty": {"default": 5}}
    d2 = {"features": {"weather": True}, "chatty": {"default": 5}}
    d3 = {"features": {"weather": False}, "chatty": {"default": 5}}
    assert sync_client.behavior_version(d1) == sync_client.behavior_version(d2)
    assert sync_client.behavior_version(d1) != sync_client.behavior_version(d3)


def test_behavior_apply_only_touches_three_segments(home, monkeypatch):
    import sync_client
    # 本地 config 里预置一个 api_key，应用行为段之后必须原样还在
    cfg_path = os.path.join(home, "config", "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"api_key": "sk-local-only", "brain_token": "t"}, f)
    ok = sync_client.behavior_apply(
        {"features": {"weather": False}, "features_by_src": {"wechat": {}},
         "chatty": {"default": 3}, "api_key": "sk-EVIL"})
    assert ok
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["features"]["weather"] is False
    assert cfg["chatty"]["default"] == 3
    assert cfg["api_key"] == "sk-local-only"          # 只动三个键
    assert cfg["brain_token"] == "t"
