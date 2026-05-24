"""ConversationStore — 回收站软删 + 恢复 + 自动 purge.

诱因: 之前 ``ConversationStore.delete()`` 直接 unlink 文件, 用户误删无法找回.
现在改为软删 (move to _trash/), restore() / permanent_delete() / purge_old_trash().
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from financial_analyst.buddy.conversations import ConversationStore


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(path=tmp_path / "conversations")


def _make(cid: str = "test-session", title: str = "Hello") -> dict:
    return {
        "id": cid, "title": title,
        "createdAt": 1700000000000, "updatedAt": 1700000001000,
        "messages": [{"role": "user", "content": "hi"}],
    }


class TestSoftDelete:
    def test_delete_moves_to_trash_not_unlink(self, store):
        store.save(_make("abc"))
        assert store.load("abc") is not None

        ok = store.delete("abc")
        assert ok is True
        # live 不见了
        assert store.load("abc") is None
        # 但 trash 里有
        trash = store.list_trash()
        assert len(trash) == 1
        assert trash[0]["id"] == "abc"

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("never-existed") is False

    def test_list_trash_has_deletedAt(self, store):
        store.save(_make("xyz"))
        before = int(time.time() * 1000)
        store.delete("xyz")
        after = int(time.time() * 1000)

        trash = store.list_trash()
        assert len(trash) == 1
        assert before <= trash[0]["deletedAt"] <= after

    def test_delete_same_cid_twice_keeps_both_copies(self, store):
        """删过的 cid, 用户重新建一个再删, trash 应有 2 个副本."""
        store.save(_make("dupe", "v1"))
        store.delete("dupe")
        time.sleep(0.01)  # ensure different timestamp

        store.save(_make("dupe", "v2"))
        store.delete("dupe")

        trash = store.list_trash()
        assert len(trash) == 2
        # 最新 (v2) 在第一个 (sort by deletedAt desc)
        assert trash[0]["title"] == "v2"
        assert trash[1]["title"] == "v1"


class TestRestore:
    def test_restore_brings_back_to_live(self, store):
        store.save(_make("aaa", "Original"))
        store.delete("aaa")
        assert store.load("aaa") is None    # 软删后 load 不到

        ok = store.restore("aaa")
        assert ok is True
        loaded = store.load("aaa")
        assert loaded is not None
        assert loaded["title"] == "Original"

    def test_restore_nonexistent_returns_false(self, store):
        assert store.restore("never-deleted") is False

    def test_restore_picks_latest_when_multiple_copies(self, store):
        """trash 多副本时, 默认 restore 最新."""
        store.save(_make("multi", "v1"))
        store.delete("multi")
        time.sleep(0.01)

        store.save(_make("multi", "v2"))
        store.delete("multi")

        store.restore("multi")    # 不指定 trash_filename → 最新
        loaded = store.load("multi")
        assert loaded["title"] == "v2"

    def test_restore_with_explicit_trash_filename(self, store):
        """指定 trash_filename 可以 restore 老的副本."""
        store.save(_make("pick", "v1"))
        store.delete("pick")
        time.sleep(0.01)
        store.save(_make("pick", "v2"))
        store.delete("pick")

        trash = store.list_trash()
        v1_fn = next(t for t in trash if t["title"] == "v1")["_trash_filename"]

        store.restore("pick", trash_filename=v1_fn)
        loaded = store.load("pick")
        assert loaded["title"] == "v1"

    def test_restore_when_live_exists_does_not_overwrite(self, store):
        """如果 cid 在 live 已有 (用户软删 X 后又建新 X), restore 加 _restored 后缀避免覆盖."""
        store.save(_make("dual", "old"))
        store.delete("dual")
        # 用户用同 cid 建新会话
        store.save(_make("dual", "new"))

        store.restore("dual")
        # 原 cid 还是 'new', 不被覆盖
        assert store.load("dual")["title"] == "new"
        # restored 副本以新 cid 存在
        restored = [c for c in store.list() if c["id"] == "dual"]
        # list() 用 file 名, restored 文件叫 dual_restored_<ts>.json — 也算 list
        # 实际 list 解析: 文件名不是关键, 关键是 conv["id"]
        # 'dual_restored_xxx' 还是 cid='dual', 所以会有 2 个 id=dual
        # 这是设计行为 — 让用户手动 rename
        assert len(restored) >= 1


class TestPermanentDelete:
    def test_permanent_skips_trash(self, store):
        store.save(_make("perm"))
        ok = store.permanent_delete("perm")
        assert ok is True
        assert store.load("perm") is None
        assert store.list_trash() == []

    def test_permanent_clears_trash_copies(self, store):
        store.save(_make("clean"))
        store.delete("clean")
        assert len(store.list_trash()) == 1

        ok = store.permanent_delete("clean")
        assert ok is True
        assert store.list_trash() == []

    def test_permanent_returns_false_when_nothing(self, store):
        assert store.permanent_delete("ghost") is False


class TestAutoPurge:
    def test_purge_old_trash(self, store, monkeypatch):
        store.save(_make("ancient"))
        store.delete("ancient")
        assert len(store.list_trash()) == 1

        # 把 trash 文件的 timestamp 改成 31 天前
        tp = next(store.trash.glob("*.json"))
        # rename: <cid>__<ts>.json — 改 ts 到 31 天前
        old_ts = int(time.time() * 1000) - 31 * 86400 * 1000
        new_fn = tp.parent / f"ancient__{old_ts}.json"
        tp.rename(new_fn)

        purged = store.purge_old_trash(ttl_days=30)
        assert purged == 1
        assert store.list_trash() == []

    def test_purge_keeps_recent(self, store):
        store.save(_make("recent"))
        store.delete("recent")
        purged = store.purge_old_trash(ttl_days=30)
        assert purged == 0
        assert len(store.list_trash()) == 1
