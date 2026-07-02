# -*- coding: utf-8 -*-
"""AI投研框架文件加载/校验(spec 2026-07-02-ai-industry-dashboard §3)。"""
import pytest


def test_load_framework_ok():
    from guanlan_v2.industry.framework import load_framework, segment_ids
    fw = load_framework()
    assert fw["meta"]["id"] == "ai_chain"
    sids = segment_ids(fw)
    # 28 满信号环节 + 2 相邻链 stub
    assert len(sids) == 30
    full = [s for s in fw["segments"] if not s.get("adjacent")]
    assert len(full) == 28
    assert {"A1", "C2", "I3", "M1", "F4", "G2"} <= set(sids)


def test_ids_are_consistent():
    from guanlan_v2.industry.framework import load_framework
    fw = load_framework()
    sids = {s["id"] for s in fw["segments"]}
    dids = {d["id"] for d in fw["drivers"]}
    assert len(fw["drivers"]) == 7 and len(fw["edges"]) == 15 and len(fw["narratives"]) == 8
    for e in fw["edges"]:
        for ref in e["from"] + e["to"]:
            assert ref in sids | dids, f"edge {e['id']} 引用了不存在的 {ref}"
    for n in fw["narratives"]:
        for a in n["activates"]:
            assert a["segment"] in sids, f"narrative {n['id']} 引用了不存在的 {a['segment']}"


def test_pool_and_digest():
    from guanlan_v2.industry.framework import load_framework, segment_pool, all_pool_codes, framework_digest
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    assert "SH688498" in pool                      # 源杰科技锚票
    assert all(c[:2] in ("SH", "SZ", "BJ") for c in all_pool_codes(fw))
    dg = framework_digest(fw)
    assert "C2" in dg and "光芯片" in dg and len(dg) < 8000   # 摘要必须紧凑,能塞进 prompt


def test_bad_reference_raises(tmp_path):
    from guanlan_v2.industry.framework import load_framework, FrameworkError
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "meta: {id: x, name: x, version: 1}\n"
        "drivers: [{id: D1, name: d, indicators: []}]\n"
        "groups: [{id: A, name: g}]\n"
        "segments: [{id: A1, name: s, group: A, logic: l, keywords: [k], stocks: []}]\n"
        "edges: [{id: T1, from: [D9], to: [A1], sign: '+', mechanism: m, lag: l, validation: [v]}]\n"
        "narratives: []\nsignal_defs: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(FrameworkError):
        load_framework(str(bad))
