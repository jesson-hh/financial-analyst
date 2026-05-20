"""Tests for ChainKBLoader (v1.4.5)."""
from __future__ import annotations
from pathlib import Path

import pytest

from financial_analyst.data.loaders.chain_kb import ChainKBLoader, Product, CodeRef


def _write_product(root: Path, product_id: str, *,
                   category: str = "compute_chain",
                   layer: str = "upstream",
                   related: list = None,
                   upstream: list = None,
                   downstream: list = None) -> Path:
    related = related or []
    upstream = upstream or []
    downstream = downstream or []
    related_yaml = ""
    for r in related:
        related_yaml += (
            f"- code: {r['code']}\n"
            f"  name: {r.get('name', '')}\n"
            f"  role: {r.get('role', 'data_supported')}\n"
            f"  weight: {r.get('weight', 0.5)}\n"
            f"  note: {r.get('note', '')}\n"
        )
    body = (
        "---\n"
        f"node_type: product\n"
        f"node_id: {product_id}\n"
        f"display_name: {product_id} display\n"
        f"category: {category}\n"
        f"layer: {layer}\n"
        f"summary: summary for {product_id}\n"
        "related_codes:\n"
        f"{related_yaml}"
        f"upstream_products: {upstream}\n"
        f"downstream_products: {downstream}\n"
        "alternatives: []\n"
        "---\n\n"
        f"# {product_id}\n\n"
        "## 简介\nbody body\n\n"
        "## 催化逻辑\n- catalyst line 1\n- catalyst line 2\n"
    )
    path = root / f"{product_id}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_loader_default_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FA_CHAIN_KB_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    loader = ChainKBLoader()
    assert ".financial-analyst" in str(loader.root)
    assert loader.root.parts[-2:] == ("chain_kb", "products")


def test_loader_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_CHAIN_KB_DIR", str(tmp_path))
    loader = ChainKBLoader()
    assert loader.root == tmp_path


def test_empty_loader_returns_none_for_unknown(tmp_path):
    loader = ChainKBLoader(root=tmp_path)
    assert loader.list_products() == []
    assert loader.get("nonexistent") is None
    assert loader.products_for_code("SH600519") == []
    assert loader.chain_context("SH600519") is None


def test_parse_product(tmp_path):
    _write_product(tmp_path, "AI_chip_GPU",
                   category="compute_chain", layer="upstream",
                   related=[
                       {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
                       {"code": "SZ300474", "name": "景嘉微", "role": "data_supported", "weight": 0.74},
                   ],
                   upstream=["wafer_foundry_advanced", "HBM_storage"],
                   downstream=["AI_server"])
    loader = ChainKBLoader(root=tmp_path)
    prod = loader.get("AI_chip_GPU")
    assert prod is not None
    assert prod.display_name == "AI_chip_GPU display"
    assert prod.category == "compute_chain"
    assert prod.layer == "upstream"
    assert len(prod.related_codes) == 2
    assert prod.related_codes[0].code == "SH688256"
    assert prod.related_codes[0].role == "anchor"
    assert prod.related_codes[0].weight == 1.0
    assert prod.upstream_products == ["wafer_foundry_advanced", "HBM_storage"]
    assert prod.downstream_products == ["AI_server"]
    assert "催化逻辑" in prod.body_md


def test_products_for_code(tmp_path):
    """One stock can appear in multiple products."""
    _write_product(tmp_path, "AI_chip_GPU", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
    ])
    _write_product(tmp_path, "edge_AI", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "data_supported", "weight": 0.6},
    ])
    _write_product(tmp_path, "AI_server", related=[
        {"code": "SH600100", "name": "同方", "role": "anchor", "weight": 1.0},
    ])
    loader = ChainKBLoader(root=tmp_path)
    results = loader.products_for_code("SH688256")
    assert len(results) == 2
    assert {p.node_id for p in results} == {"AI_chip_GPU", "edge_AI"}


def test_chain_context_anchor_ranks_first(tmp_path):
    """When a stock is in multiple products, anchor role wins over data_supported."""
    _write_product(tmp_path, "AI_chip_GPU", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
    ])
    _write_product(tmp_path, "edge_AI", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "data_supported", "weight": 0.9},
    ])
    loader = ChainKBLoader(root=tmp_path)
    ctx = loader.chain_context("SH688256")
    assert ctx is not None
    assert ctx["primary_product"]["id"] == "AI_chip_GPU"
    assert ctx["primary_product"]["role_for_stock"] == "anchor"


def test_chain_context_peer_codes(tmp_path):
    _write_product(tmp_path, "AI_chip_GPU", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
        {"code": "SH688041", "name": "海光信息", "role": "anchor", "weight": 1.0},
        {"code": "SZ300474", "name": "景嘉微", "role": "data_supported", "weight": 0.74},
        {"code": "SH688521", "name": "芯原", "role": "data_supported", "weight": 0.43},
        {"code": "SH688385", "name": "复旦微", "role": "llm_inferred", "weight": -0.08},
    ])
    loader = ChainKBLoader(root=tmp_path)
    ctx = loader.chain_context("SH688256", max_peers=4)
    peers = ctx["peer_codes"]
    # 4 max; SH688256 excluded; 复旦微 filtered out (-0.08 < min_weight 0.3)
    assert len(peers) <= 4
    assert "SH688256" not in [p["code"] for p in peers]
    assert "SH688385" not in [p["code"] for p in peers]  # negative weight excluded
    # Sorted by weight descending
    weights = [p["weight"] for p in peers]
    assert weights == sorted(weights, reverse=True)


def test_chain_context_catalyst_extracted(tmp_path):
    _write_product(tmp_path, "AI_chip_GPU", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
    ])
    loader = ChainKBLoader(root=tmp_path)
    ctx = loader.chain_context("SH688256")
    assert "催化逻辑" in ctx["catalyst_md"]
    assert "catalyst line 1" in ctx["catalyst_md"]


def test_chain_context_unknown_code_returns_none(tmp_path):
    _write_product(tmp_path, "AI_chip_GPU", related=[
        {"code": "SH688256", "name": "寒武纪", "role": "anchor", "weight": 1.0},
    ])
    loader = ChainKBLoader(root=tmp_path)
    assert loader.chain_context("SH999999") is None


def test_list_categories(tmp_path):
    _write_product(tmp_path, "AI_chip", category="compute_chain", related=[])
    _write_product(tmp_path, "lithium_carbonate", category="lithium_chain", related=[])
    _write_product(tmp_path, "AI_server", category="compute_chain", related=[])
    loader = ChainKBLoader(root=tmp_path)
    assert loader.list_categories() == ["compute_chain", "lithium_chain"]


def test_import_from_skips_non_product_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # Valid product
    _write_product(src, "AI_chip", related=[{"code": "SH688256", "name": "x"}])
    # Not a product (no frontmatter)
    (src / "README.md").write_text("plain readme\n", encoding="utf-8")
    # Underscore-prefixed (template / generated)
    (src / "_template.md").write_text("---\nnode_type: product\nnode_id: t\n---\n", encoding="utf-8")
    # Wrong node_type
    (src / "theme.md").write_text("---\nnode_type: theme\nnode_id: t\n---\n", encoding="utf-8")

    dst = tmp_path / "dst"
    loader = ChainKBLoader(root=dst)
    n = loader.import_from(src)
    assert n == 1  # only AI_chip
    assert (dst / "AI_chip.md").exists()
    assert not (dst / "README.md").exists()
    assert not (dst / "_template.md").exists()
    assert not (dst / "theme.md").exists()


def test_reload_picks_up_new_files(tmp_path):
    loader = ChainKBLoader(root=tmp_path)
    assert loader.list_products() == []
    _write_product(tmp_path, "AI_chip", related=[{"code": "SH688256"}])
    # Without reload, cached empty result is still returned
    assert loader.list_products() == []
    loader.reload()
    assert loader.list_products() == ["AI_chip"]


def test_stats(tmp_path):
    loader = ChainKBLoader(root=tmp_path)
    assert loader.stats()["n_products"] == 0
    _write_product(tmp_path, "AI_chip", category="compute_chain", related=[
        {"code": "SH688256", "name": "寒武纪"},
    ])
    _write_product(tmp_path, "lithium", category="lithium_chain", related=[
        {"code": "SZ002460", "name": "赣锋锂业"},
    ])
    loader.reload()
    s = loader.stats()
    assert s["n_products"] == 2
    assert s["n_categories"] == 2
    assert s["n_codes_indexed"] == 2
