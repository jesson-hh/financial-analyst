# -*- coding: utf-8 -*-
"""票池候选生成(离线 CLI):同花顺概念成分 → 每环节候选清单 JSON。

只产候选,绝不自动改 ai_chain.yaml——人工审核后手动并入(spec §3.2)。
用法: <引擎python> -m guanlan_v2.industry.pool_candidates --out var/industry_pool_candidates.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .framework import load_framework
from .llmx import _norm_code

_DEFAULT_PARQUET_ROOT = r"G:/stocks/stock_data/parquet"


def build_candidates(fw: dict, constituents, index_df) -> dict:
    name2code = dict(zip(index_df["concept_name"].astype(str), index_df["concept_code"].astype(str)))
    out: dict = {}
    for s in fw["segments"]:
        if s.get("adjacent"):
            continue
        pool = {x["code"] for x in s.get("stocks", [])}
        rows = []
        for cname in s.get("ths_concepts", []):
            ccode = name2code.get(cname)
            if not ccode:
                continue
            sub = constituents[constituents["concept_code"].astype(str) == ccode]
            for _, r in sub.iterrows():
                code = _norm_code(str(r["stock_code"]))
                if not code:
                    continue
                rows.append({"code": code, "name": str(r.get("stock_name") or ""),
                             "concept": cname, "already_in_pool": code in pool})
        seen: set = set()
        uniq = []
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            uniq.append(r)
        out[s["id"]] = uniq
    return out


def main() -> None:
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="var/industry_pool_candidates.json")
    args = ap.parse_args()
    root = Path(os.environ.get("GL_PARQUET_ROOT") or _DEFAULT_PARQUET_ROOT)
    cons = pd.read_parquet(root / "concept_ths_constituent.parquet")
    idx = pd.read_parquet(root / "concept_ths_index.parquet")
    # Rename to match build_candidates interface expectations
    if "name" in idx.columns and "concept_name" not in idx.columns:
        idx = idx.rename(columns={"name": "concept_name"})
    if "short_name" in cons.columns and "stock_name" not in cons.columns:
        cons = cons.rename(columns={"short_name": "stock_name"})
    fw = load_framework()
    cands = build_candidates(fw, cons, idx)
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for v in cands.values())
    print(f"candidates written: {outp} segments={len(cands)} rows={n}")


if __name__ == "__main__":
    main()
