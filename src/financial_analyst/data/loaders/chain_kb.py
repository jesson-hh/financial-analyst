"""Industry-chain knowledge base loader.

Each product node lives in one markdown file with YAML frontmatter::

    ---
    node_type: product
    node_id: AI_chip_GPU
    display_name: AI 加速 GPU/DCU
    category: compute_chain
    layer: upstream
    summary: 国产 AI 算力芯片, 对标 NVIDIA H100/H200, 国产替代核心.
    related_codes:
    - code: SH688256
      name: 寒武纪
      role: anchor
      weight: 1.0
      note: 'anchor: 国产 AI 加速卡龙头'
    upstream_products: [wafer_foundry_advanced, HBM_storage]
    downstream_products: [AI_server]
    alternatives: []
    ---
    # AI 加速 GPU/DCU
    ## 简介 ...
    ## 催化逻辑 ...

``ChainKBLoader`` parses all products into an in-memory graph and exposes
``chain_context(code)`` which returns the primary product + peers +
upstream/downstream + catalyst text, suitable for direct injection into
``factor-computer`` output so ``fundamental-analyst`` sees industry-chain
position alongside basic price/factor data.

Default path: ``~/.financial-analyst/memories/chain_kb/products/``.
Override via ``FA_CHAIN_KB_DIR`` env var or ctor arg.

Companion CLI: ``financial-analyst chain {list, show, for, import, stats}``.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class CodeRef:
    code: str
    name: str = ""
    role: str = ""        # anchor | data_supported | llm_inferred
    weight: float = 0.0
    note: str = ""


@dataclass
class Product:
    node_id: str
    display_name: str = ""
    category: str = ""       # which chain — e.g. "compute_chain", "lithium_chain"
    layer: str = ""          # upstream | midstream | downstream
    summary: str = ""
    related_codes: List[CodeRef] = field(default_factory=list)
    upstream_products: List[str] = field(default_factory=list)
    downstream_products: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)
    body_md: str = ""        # everything after the YAML frontmatter
    file_path: Optional[Path] = None

    def code_role(self, code: str) -> Optional[CodeRef]:
        for ref in self.related_codes:
            if ref.code == code:
                return ref
        return None


class ChainKBLoader:
    """Load the chain_kb product graph and provide stock-code lookups."""

    DEFAULT_DIR_ENV = "FA_CHAIN_KB_DIR"

    def __init__(self, root: Optional[Path] = None):
        if root is None:
            override = os.environ.get(self.DEFAULT_DIR_ENV, "")
            if override:
                root = Path(override).expanduser()
            else:
                root = Path.home() / ".financial-analyst" / "memories" / "chain_kb" / "products"
        self._root = Path(root)
        self._cache: Dict[str, Product] = {}
        self._code_index: Dict[str, List[str]] = {}  # code → [product_id, ...]
        self._loaded = False

    @property
    def root(self) -> Path:
        return self._root

    # ----- parsing ----------------------------------------------------------

    @staticmethod
    def _parse_product(path: Path) -> Optional[Product]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None
        if not text.startswith("---"):
            return None
        end = text.find("\n---", 3)
        if end < 0:
            return None
        try:
            fm = yaml.safe_load(text[3:end]) or {}
        except yaml.YAMLError:
            return None
        if fm.get("node_type") != "product":
            return None

        body_md = text[end + 4:].lstrip("\n")
        related = []
        for r in (fm.get("related_codes") or []):
            related.append(CodeRef(
                code=str(r.get("code", "")),
                name=str(r.get("name", "")),
                role=str(r.get("role", "")),
                weight=float(r.get("weight", 0.0) or 0.0),
                note=str(r.get("note", "")),
            ))

        return Product(
            node_id=str(fm.get("node_id", path.stem)),
            display_name=str(fm.get("display_name", "")),
            category=str(fm.get("category", "")),
            layer=str(fm.get("layer", "")),
            summary=str(fm.get("summary", "")),
            related_codes=related,
            upstream_products=list(fm.get("upstream_products") or []),
            downstream_products=list(fm.get("downstream_products") or []),
            alternatives=list(fm.get("alternatives") or []),
            body_md=body_md,
            file_path=path,
        )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._cache.clear()
        self._code_index.clear()
        if not self._root.is_dir():
            self._loaded = True
            return
        for path in self._root.glob("*.md"):
            prod = self._parse_product(path)
            if prod is None:
                continue
            self._cache[prod.node_id] = prod
            for ref in prod.related_codes:
                self._code_index.setdefault(ref.code, []).append(prod.node_id)
        self._loaded = True

    def reload(self) -> None:
        self._loaded = False
        self._ensure_loaded()

    # ----- queries ----------------------------------------------------------

    def list_products(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._cache.keys())

    def list_categories(self) -> List[str]:
        self._ensure_loaded()
        return sorted({p.category for p in self._cache.values() if p.category})

    def get(self, product_id: str) -> Optional[Product]:
        self._ensure_loaded()
        return self._cache.get(product_id)

    def products_for_code(self, code: str) -> List[Product]:
        """All products whose related_codes mention ``code``."""
        self._ensure_loaded()
        ids = self._code_index.get(code, [])
        return [self._cache[i] for i in ids if i in self._cache]

    def peer_codes(self, product_id: str, *, exclude: Optional[str] = None,
                   max_n: int = 8, min_weight: float = 0.3) -> List[CodeRef]:
        """Other stocks in the same product node, sorted by weight."""
        prod = self.get(product_id)
        if prod is None:
            return []
        peers = [r for r in prod.related_codes
                 if r.code != exclude and r.weight >= min_weight]
        peers.sort(key=lambda r: r.weight, reverse=True)
        return peers[:max_n]

    def chain_context(self, code: str, *, max_peers: int = 6,
                      max_products: int = 3,
                      body_chars: int = 1500) -> Optional[dict]:
        """Build a compact chain_context dict for downstream LLM agents.

        Returns ``None`` if the stock isn't in any product. Otherwise:

        ::

            {
              "stock": "SH688256",
              "primary_product": {
                "id": "AI_chip_GPU", "display_name": "AI 加速 GPU/DCU",
                "category": "compute_chain", "layer": "upstream",
                "role_for_stock": "anchor", "weight_for_stock": 1.0,
                "summary": "...",
              },
              "all_products": [{...}, ...],   # up to max_products
              "upstream_products": [...names...],
              "downstream_products": [...names...],
              "peer_codes": [{"code": ..., "name": ..., "role": ..., "weight": ...}],
              "catalyst_md": "## 催化逻辑\\n...",  # tail of primary product body
            }
        """
        products = self.products_for_code(code)
        if not products:
            return None
        # Rank by (role priority, weight) — anchor first, then weight desc
        role_rank = {"anchor": 3, "data_supported": 2, "llm_inferred": 1}

        def _sort_key(p: Product):
            ref = p.code_role(code)
            return (role_rank.get(ref.role if ref else "", 0),
                    ref.weight if ref else 0.0)

        products = sorted(products, key=_sort_key, reverse=True)
        primary = products[0]
        primary_ref = primary.code_role(code)

        def _shape(p: Product) -> dict:
            ref = p.code_role(code)
            return {
                "id": p.node_id,
                "display_name": p.display_name,
                "category": p.category,
                "layer": p.layer,
                "role_for_stock": ref.role if ref else "",
                "weight_for_stock": ref.weight if ref else 0.0,
                "summary": p.summary,
            }

        # Catalyst text from primary product — usually under "## 催化逻辑"
        catalyst = ""
        body = primary.body_md or ""
        marker = body.find("## 催化")
        if marker < 0:
            marker = body.find("## Catalyst")
        if marker >= 0:
            catalyst = body[marker:marker + body_chars]
        elif body:
            catalyst = body[:body_chars]

        return {
            "stock": code,
            "primary_product": {
                **_shape(primary),
                "summary": primary.summary,
            },
            "all_products": [_shape(p) for p in products[:max_products]],
            "upstream_products": list(primary.upstream_products),
            "downstream_products": list(primary.downstream_products),
            "peer_codes": [
                {"code": r.code, "name": r.name, "role": r.role, "weight": r.weight}
                for r in self.peer_codes(primary.node_id, exclude=code, max_n=max_peers)
            ],
            "catalyst_md": catalyst.strip(),
        }

    # ----- management -------------------------------------------------------

    def import_from(self, source_dir: Path, *, overwrite: bool = False) -> int:
        """Copy ``<source_dir>/*.md`` (products only) into the loader's root."""
        import shutil
        source_dir = Path(source_dir).expanduser()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source not a directory: {source_dir}")
        self._root.mkdir(parents=True, exist_ok=True)
        n = 0
        for src in source_dir.glob("*.md"):
            if src.stem.startswith("_"):  # skip _templates, _generated
                continue
            # Quick sanity: only copy files starting with `---\nnode_type: product`
            try:
                head = src.read_text(encoding="utf-8")[:200]
            except Exception:
                continue
            if "node_type: product" not in head:
                continue
            dst = self._root / src.name
            if dst.exists() and not overwrite:
                continue
            shutil.copy2(src, dst)
            n += 1
        self._loaded = False  # invalidate cache
        return n

    def stats(self) -> dict:
        self._ensure_loaded()
        if not self._cache:
            return {"n_products": 0, "n_categories": 0, "n_codes_indexed": 0,
                    "root": str(self._root)}
        return {
            "n_products": len(self._cache),
            "n_categories": len(self.list_categories()),
            "n_codes_indexed": len(self._code_index),
            "categories": self.list_categories(),
            "root": str(self._root),
        }
