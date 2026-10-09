# -*- coding: utf-8 -*-
"""M5 记忆同步链路：MemoryStore.add/edit/delete 的 outbox 钩子 + 回声抑制。

不起 HTTP（路由层薄）；embed 打桩（绝不真调 API）；全部临时目录。
"""
import json
import os
import sys
import tempfile

import pytest

BRAIN = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "brain"))
sys.path.insert(0, BRAIN)

import memory_store_v2 as ms  # noqa: E402
import sync_outbox            # noqa: E402


@pytest.fixture()
def outbox_env(tmp_path, monkeypatch):
    """outbox 文件指到 tmp + 假 flusher（只登记不发送）+ embed 打桩。"""
    monkeypatch.setattr(sync_outbox, "OUTBOX_PATH",
                        str(tmp_path / "outbox.jsonl"))
    sync_outbox.reset_for_test()
    sent = []
    sync_outbox.set_flusher(lambda entry: sent.append(entry) or True)
    monkeypatch.setattr(ms, "embed_texts", lambda texts, **kw: [])
    yield {"sent": sent, "path": str(tmp_path / "outbox.jsonl")}
    sync_outbox.reset_for_test()
    sync_outbox.set_flusher(None)


def _store(tmp_path, name="memory"):
    return ms.MemoryStore(str(tmp_path / (name + ".json")),
                          str(tmp_path / (name + ".idx")))


def _outbox(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_add_goes_to_outbox(outbox_env, tmp_path):
    st = _store(tmp_path)
    st.add("她怕黑", mtype="fact", src="app")
    entries = _outbox(outbox_env["path"])
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "memory_add"
    assert e["payload"]["store"] == "memory"
    assert e["payload"]["item"]["text"] == "她怕黑"
    assert e["payload"]["item"]["id"]


def test_edit_goes_to_outbox(outbox_env, tmp_path):
    st = _store(tmp_path)
    st.add("她怕黑", mtype="fact")
    iid = st.data["items"][0]["id"]
    out = st.edit(iid, "她怕黑，但不说")
    assert out and out["text"] == "她怕黑，但不说"
    kinds = [e["kind"] for e in _outbox(outbox_env["path"])]
    assert kinds == ["memory_add", "memory_edit"]
    last = _outbox(outbox_env["path"])[-1]
    assert last["payload"]["store"] == "memory"
    assert last["payload"]["item"]["id"] == iid
    assert last["payload"]["item"]["text"] == "她怕黑，但不说"


def test_edit_empty_text_is_noop(outbox_env, tmp_path):
    st = _store(tmp_path)
    st.add("她怕黑")
    iid = st.data["items"][0]["id"]
    assert st.edit(iid, "   ") is None          # 空文本拒绝
    assert st.edit("m_nosuch", "x") is None     # 不存在的 id
    kinds = [e["kind"] for e in _outbox(outbox_env["path"])]
    assert kinds == ["memory_add"]              # outbox 没多东西


def test_delete_tombstone_and_outbox(outbox_env, tmp_path):
    st = _store(tmp_path)
    st.add("她怕黑")
    iid = st.data["items"][0]["id"]
    st.delete(iid)
    assert st.list_items() == []
    entries = _outbox(outbox_env["path"])
    kinds = [e["kind"] for e in entries]
    assert kinds == ["memory_add", "memory_del"]
    assert entries[-1]["payload"]["id"] == iid
    # 墓碑进账本（增量拉取方靠它知道这条没了）
    assert any(t["id"] == iid for t in st.data["meta"].get("del", []))


def test_delete_missing_id_silent(outbox_env, tmp_path):
    st = _store(tmp_path)
    st.delete("m_nosuch")                        # 不抛、不记墓碑、不进 outbox
    assert st.data["meta"].get("del", []) == []
    assert _outbox(outbox_env["path"]) == []


def test_pull_suppresses_echo(outbox_env, tmp_path):
    """云端拉下来的增量落盘（走 suppress）不能再记回 outbox —— 死循环防线。"""
    st = _store(tmp_path)
    with sync_outbox.suppress():
        st.add("云端来的记忆", mtype="fact")
        st.save()
    assert _outbox(outbox_env["path"]) == []
    assert st.list_items()[0]["text"] == "云端来的记忆"


def test_no_flusher_means_noop(outbox_env, tmp_path):
    """同步没启用（flusher=None，= 云端权威源自己）：record 是 no-op，
    零开销，也不产生文件。"""
    sync_outbox.set_flusher(None)
    st = _store(tmp_path)
    st.add("没人同步这条")
    assert not os.path.exists(outbox_env["path"])


def test_store_name_whitelist(outbox_env, tmp_path):
    """open_store_by_name 的路径穿越防线：怪名字一律 None。"""
    for bad in ("../evil", "a/b", "a\\b", "..", "", None, "memory."):
        assert ms.open_store_by_name(bad) is None, repr(bad)
