# -*- coding: utf-8 -*-
import pandas as pd


def test_build_candidates_marks_existing():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.pool_candidates import build_candidates
    fw = load_framework()
    idx = pd.DataFrame([{"concept_name": "共封装光学(CPO)", "concept_code": "886001"}])
    cons = pd.DataFrame([
        {"concept_code": "886001", "stock_code": "300308", "stock_name": "中际旭创"},
        {"concept_code": "886001", "stock_code": "688498", "stock_name": "源杰科技"},
        {"concept_code": "886001", "stock_code": "301301", "stock_name": "某新票"},
    ])
    cands = build_candidates(fw, cons, idx)
    c2 = {c["code"]: c for c in cands["C2"]}
    assert c2["SH688498"]["already_in_pool"] is True      # 已是锚票
    assert c2["SZ301301"]["already_in_pool"] is False     # 新候选
    assert all(v["concept"] == "共封装光学(CPO)" for v in c2.values())


def test_segment_without_concept_gets_empty():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.pool_candidates import build_candidates
    fw = load_framework()
    cands = build_candidates(fw, pd.DataFrame(columns=["concept_code", "stock_code", "stock_name"]),
                             pd.DataFrame(columns=["concept_name", "concept_code"]))
    assert cands["C4"] == []                              # C4 ths_concepts 为空
