from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace

from financial_analyst.agent.evidence_prompt import project_company_history_for_prompt


def _large_sections():
    from test_report_prompt_evidence import _full_evidence

    sections = _full_evidence()["sections"]
    points = [{"date": str(date(2021, 1, 1) + timedelta(days=index)),
               "as_of": str(date(2021, 1, 1) + timedelta(days=index)),
               "value": 10 + index / 1000, "unit": "x", "period": "TTM",
               "ref": f"local:test:{index}"} for index in range(2000)]
    refs = [f"pack.sections.valuation.history_series.points[{index}]" for index in range(2000)]
    coverage = {"valid_positive_points": 2000, "missing_dates": ["2026-08-01"], "nonpositive_dates": []}
    warnings = [{"code": "current_mutable_history_not_pit", "message": "current only"}]
    sections["valuation"].update({
        "history_series": {"metric": "ttm_pe", "points": points, "coverage": coverage,
                           "status": "available", "warnings": warnings, "point_in_time": False},
        "history_pb_series": {"metric": "pb", "points": deepcopy(points), "coverage": deepcopy(coverage),
                              "status": "available", "warnings": deepcopy(warnings)},
    })
    sections["company_research_manifest"] = {"status": "incomplete", "gaps": [{"code": "source_failed"}]}
    sections["company_valuation_dag"] = {
        "status": "incomplete", "gaps": [{"code": "ev_missing"}],
        "nodes": [{"calc_id": "valuation.historical_percentile", "status": "complete", "result": 95,
                   "input_refs": refs, "warnings": deepcopy(warnings), "gaps": [],
                   "depends_on": ["valuation.ttm_pe"]},
                  {"calc_id": "valuation.enterprise_value", "status": "incomplete", "result": None,
                   "input_refs": ["missing.debt"], "gaps": [{"code": "missing_input"}]}],
        "edges": [{"from": "input:" + ref, "to": "valuation.historical_percentile"} for ref in refs]
                 + [{"from": "valuation.ttm_pe", "to": "valuation.historical_percentile"}],
    }
    sections["company_vibe_valuation"] = {"status": "complete", "calculations": [{
        "function": "percentile_rank", "calculation_id": "CAL-PERCENTILE", "status": "complete",
        "result": {"value": 95, "unit": "%"}, "output": {"status": "ok", "value": 95},
        "inputs": {"history": deepcopy(points), "current_ttm_pe": {"value": 82}},
        "input_refs": refs + ["CAL-CURRENT-PE"], "gaps": [], "warnings": deepcopy(warnings),
    }]}
    return sections


def test_projection_is_nonmutating_and_preserves_all_outcomes_and_real_gaps():
    source = _large_sections()
    before = deepcopy(source)
    view = project_company_history_for_prompt(source)
    assert source == before
    assert view["announcements"] == source["announcements"]
    assert view["company_research_manifest"] == source["company_research_manifest"]
    for key in ("history_series", "history_pb_series"):
        projected, original = view["valuation"][key], source["valuation"][key]
        assert projected["coverage"] == original["coverage"]
        assert projected["warnings"] == original["warnings"]
        assert projected["status"] == original["status"]
        nav = projected["points_omitted_from_prompt"]
        assert nav["omitted_from_prompt"] is True and nav["original_count"] == 2000
        assert nav["first_date"] == original["points"][0]["date"]
        assert nav["last_date"] == original["points"][-1]["date"]
        assert len(nav["content_sha256"]) == 64
        assert "points" not in projected
    dag = view["company_valuation_dag"]
    assert dag["nodes"][1] == source["company_valuation_dag"]["nodes"][1]
    assert dag["edges"] == [{"from": "valuation.ttm_pe", "to": "valuation.historical_percentile"}]
    for projected, original in zip(dag["nodes"], source["company_valuation_dag"]["nodes"]):
        for key in ("calc_id", "result", "status", "gaps", "warnings"):
            assert projected.get(key) == original.get(key)
    calc = view["company_vibe_valuation"]["calculations"][0]
    original = source["company_vibe_valuation"]["calculations"][0]
    for key in ("calculation_id", "result", "output", "status", "gaps", "warnings"):
        assert calc[key] == original[key]
    assert calc["input_refs"] == ["CAL-CURRENT-PE"]
    assert calc["inputs"]["current_ttm_pe"] == original["inputs"]["current_ttm_pe"]
    assert len(json.dumps(view)) < len(json.dumps(source)) * 0.1


def test_writer_and_fundamental_actual_prompts_omit_history_arrays_without_mutating_inputs(monkeypatch, tmp_path):
    from test_report_prompt_evidence import _run, _quote, _rw_response, _extract_evidence_json, rw_mod, fa_mod

    ev = {"ok": True, "sections": _large_sections()}
    before = deepcopy(ev)
    captured = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
                    {"code": "SH603986", "asof_date": date.today().isoformat(),
                     "out_dir": str(tmp_path), "quote-fetcher": _quote(), "evidence-loader": ev},
                    _rw_response("研究证据不足"))
    prompt = _extract_evidence_json(captured["user"])
    assert "points" not in prompt["valuation"]["history_series"]
    assert prompt["valuation"]["history_series"]["points_omitted_from_prompt"]["original_count"] == 2000
    assert len(captured["user"]) < 50000
    assert ev == before
    captured = _run(monkeypatch, fa_mod, fa_mod.FundamentalAnalyst,
                    {"quote-fetcher": _quote(), "evidence-loader": ev},
                    {"valuation_score": 1, "mv_tier": "mid"})
    prompt = _extract_evidence_json(captured["user"])
    assert "points" not in prompt["valuation"]["history_pb_series"]
    assert len(captured["user"]) < 50000
    assert ev == before


def test_prompt_projection_does_not_open_mutable_bins_for_historical_research(monkeypatch):
    from guanlan_v2.reports import evidence

    monkeypatch.setattr(evidence, "_hk_today", lambda: date(2026, 9, 8))
    monkeypatch.setattr(evidence, "_local_valuation_history", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("historical bins read")))
    monkeypatch.setattr(evidence, "_valuation_bands", lambda *a: (_ for _ in ()).throw(AssertionError("historical network read")))
    monkeypatch.setattr(evidence, "_engine_data_module", lambda name: SimpleNamespace(
        load_facts=lambda *a, **kw: SimpleNamespace(to_dict=lambda: {"valuation": {"bvps": 4}})))
    sections = {"valuation": evidence._sec_valuation("SH600667", "2026-09-07")}
    assert project_company_history_for_prompt(sections) == sections
