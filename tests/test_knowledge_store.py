"""Tests for store.py — upsert / query / persistence / shape validation."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from financial_analyst.data.knowledge_index.store import ChromaStore


def _make_emb(seed: int, n: int = 3, dim: int = 4) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.rand(n, dim).astype(np.float32)
    return v


def test_upsert_then_query_returns_nearest(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=0)
    store.upsert(
        ids=["a", "b", "c"],
        embeddings=emb,
        metadatas=[{"src": "x"}, {"src": "y"}, {"src": "z"}],
        documents=["doc-a", "doc-b", "doc-c"],
    )
    assert store.count() == 3
    res = store.query(emb[0], n_results=1)
    # exact match — top hit should be 'a'
    assert res["ids"][0][0] == "a"
    assert res["documents"][0][0] == "doc-a"


def test_query_top_k(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=1, n=5, dim=4)
    store.upsert(
        ids=[f"id{i}" for i in range(5)],
        embeddings=emb,
        metadatas=[{"i": i} for i in range(5)],
        documents=[f"d{i}" for i in range(5)],
    )
    res = store.query(emb[0], n_results=3)
    assert len(res["ids"][0]) == 3


def test_persistence_across_instances(tmp_path: Path):
    """Close + reopen pointing at the same root must surface the prior data."""
    a = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=2)
    a.upsert(
        ids=["x", "y", "z"],
        embeddings=emb,
        metadatas=[{"name": "x"}, {"name": "y"}, {"name": "z"}],
        documents=["dx", "dy", "dz"],
    )
    assert a.count() == 3
    del a  # let GC release file handles before reopen

    b = ChromaStore(root=tmp_path)
    assert b.count() == 3
    ids = sorted(b.all_ids())
    assert ids == ["x", "y", "z"]
    # query should still hit
    res = b.query(emb[1], n_results=1)
    assert res["ids"][0][0] == "y"


def test_empty_upsert_is_noop(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    store.upsert(ids=[], embeddings=np.empty((0, 4), dtype=np.float32), metadatas=[], documents=[])
    assert store.count() == 0


def test_upsert_rejects_non_numpy(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    with pytest.raises(TypeError, match="numpy array"):
        store.upsert(
            ids=["a"],
            embeddings=[[1.0, 2.0, 3.0, 4.0]],  # list, not ndarray
            metadatas=[{}],
            documents=["doc"],
        )


def test_upsert_rejects_wrong_shape(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    # 1-D, not 2-D
    with pytest.raises(ValueError, match="2-D"):
        store.upsert(
            ids=["a"],
            embeddings=np.zeros(4, dtype=np.float32),
            metadatas=[{}],
            documents=["doc"],
        )


def test_upsert_rejects_mismatched_lengths(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=0, n=2, dim=4)
    with pytest.raises(ValueError, match="shape"):
        store.upsert(
            ids=["a", "b", "c"],  # 3
            embeddings=emb,        # 2
            metadatas=[{}, {}, {}],
            documents=["d", "d", "d"],
        )


def test_get_by_ids_returns_stored_metadata(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=3)
    store.upsert(
        ids=["a", "b", "c"],
        embeddings=emb,
        metadatas=[{"mtime": 100.0}, {"mtime": 200.0}, {"mtime": 300.0}],
        documents=["da", "db", "dc"],
    )
    got = store.get_by_ids(["a", "c"])
    assert sorted(got["ids"]) == ["a", "c"]
    by_id = dict(zip(got["ids"], got["metadatas"]))
    assert by_id["a"]["mtime"] == 100.0
    assert by_id["c"]["mtime"] == 300.0


def test_delete_removes_ids(tmp_path: Path):
    store = ChromaStore(root=tmp_path)
    emb = _make_emb(seed=4)
    store.upsert(
        ids=["a", "b", "c"],
        embeddings=emb,
        metadatas=[{"v": 1}, {"v": 2}, {"v": 3}],
        documents=["da", "db", "dc"],
    )
    store.delete(["b"])
    assert store.count() == 2
    assert sorted(store.all_ids()) == ["a", "c"]


def test_upsert_overwrites_existing_id(tmp_path: Path):
    """Same id with new embedding + doc updates in place, doesn't dupe."""
    store = ChromaStore(root=tmp_path)
    emb1 = _make_emb(seed=5, n=1, dim=4)
    store.upsert(ids=["a"], embeddings=emb1, metadatas=[{"v": 1}], documents=["v1"])
    assert store.count() == 1
    emb2 = _make_emb(seed=6, n=1, dim=4)
    store.upsert(ids=["a"], embeddings=emb2, metadatas=[{"v": 2}], documents=["v2"])
    assert store.count() == 1
    got = store.get_by_ids(["a"])
    assert got["documents"][0] == "v2"
    assert got["metadatas"][0]["v"] == 2
