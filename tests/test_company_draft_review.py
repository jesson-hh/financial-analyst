from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from datetime import date

import pytest

from financial_analyst.agent.company_draft_review import (
    inspect_company_draft, revise_company_draft_once,
)


def _inputs():
    from test_company_report_gate import _evidence, _calculations, _report
    from test_company_risk_gate import _complete_issuer_evidence

    evidence = _evidence()
    sections = evidence["evidence_pack"]["sections"]
    sections["company_research_manifest"] = evidence["hub"]
    sections["company_vibe_valuation"] = {"calculations": _calculations()}
    for row in evidence["hub"]["source_register"]:
        if row["evidence_id"] == "CE-EST":
            row["stages"].append("estimates")
    sections["company_estimates_contract"] = {
        "status": "complete", "calculations": [row for row in _complete_issuer_evidence()
                                                if row.get("function")],
    }
    report = _report()
    contract = report["risk_contract"]
    group = contract["counter_evidence_groups"][0]
    report["company_risk_input"] = {
        "strong_claims": [{"kind": "strong_claim", "claim_id": group["claim_id"],
                           "claim": group["claim"], "strong_claim": True,
                           "source_refs": group["issuer_claim_source_refs"]}],
        "counter_evidence": [{"kind": "counter_evidence", "claim_id": group["claim_id"],
                              "counter_evidence": row["statement"],
                              "relative_direction": row["relative_direction"],
                              "source_refs": row["issuer_source_refs"]}
                             for row in group["counter_evidence"]],
        "decision_points": contract["decision_points"],
    }
    return sections, report


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return {"choices": [{"message": {"content": self.response}}]}


def test_preflight_uses_real_gate_without_promoting_lifecycle_or_mutating_evidence():
    sections, report = _inputs()
    sections["company_research_manifest"]["status"] = "incomplete"
    sections["company_research_manifest"]["lifecycle_pending_stages"] = ["risk", "report"]
    sections["company_research_manifest"]["gaps"] = [{
        "stage": "risk", "code": "strong_claim_inventory_missing", "critical": True,
        "message": "Preparation-time model claims are not written yet.",
    }]
    before = deepcopy(sections)
    inspected = inspect_company_draft(report, sections)
    assert inspected["status"] == "no_writer_findings"
    assert inspected["final_acceptance"] is False
    assert sections == before
    for old, new, expected in (
        ("100亿元", "101亿元", "unsupported_numeric_claim"),
        ("[evidence:CE-FIN]", "[evidence:CE-CONTEXT]", "context_used_as_issuer_evidence"),
        ("[calculation:CAL-PE]", "[calculation:calc-?]", "section_citation_missing"),
    ):
        changed = dict(report, markdown_body=report["markdown_body"].replace(old, new))
        codes = {finding["code"] for finding in inspect_company_draft(changed, sections)["findings"]}
        assert expected in codes


def test_one_revision_can_correct_precise_citation_with_full_drafts_preserved():
    sections, good = _inputs()
    bad = dict(good, markdown_body=good["markdown_body"].replace("100亿元", "101亿元"))
    client = _Client(json.dumps(good, ensure_ascii=False))
    output, audit = asyncio.run(revise_company_draft_once(client, [], bad, sections))
    assert output == good
    assert len(client.calls) == 1
    assert audit["selected"] == "revision" and audit["final_acceptance"] is False
    assert audit["drafts"] == {"initial": bad, "revision": good}
    assert "unsupported_numeric_claim" in client.calls[0]["messages"][-1]["content"]
    assert sections["financials"]["revenue"] == 10_000_000_000


def test_missing_assumption_id_alone_gets_one_bounded_revision_without_promoting_warning():
    sections, good = _inputs()
    bad = dict(good, markdown_body=good["markdown_body"].replace("[assumption:ASM-GROWTH]", ""))
    original = deepcopy(bad)
    reviewed = inspect_company_draft(bad, sections)
    assert reviewed["status"] == "needs_revision"
    warning = next(row for row in reviewed["findings"] if row["code"] == "assumption_not_registered")
    assert warning["severity"] == "medium"  # final gate severity is untouched
    assert "assumption_not_registered" in reviewed["warning_codes"]
    client = _Client(json.dumps(good, ensure_ascii=False))
    selected, audit = asyncio.run(revise_company_draft_once(client, [], bad, sections))
    assert selected == good and audit["selected"] == "revision"
    assert len(client.calls) == 1 and bad == original
    feedback = json.loads(client.calls[0]["messages"][-1]["content"].split("\n", 1)[1])
    finding = next(row for row in feedback["findings"] if row["code"] == "assumption_not_registered")
    assert "情景假设：收入增长 10%" in finding["draft_line"]
    assert "section_id" in finding["repair_guidance"]
    assert audit["final_acceptance"] is False


def test_revision_cannot_reduce_fact_errors_by_turning_them_into_unregistered_assumptions():
    sections, good = _inputs()
    bad = dict(good, markdown_body=good["markdown_body"].replace("100亿元", "101亿元").replace("20倍", "21倍"))
    candidate = dict(good, markdown_body=good["markdown_body"].replace(
        "公司收入为 100亿元。[evidence:CE-FIN]",
        "情景假设：公司收入为 101亿元。[evidence:CE-FIN]",
    ))
    client = _Client(json.dumps(candidate, ensure_ascii=False))
    selected, audit = asyncio.run(revise_company_draft_once(client, [], bad, sections))
    assert selected == bad and audit["selected"] == "initial"
    assert "assumption_not_registered" in audit["revision"]["finding_codes"]
    assert len(client.calls) == 1


def test_revision_feedback_keeps_line_scope_and_never_mutates_or_invents_values():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    parsed = {"markdown_body": "one\n" + "x" * 1601}
    review = {"findings": [{"code": "assumption_value_mismatch", "line": 2}]
              + [{"code": "numeric_claim_uncited", "line": 500}] * 80}
    before = deepcopy((parsed, review))
    feedback = _revision_feedback(parsed, review)
    assert len(feedback["findings"]) == 80 and feedback["omitted_findings"] == 1
    assert feedback["findings"][0]["draft_line"] == "x" * 1600
    assert feedback["findings"][0]["draft_line_truncated"] is True
    assert "不要把事实基线补入 assumptions" in feedback["findings"][0]["repair_guidance"]
    assert "draft_line" not in feedback["findings"][1]
    assert (parsed, review) == before


def test_company_prompt_gives_separate_baseline_scenario_and_signed_value_contract():
    from financial_analyst.agent.tier3.report_writer import COMPANY_RISK_INPUT_PROMPT

    assert "同一物理行" in COMPANY_RISK_INPUT_PROMPT
    assert "assumption_id、section_id、value 或 values" in COMPANY_RISK_INPUT_PROMPT
    assert "负值不能因写“净流出”而改成正数" in COMPANY_RISK_INPUT_PROMPT
    assert "日期用 YYYY-MM-DD" in COMPANY_RISK_INPUT_PROMPT
    assert "顶层 assumptions 与 summary_json.assumptions" in COMPANY_RISK_INPUT_PROMPT
    assert "省略公式常数不等于省略口径" in COMPANY_RISK_INPUT_PROMPT
    assert "不能混成同一历史样本" in COMPANY_RISK_INPUT_PROMPT


def test_revision_feedback_includes_bound_signed_fields_with_date_not_replacement_values():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["financials"]["flows"] = [
        {"date": "2026-08-18", "main_net": -123456789, "unit": "元"},
        {"date": "2026-08-19", "main_net": 7, "unit": "元"},
    ]
    # A short date token must not outrank the disputed amount merely because
    # many earlier rows happen to contain that day-of-month.
    sections["financials"] = {"earlier_rows": [{"date": "2026-07-18"} for _ in range(30)],
                               **sections["financials"]}
    report["markdown_body"] = "主力净流出 123,456,789 元。[evidence:CE-FIN]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1,
                             "details": {"number": "123,456,789 元"}},
                            {"code": "unsupported_numeric_claim", "line": 1,
                             "details": {"number": "18"}}]}
    before = deepcopy((sections, report, review))
    feedback = _revision_feedback(report, review, sections)
    ref = feedback["reference_navigation"]["references"][0]
    assert ref["scope"] == "issuer" and ref["usable"] is True
    assert ref["fields"][0]["path"] == "financials.flows[0].main_net"
    assert ref["fields"][0]["value"] == -123456789
    assert ref["fields"][0]["context"]["date"] == "2026-08-18"
    assert ref["fields"][0]["context"]["unit"] == "元"
    assert ref["selection"] == "nearby_original_fields_not_replacement_values"
    assert (sections, report, review) == before


def test_revision_feedback_never_borrows_other_sources_or_calculation_inputs():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["company_vibe_valuation"]["calculations"][0].update(
        result={"value": 20, "unit": "multiple"}, formula="price / eps * 100",
        inputs={"refs": ["CE-VAL"], "undocumented_constant": 100},
    )
    report["markdown_body"] = "PE 21 倍。[calculation:CAL-PE]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1,
                             "details": {"number": "21 倍"}}]}
    feedback = _revision_feedback(report, review, sections)
    refs = feedback["reference_navigation"]["references"]
    assert len(refs) == 1 and refs[0]["kind"] == "calculation"
    assert {row["path"] for row in refs[0]["fields"]} == {
        "calculation:cal-pe.result.value", "calculation:cal-pe.result.unit",
    }
    assert {str(row["value"]) for row in refs[0]["fields"]} == {"20", "multiple"}
    assert "undocumented_constant" not in json.dumps(feedback)
    assert "financials.revenue" not in json.dumps(feedback)


def test_revision_feedback_prunes_unusable_nested_data_and_does_not_upgrade_invalid_calculation():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["financials"]["failed_provider"] = {"status": "failed", "value": 555777}
    sections["company_vibe_valuation"]["calculations"][0].update(status="invalid", result=555777)
    report["markdown_body"] = "收入555777。[evidence:CE-FIN] PE555777。[calculation:CAL-PE]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1,
                             "details": {"number": "555777"}}]}
    refs = _revision_feedback(report, review, sections)["reference_navigation"]["references"]
    assert "failed_provider" not in json.dumps(refs)
    calculation = next(row for row in refs if row["kind"] == "calculation")
    assert calculation["status"] == "invalid" and calculation["usable"] is False
    assert calculation["fields"] == []


@pytest.mark.parametrize("rejected", [
    {"status": "failed"}, {"status": "ok", "future": True}, {"status": "ok", "usable": False},
])
def test_revision_feedback_keeps_original_list_indices_after_rejected_source_rows(rejected):
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["financials"]["rows"] = [
        {**rejected, "value": 987654, "unit": "DO-NOT-EXPOSE"},
        {"status": "ok", "value": 123456, "date": "2026-08-19", "unit": "元"},
    ]
    report["markdown_body"] = "收入123456。[evidence:CE-FIN]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1,
                             "details": {"number": "123456"}}]}
    original = deepcopy(sections)
    ref = _revision_feedback(report, review, sections)["reference_navigation"]["references"][0]
    value = next(row for row in ref["fields"] if row["value"] == 123456)
    assert value["path"] == "financials.rows[1].value"
    assert value["context"]["date"] == "2026-08-19"
    assert all(not row["path"].startswith("financials.rows[0]") for row in ref["fields"])
    assert "987654" not in json.dumps(ref) and "DO-NOT-EXPOSE" not in json.dumps(ref)
    assert sections == original


def test_revision_feedback_keeps_original_selected_assumption_indices_when_pruning():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    report["assumptions"] = [
        {"assumption_id": "FAILED", "status": "failed", "value": "99%"},
        {"assumption_id": "ACTUAL", "section_id": "scenarios_verdict", "value": "12%"},
    ]
    feedback = _revision_feedback(report, {"findings": []}, sections)
    fields = feedback["selected_assumptions"]["fields"]
    value = next(row for row in fields if row["value"] == "12%")
    assert value["path"] == "selected_assumptions[1].value"
    assert value["context"]["assumption_id"] == "ACTUAL"
    assert "FAILED" not in json.dumps(fields) and "99%" not in json.dumps(fields)


@pytest.mark.parametrize("missing_ref", ["CE-UNKNOWN", "CAL-ABSENT"])
def test_revision_feedback_rejects_complete_output_with_unresolved_calculation_lineage(missing_ref):
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["company_vibe_valuation"]["calculations"].append({
        "calculation_id": "CAL-MISSING", "status": "complete",
        "input_refs": [missing_ref], "result": {"value": 123456, "unit": "ratio"},
    })
    report["markdown_body"] = "指标123456。[calculation:CAL-MISSING]"
    review = {"findings": [{"code": "calculation_lineage_unresolved", "line": 1}]}
    ref = _revision_feedback(report, review, sections)["reference_navigation"]["references"][0]
    assert ref["status"] == "complete"  # Preserve the original calculation state.
    assert ref["usable"] is False and ref["fields"] == []
    assert ref["unresolved_input_refs"] == [missing_ref]
    assert ref["unusable_reason"] == "calculation_lineage_unresolved"
    assert "123456" not in json.dumps(ref)


def test_revision_feedback_resolves_workspace_snapshot_by_original_registered_identity():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    sections["company_research_manifest"]["source_register"].append({
        "evidence_id": "CE-SNAPSHOT", "source_id": "dataset.company.test",
        "source_ref": "snapshot-original", "status": "ok", "usable": True, "type": "context",
    })
    sections["workspace_live"] = {"datasets": {"test": {
        "asset_id": "snapshot-original", "snapshot": {"dataset_id": "dataset.company.test",
            "status": "ok", "rows": [{"period": "2026-07", "value": 456, "unit": "TWD"}]},
    }}}
    report["markdown_body"] = "行业数字457。[evidence:CE-SNAPSHOT]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1}]}
    ref = _revision_feedback(report, review, sections)["reference_navigation"]["references"][0]
    assert ref["scope"] == "context" and ref["evidence_id"] == "CE-SNAPSHOT"
    assert any(row["path"].endswith("rows[0].value") and row["value"] == 456 for row in ref["fields"])


@pytest.mark.parametrize("citation,details", [
    ("[证据:CE-FIN]", {}), ("[CE-FIN]", {}), ("", {"cited_refs": ["CE-FIN"]}),
])
def test_revision_feedback_uses_authoritative_citation_aliases_and_gate_resolved_table_refs(citation, details):
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    report["markdown_body"] = "收入101亿元。" + citation
    feedback = _revision_feedback(report, {"findings": [{
        "code": "unsupported_numeric_claim", "line": 1, "details": details,
    }]}, sections)
    assert feedback["reference_navigation"]["references"][0]["evidence_id"] == "CE-FIN"


@pytest.mark.parametrize("source_change,payload_change,usable", [
    ({"future": True}, {}, False),
    ({"usable": False}, {}, False),
    ({}, {"status": "failed"}, False),
    ({"type": "context"}, {}, True),
])
def test_revision_feedback_preserves_rejected_source_state_and_context_scope(source_change, payload_change, usable):
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    source = next(row for row in sections["company_research_manifest"]["source_register"]
                  if row["evidence_id"] == "CE-FIN")
    source.update(source_change)
    sections["financials"].update(payload_change)
    report["markdown_body"] = "收入 101亿元。[evidence:CE-FIN]"
    review = {"findings": [{"code": "unsupported_numeric_claim", "line": 1}]}
    ref = _revision_feedback(report, review, sections)["reference_navigation"]["references"][0]
    assert ref["usable"] is usable
    if usable:
        assert ref["scope"] == "context"
    else:
        assert ref["fields"] == [] and ref["unusable_reason"]


def test_revision_feedback_groups_all_errors_on_one_line_and_preserves_selected_assumption_scope():
    from financial_analyst.agent.company_draft_review import _revision_feedback

    sections, report = _inputs()
    report["markdown_body"] = "事实100亿元。[evidence:CE-FIN] 情景假设20%。[assumption:SCENARIO]"
    report["assumptions"] = [{"assumption_id": "SCENARIO", "section_id": "scenarios_verdict", "value": "20%"}]
    report["summary_json"] = {"assumptions": [{"assumption_id": "WRONG-VERSION", "value": "100亿元"}]}
    review = {"findings": [
        {"code": "assumption_value_mismatch", "line": 1, "details": {"number": "100亿元"}},
        {"code": "numeric_claim_uncited", "line": 1, "details": {"number": "20%"}},
    ]}
    feedback = _revision_feedback(report, review, sections)
    assert len(feedback["line_repair_groups"]) == 1
    assert feedback["line_repair_groups"][0]["disputed_numbers"] == ["100亿元", "20%"]
    fields = feedback["selected_assumptions"]["fields"]
    field = next(row for row in fields if row["path"].endswith(".value"))
    assert field["value"] == "20%"
    assert field["context"] == {"assumption_id": "SCENARIO", "section_id": "scenarios_verdict"}
    assert "WRONG-VERSION" not in json.dumps(feedback)


def test_revision_feedback_excerpts_are_bounded_and_marked_truncated():
    from financial_analyst.agent.company_draft_review import _local_fields, _reference_feedback

    fields = _local_fields({"records": [{"value": index, "description": "x" * 1000}
                                        for index in range(5000)]}, "payload", {"123"})
    assert fields["truncated"] is True and len(fields["fields"]) <= 16
    assert len(json.dumps(fields, ensure_ascii=False)) < 3400
    sections, _ = _inputs()
    navigation = _reference_feedback(sections, {f"nonexistent-{index}": set() for index in range(30)})
    assert len(navigation["references"]) == 24 and navigation["omitted_references"] == 6
    assert all(ref["resolution"] == "unresolved" for ref in navigation["references"])


def test_bad_revision_and_network_failure_keep_the_original_incomplete_draft():
    sections, report = _inputs()
    bad = dict(report, markdown_body=report["markdown_body"].replace("100亿元", "101亿元"))
    for response in ("{", RuntimeError("do not expose credential-like exception body"), json.dumps(bad)):
        client = _Client(response)
        output, audit = asyncio.run(revise_company_draft_once(client, [], bad, sections))
        assert output == bad and audit["selected"] == "initial"
        assert len(client.calls) == 1
        assert "credential-like" not in json.dumps(audit)


def test_successful_draft_or_missing_verified_register_never_calls_model():
    sections, report = _inputs()
    for evidence in (sections, {}, {"company_research_manifest": {"source_register": []}}):
        client = _Client(AssertionError("unexpected retry"))
        output, audit = asyncio.run(revise_company_draft_once(client, [], report, evidence))
        assert output == report and not client.calls
        assert audit["revision_attempted"] is False


def test_risk_preflight_reuses_sanitizer_and_reports_context_only_counter(monkeypatch):
    from guanlan_v2.console import workspace_tools

    sections, report = _inputs()
    report["company_risk_input"] = {
        "forward_vs_ttm_judgement": {"status": "complete", "value": "fabricated"},
        "strong_claims": [{"kind": "strong_claim", "claim_id": "c-growth",
                           "claim": "公司收入能够持续增长。", "strong_claim": True,
                           "source_refs": ["CE-FIN"]}],
        "counter_evidence": [{"kind": "counter_evidence", "claim_id": "c-growth",
                              "relative_direction": "disconfirming",
                              "counter_evidence": "行业背景有所变化。",
                              "source_refs": [{"evidence_id": "CE-CONTEXT", "scope": "issuer"}]}],
        "decision_points": [],
    }
    real = workspace_tools._sanitise_company_risk_input
    received = []
    def capture(value, **kwargs):
        result = real(value, **kwargs)
        if kwargs.get("_root", True):
            received.append(result)
        return result
    monkeypatch.setattr(workspace_tools, "_sanitise_company_risk_input", capture)
    reviewed = inspect_company_draft(report, sections)
    assert received and "forward_vs_ttm_judgement" not in received[-1]
    assert received[-1]["counter_evidence"][0]["source_refs"] == ["CE-CONTEXT"]
    risk = reviewed["risk_review"]
    assert risk["status"] == "incomplete"
    rejected = risk["rejected_counter_evidence"]
    assert rejected[0]["reference_resolution"][0]["resolution"] == "context_only"
    assert any(row["check_id"] == "risk_contract" for row in reviewed["findings"])


def test_invalid_json_retry_and_semantic_revision_share_three_call_bound():
    from financial_analyst.agent.tier3.report_writer import _chat_json_object_with_one_retry

    sections, good = _inputs()
    bad = dict(good, markdown_body=good["markdown_body"].replace("100亿元", "101亿元"))
    class SequenceClient:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            return {"choices": [{"message": {"content": ["{", json.dumps(bad), "{"][self.calls - 1]}}]}
    client = SequenceClient()
    call_audit = {}
    first = asyncio.run(_chat_json_object_with_one_retry(client, [], attempt_audit=call_audit))
    selected, review = asyncio.run(revise_company_draft_once(client, [], first, sections))
    assert client.calls == 3 and call_audit["chat_calls"] == 2
    assert selected == bad and review["selected"] == "initial"


def test_revision_cannot_improve_count_by_dropping_the_risk_sidecar():
    sections, report = _inputs()
    bad = dict(report, markdown_body=report["markdown_body"].replace("100亿元", "101亿元"))
    missing = dict(report)
    missing.pop("company_risk_input")
    review = inspect_company_draft(missing, sections)
    assert any(row["code"] == "strong_claim_inventory_missing" for row in review["findings"])
    client = _Client(json.dumps(missing))
    selected, audit = asyncio.run(revise_company_draft_once(client, [], bad, sections))
    assert selected == bad and audit["selected"] == "initial"


@pytest.mark.parametrize("top", ["absent", None, [], [{
    "assumption_id": "top-scenario", "value": "12%", "unit": "%",
    "target_price": 99, "nested": {"action": "buy", "reason": "explicit scenario"},
}]])
def test_company_assumptions_preflight_and_saved_sidecar_use_the_same_selection(
        monkeypatch, tmp_path, top):
    from test_report_prompt_evidence import _run, _rw_response, rw_mod
    from guanlan_v2.research import company_report_gate

    fallback = [{"assumption_id": "summary-scenario", "value": "10%"}]
    response = _rw_response("公司研究证据仍需补充。")
    response["summary_json"] = {"assumptions": fallback}
    if top != "absent":
        response["assumptions"] = top
    expected = rw_mod._company_assumptions(response)
    if isinstance(top, list):
        assert expected == rw_mod._strip_company_summary_fields(top)
    else:
        assert expected == fallback

    sections, _ = _inputs()
    received = []
    real_gate = company_report_gate.validate_company_research_report
    def capture_gate(*args, **kwargs):
        received.append(kwargs.get("assumptions"))
        return real_gate(*args, **kwargs)
    monkeypatch.setattr(company_report_gate, "validate_company_research_report", capture_gate)
    _run(monkeypatch, rw_mod, rw_mod.ReportWriter, {
        "code": "SH603986", "asof_date": date.today().isoformat(),
        "out_dir": str(tmp_path), "evidence-loader": {"ok": True, "sections": sections},
    }, response)
    assert received and all(value == expected for value in received)
    sidecar = json.loads((tmp_path / f"SH603986_{date.today().isoformat()}.json").read_text(encoding="utf-8"))
    assert sidecar["assumptions"] == expected
    assert "target_price" not in json.dumps(sidecar["assumptions"])
