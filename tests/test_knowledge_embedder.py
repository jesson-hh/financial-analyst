"""Tests for embedder.py — StubEmbedder shape + reproducibility, BGE smoke (slow)."""
from __future__ import annotations

import numpy as np
import pytest

from financial_analyst.data.knowledge_index.embedder import BgeEmbedder, StubEmbedder


def test_stub_encode_returns_expected_shape():
    e = StubEmbedder(dim=8)
    out = e.encode(["a", "b", "c"])
    assert isinstance(out, np.ndarray)
    assert out.shape == (3, 8)
    assert out.dtype == np.float32


def test_stub_encode_empty_input_returns_empty_array():
    e = StubEmbedder(dim=8)
    out = e.encode([])
    assert out.shape == (0, 8)


def test_stub_encode_is_deterministic():
    e1 = StubEmbedder(dim=16, random_state=42)
    e2 = StubEmbedder(dim=16, random_state=42)
    out1 = e1.encode(["foo", "bar"])
    out2 = e2.encode(["foo", "bar"])
    np.testing.assert_array_equal(out1, out2)


def test_stub_encode_same_text_same_vector():
    """Same text within one instance maps to identical vector — lets us
    detect cache hits / dedup downstream."""
    e = StubEmbedder(dim=8)
    out = e.encode(["repeat", "repeat", "different"])
    np.testing.assert_array_equal(out[0], out[1])
    assert not np.array_equal(out[0], out[2])


def test_stub_vectors_are_l2_normalised():
    e = StubEmbedder(dim=8)
    out = e.encode(["hello world", "another text"])
    norms = np.linalg.norm(out, axis=1)
    np.testing.assert_allclose(norms, np.ones(2), atol=1e-5)


def test_stub_different_seed_different_vectors():
    e1 = StubEmbedder(dim=8, random_state=0)
    e2 = StubEmbedder(dim=8, random_state=1)
    v1 = e1.encode(["x"])
    v2 = e2.encode(["x"])
    assert not np.array_equal(v1, v2)


def test_bge_default_attributes():
    """BgeEmbedder should expose dim + model_name without actually loading
    the 340MB model — that's the lazy-loading contract."""
    e = BgeEmbedder()
    assert e.dim == 1024
    assert e.model_name == "BAAI/bge-large-zh-v1.5"
    assert e._model is None  # not yet loaded


def test_bge_empty_input_returns_empty_array_without_loading():
    """Empty list shouldn't trigger the model download — short-circuit path."""
    e = BgeEmbedder()
    out = e.encode([])
    assert out.shape == (0, 1024)
    assert e._model is None  # still not loaded


@pytest.mark.slow
def test_bge_real_model_smoke(tmp_path):
    """End-to-end smoke against the real BGE model. Skipped by default —
    requires the 340MB model download. Run with ``pytest -m slow``."""
    e = BgeEmbedder()
    out = e.encode(["反转因子在 A 股很强", "动量在 A 股容易翻车"])
    assert out.shape == (2, 1024)
    assert out.dtype == np.float32
