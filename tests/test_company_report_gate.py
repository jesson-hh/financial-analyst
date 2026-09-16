from __future__ import annotations

import copy

import pytest

from guanlan_v2.research.company_report_gate import (
    REPORT_GATE_PROMPT_CONTRACT,
    SECTION_ORDER,
    _numbers_in_text,
    _source_inventory,
    validate_company_research_report,
)


def _source(
    evidence_id: str, source_id: str, section: str, scope: str,
) -> dict:
    return {
        "source_id": source_id,
        "type": scope,
        "as_of": "2026-08-30",
        "period": "2026H1",
        "unit": None,
        "reliability": "official" if scope == "issuer" else "derived",
        "stale": False,
        "status": "ok",
        "evidence_id": evidence_id,
        "stages": ["report"],
        "source_ref": f"section:{section}",
        "record_count": 1,
        "authorities": [],
        "content_sha256": evidence_id.lower(),
        "warnings": [],
    }


def _evidence(status: str = "complete", gaps: list[dict] | None = None) -> dict:
    register = [
        _source("CE-PROFILE", "pack.company_facts", "company_facts", "issuer"),
        _source("CE-FIN", "financials_pit_loader", "financials", "issuer"),
        _source("CE-EST", "ths_eps_forecast", "expectations", "issuer"),
        _source("CE-VAL", "pack.valuation", "valuation", "issuer"),
        _source("CE-RISK", "cninfo_announcements", "announcements", "issuer"),
        _source("CE-CONTEXT", "pack.chain", "chain", "context"),
    ]
    stage_status = {
        "profile": status,
        "financials": status,
        "estimates": status,
        "valuation": status,
        "risk": status,
        "report": status,
    }
    hub = {
        "schema_version": "company-research-hub@1",
        "status": status,
        "as_of": "2026-08-30T00:00:00Z",
        "source_register": register,
        "evidence_scope": {
            "issuer": [row["evidence_id"] for row in register if row["type"] == "issuer"],
            "context": [row["evidence_id"] for row in register if row["type"] == "context"],
        },
        "stage_status": stage_status,
        "gaps": list(gaps or []),
        "conflicts": [],
    }
    return {
        "schema_version": "company-research-evidence-ledger@1",
        "hub": hub,
        "evidence_pack": {
            "schema_version": "company-research-evidence@2",
            "code": "SH603986",
            "generated_at": "2026-08-30",
            "sections": {
                "company_facts": {
                    "as_of": "2026-06-30", "business": "NOR Flash",
                },
                "financials": {
                    "as_of": "2026-06-30", "revenue": 10_000_000_000,
                    "net_profit": 1_000_000_000,
                },
                "expectations": {
                    "as_of": "2026-08-25", "year": 2027, "eps": 2.5,
                },
                "valuation": {"as_of": "2026-08-30", "pe_ttm": 20.0},
                "announcements": {
                    "as_of": "2026-08-28", "items": [{"title": "诉讼风险公告"}],
                },
                # This deliberately contains the same revenue number.  Tests can
                # prove scope separation independently from number matching.
                "chain": {
                    "as_of": "2026-08-20", "industry_revenue": 10_000_000_000,
                    "demand_growth": "15%",
                },
            },
        },
    }


def _calculations() -> list[dict]:
    return [{
        "calculation_id": "CAL-PE",
        "kind": "valuation_multiple",
        "formula": "price / eps",
        "inputs": ["CE-VAL"],
        "status": "complete",
        "result": 20.0,
    }]


def _risk_contract() -> dict:
    return {
        "schema_version": "company-risk-contract@1",
        "contract_id": "RISK-VALID",
        "status": "complete",
        "gate": {
            "passed": True,
            "counter_evidence": {
                "required_groups": 1,
                "strong_claims": 1,
                "complete_groups": 1,
                "passed": True,
            },
            "decision_points": {
                "required": 3,
                "observed": 3,
                "eligible_issuer": 3,
                "passed": True,
            },
        },
        "counter_evidence_groups": [{
            "claim_id": "claim-growth",
            "claim": "盈利修复可以延续。",
            "issuer_claim_source_refs": ["CE-FIN"],
            "counter_evidence": [{
                "counter_id": "counter-delay",
                "statement": "公告显示客户验收延后。",
                "relative_direction": "disconfirming",
                "issuer_source_refs": ["CE-RISK"],
                "freshness": "fresh",
            }],
            "freshness": "fresh",
            "status": "complete",
        }],
        "decision_points": [
            {
                "point_id": point_id,
                "question": "后续公开数据能否验证核心判断？",
                "metric": "公司披露指标",
                "event": None,
                "threshold": "达到公告阈值",
                "resolution_condition": None,
                "deadline": deadline,
                "status": "monitoring",
                "source_refs": ["CE-RISK"],
                "issuer_source_refs": ["CE-RISK"],
                "impact": "未达阈值则下调情景判断。",
                "scope": "issuer",
                "freshness": "fresh",
                "eligible": True,
                "ineligibility_reasons": [],
            }
            for point_id, deadline in (
                ("delivery-close", "2026-09-30"),
                ("margin-confirmation", "2026-10-31"),
                ("demand-confirmation", "2026-12-31"),
            )
        ],
        "gaps": [],
    }


def _report(*, status: str = "complete", sources_gaps: str | None = None) -> dict:
    gap_body = sources_gaps or (
        "证据登记见 [evidence:CE-PROFILE]；当前没有登记关键缺口。"
    )
    body = f"""<!-- company-research-status: {status} -->
# 兆易创新公司研究

本报告仅供研究和参考，不构成任何投资建议。本报告为非个性化投资建议，
未考虑任何个人的财务状况、风险承受能力或投资目标。

## [section:company_profile] 结论摘要

公司主营 NOR Flash，画像来自公司证据。[evidence:CE-PROFILE]

## [section:financials] 事实

公司收入为 100亿元。[evidence:CE-FIN]

## [section:expectations] 推断

市场一致预期为 2027 年 EPS 2.5元。[evidence:CE-EST]

## [section:valuation] 估值

确定性计算得到 PE 20倍。[calculation:CAL-PE]

## [section:risk_catalysts] 风险与反证

盈利延续结论及其公告反证见结构化风险合同。[claim:claim-growth]
公司诉讼风险来自公告。[evidence:CE-RISK]
行业背景需求增长 15%。[evidence:CE-CONTEXT]

## [section:scenarios_verdict] 裁决点

基准证据来自公司估值。[evidence:CE-VAL]
验收进度裁决见 [decision:delivery-close]。
利润率确认裁决见 [decision:margin-confirmation]。
需求确认裁决见 [decision:demand-confirmation]。
情景假设：收入增长 10%。[assumption:ASM-GROWTH]

## [section:sources_gaps] 数据缺口

{gap_body}
"""
    return {
        "markdown_body": body,
        "risk_contract": _risk_contract(),
        "assumptions": [{
            "assumption_id": "ASM-GROWTH",
            "section_id": "scenarios_verdict",
            "value": "10%",
        }],
    }


def _codes(result: dict, *, warnings: bool = False) -> set[str]:
    rows = result["warnings"] if warnings else result["violations"]
    return {row["code"] for row in rows}


def test_strict_complete_report_passes_and_is_deterministic():
    kwargs = {
        "evidence": _evidence(),
        "calculations": _calculations(),
        "upstream": "complete",
    }
    first = validate_company_research_report(_report(), **kwargs)
    second = validate_company_research_report(_report(), **kwargs)

    assert first == second
    assert first["status"] == "complete"
    assert first["completion_status"] == "complete"
    assert first["freshness_status"] == "fresh"
    assert first["violations"] == []
    assert first["warnings"] == []
    assert [row["section_id"] for row in first["audit"]["sections"]] == list(SECTION_ORDER)
    assert all(row["status"] == "passed" for row in first["checks"])
    assert "[section:company_profile]" in REPORT_GATE_PROMPT_CONTRACT


def test_missing_body_and_unknown_upstream_fail_closed_without_raising():
    result = validate_company_research_report(None)

    assert result["status"] == "incomplete"
    assert {"report_body_missing", "upstream_status_unknown"} <= _codes(result)
    assert next(row for row in result["checks"] if row["check_id"] == "sections")["status"] == "not_run"


def test_upstream_incomplete_never_passes_even_when_status_and_gap_are_disclosed():
    gap = {
        "gap_id": "G-VALUATION-DAG",
        "code": "valuation_calculation_dag_not_connected",
        "stage": "valuation",
        "critical": True,
        "message": "seven calculations are pending",
    }
    evidence = _evidence("incomplete", [gap])
    report = _report(
        status="incomplete",
        sources_gaps=(
            "估值计算仍未接通 [gap:G-VALUATION-DAG]；"
            "证据登记见 [evidence:CE-PROFILE]。"
        ),
    )

    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream=evidence["hub"],
    )

    assert result["status"] == "incomplete"
    assert "upstream_not_complete" in _codes(result)
    assert "required_gap_undisclosed" not in _codes(result)
    assert "false_completeness_claim" not in _codes(result)


def test_stale_upstream_preserves_complete_structure_but_returns_stale():
    result = validate_company_research_report(
        _report(status="stale"),
        evidence=_evidence("stale"), calculations=_calculations(), upstream="stale",
    )

    assert result["status"] == "stale"
    assert result["completion_status"] == "complete"
    assert result["freshness_status"] == "stale"
    assert result["violations"] == []
    assert "upstream_stale" in _codes(result, warnings=True)


def test_upstream_status_precedence_stale_wins_over_incomplete():
    upstream = {
        "schema_version": "company-research-final-upstream@1",
        "status": "incomplete",
        "stage_status": {
            "valuation": "stale",
            "risk": "incomplete",
            "report": "complete",
        },
    }
    result = validate_company_research_report(
        _report(status="stale"), evidence=_evidence(),
        calculations=_calculations(), upstream=upstream,
    )

    assert result["audit"]["upstream"]["status"] == "stale"
    assert result["status"] == "stale"
    assert result["completion_status"] == "complete"
    assert result["freshness_status"] == "stale"


def test_upstream_status_precedence_failed_wins_over_stale():
    upstream = {
        "schema_version": "company-research-final-upstream@1",
        "status": "stale",
        "stage_status": {
            "valuation": "stale",
            "risk": "failed",
            "report": "complete",
        },
    }
    result = validate_company_research_report(
        _report(status="failed"), evidence=_evidence(),
        calculations=_calculations(), upstream=upstream,
    )

    assert result["audit"]["upstream"]["status"] == "failed"
    assert result["status"] == "failed"
    assert result["completion_status"] == "failed"


def test_conservative_report_stale_marker_cannot_be_returned_as_complete():
    result = validate_company_research_report(
        _report(status="stale"),
        evidence=_evidence("complete"), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "stale"
    assert result["completion_status"] == "complete"
    assert result["freshness_status"] == "stale"
    assert "report_self_declared_stale" in _codes(result, warnings=True)


def test_optional_source_failure_does_not_override_authoritative_hub_status():
    evidence = _evidence("complete")
    evidence["evidence_pack"]["sections"]["chain"]["status"] = "failed"
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "行业背景需求增长 15%。[evidence:CE-CONTEXT]\n", "",
    )
    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream=None,
    )

    assert result["audit"]["upstream"]["status"] == "complete"
    assert result["status"] == "complete"


def test_non_strict_accepts_heading_aliases_but_strict_requires_markers():
    report = _report()
    for section_id in SECTION_ORDER:
        report["markdown_body"] = report["markdown_body"].replace(
            f"[section:{section_id}] ", "",
        )
    strict = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    migration = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(),
        upstream="complete", strict=False,
    )

    assert strict["status"] == "incomplete"
    assert "canonical_section_marker_missing" in _codes(strict)
    assert migration["status"] == "complete"
    assert "canonical_section_marker_missing" not in _codes(migration)


def test_nested_alias_heading_is_not_misclassified_as_top_level_section():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司收入为 100亿元。[evidence:CE-FIN]",
        "公司收入为 100亿元。[evidence:CE-FIN]\n\n### 估值方法\n本节不改变顶层顺序。",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "complete"
    assert [row["section_id"] for row in result["audit"]["sections"]] == list(SECTION_ORDER)


def test_strict_rejects_unexpected_level2_or_wrong_canonical_heading_level():
    extra = _report()
    extra["markdown_body"] = extra["markdown_body"].replace(
        "## [section:company_profile]", "## 摘要\n简述。\n\n## [section:company_profile]",
    )
    result = validate_company_research_report(
        extra, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert "unexpected_level2_section" in _codes(result)

    wrong_level = _report()
    wrong_level["markdown_body"] = wrong_level["markdown_body"].replace(
        "## [section:valuation]", "### [section:valuation]",
    )
    result = validate_company_research_report(
        wrong_level, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert "canonical_section_heading_level_invalid" in _codes(result)


def test_missing_duplicate_and_out_of_order_sections_are_blocking():
    report = _report()
    body = report["markdown_body"]
    start = body.index("## [section:expectations]")
    end = body.index("## [section:valuation]")
    expectations = body[start:end]
    report["markdown_body"] = body[:start] + body[end:] + expectations

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert result["status"] == "incomplete"
    assert "section_order_invalid" in _codes(result)

    duplicate = _report()
    duplicate["markdown_body"] += (
        "\n## [section:financials] 财务\n重复。[evidence:CE-FIN]\n"
    )
    result = validate_company_research_report(
        duplicate, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert {"duplicate_required_section", "section_order_invalid"} <= _codes(result)


def test_unsupported_number_is_rejected_but_registered_assumption_is_allowed():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace("100亿元", "999亿元")

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    unsupported = [
        row for row in result["violations"]
        if row["code"] == "unsupported_numeric_claim"
    ]
    assert result["status"] == "incomplete"
    assert unsupported and unsupported[0]["section_id"] == "financials"
    assumption_claim = next(
        row for row in result["audit"]["numeric_claims"] if row["number"] == "10%"
    )
    assert assumption_claim["supported"] is True
    assert assumption_claim["support_kind"] == "assumption"


def test_iso_date_next_to_chinese_does_not_split_into_numeric_claims():
    assert _numbers_in_text("截至2026-06-30总资产待核验。") == []
    assert _numbers_in_text("公告日2026年8月27日后复核。") == []


@pytest.mark.parametrize("period", ["2026-2028年", "2026至2028年", "2026年", "2026全年"])
def test_chinese_year_suffix_allows_adjacent_ascii_metric(period):
    assert [row["raw"] for row in _numbers_in_text(f"{period}EPS增速8.71%")] == ["8.71%"]


def test_year_range_does_not_hide_bare_numeric_ranges_or_invalid_period_ids():
    assert [row["raw"] for row in _numbers_in_text("金额2026-2028元")] == ["2026", "2028元"]
    assert [row["raw"] for row in _numbers_in_text("2026H1metric 为8.71%")] == ["8.71%"]


def test_explicit_cagr_year_interval_without_chinese_suffix_is_not_a_financial_quantity():
    assert [row["raw"] for row in _numbers_in_text("前瞻增速（2026-2028 CAGR 8.71%）")] == ["8.71%"]
    assert [row["raw"] for row in _numbers_in_text("2026 至 2028 cagr 为8.71%")] == ["8.71%"]


@pytest.mark.parametrize("text", [
    "金额2026-2028元", "PE 2026-2028倍", "2026-2028 CAGR_estimate",
    "2026-2028是公司收入区间", "2028-2026 CAGR 8.71%", "2026-2026 CAGR 8.71%",
])
def test_only_explicit_ascending_cagr_calendar_intervals_are_exempted(text):
    assert any(row["value"] in {"2026", "2028"} for row in _numbers_in_text(text))


def _pending_industry_gap(*, count=82, pending=82, source="research.newsradar.live"):
    return {
        "gap_id": "G-INDUSTRY-PENDING",
        "kind": "industry_news_review_pending",
        "source_id": source,
        "severity": "optional",
        "detail": (
            f"{count} industry candidates were fetched; {pending} remain unreviewed/uncertain, "
            "0 are unrelated, and 0 are user-confirmed related/report-eligible. "
            "Candidates remain industry context and never become issuer facts."
        ),
    }


def test_registered_gap_closes_only_explicit_all_pending_industry_count():
    gap = _pending_industry_gap()
    stage_gap = {
        "code": gap["kind"], "source_id": gap["source_id"], "stage": "risk",
        "message": gap["detail"], "severity": "optional",
    }
    result = validate_company_research_report(
        _report(sources_gaps=(
            "行业新闻候选82条均未审核，不能作为发行人事实[gap:G-INDUSTRY-PENDING]。"
        )),
        evidence=_evidence(gaps=[gap, stage_gap]),
        calculations=_calculations(), upstream="complete",
    )
    assert result["status"] == "complete"
    count = next(row for row in result["audit"]["numeric_claims"] if row["number"] == "82")
    assert count["support_kind"] == "gap_disclosure"
    assert count["supporting_refs"] == ["gap:g-industry-pending"]
    assert "optional_gap_undisclosed" not in _codes(result, warnings=True)


def test_registered_gap_closes_source_named_all_pending_industry_disclosure():
    # Production cr_6331787bd8aa4201 line 77: the precise registered count
    # was rejected only because the writer used this synonymous wording.
    result = validate_company_research_report(
        _report(sources_gaps=(
            "无可用数据：行业新闻雷达（research.newsradar.live）81条候选均未评审，"
            "无用户确认相关条目 [gap:G-INDUSTRY-PENDING]。"
        )),
        evidence=_evidence(gaps=[_pending_industry_gap(count=81, pending=81)]),
        calculations=_calculations(), upstream="complete",
    )
    assert result["status"] == "complete"
    count = next(row for row in result["audit"]["numeric_claims"] if row["number"] == "81")
    assert count["support_kind"] == "gap_disclosure"


def test_live_acceptance_source_named_pending_count_is_not_an_issuer_fact():
    # bg_52827c4ee7 line 167: same registered 82/all-pending/zero-confirmed
    # receipt, with the source name and explicit non-issuer limitation.
    body = (
        "无可用数据：行业新闻雷达（research.newsradar.live）82 条候选均未审核，"
        "不能作为发行人事实。 [gap:G-INDUSTRY-PENDING]"
    )
    result = validate_company_research_report(
        _report(sources_gaps=body), evidence=_evidence(gaps=[_pending_industry_gap()]),
        calculations=_calculations(), upstream="complete",
    )
    assert result["status"] == "complete"
    count = next(row for row in result["audit"]["numeric_claims"] if row["number"] == "82")
    assert count["support_kind"] == "gap_disclosure"
    assert count["supporting_refs"] == ["gap:g-industry-pending"]


def test_pipeline_projected_gap_preserves_exact_nested_gap_id_for_draft_preflight():
    original = _pending_industry_gap()
    projected = {
        "code": original["kind"], "input_key": original["source_id"], "stage": "profile",
        "message": original["detail"], "details": {"gap_id": original["gap_id"]},
    }
    body = (
        "无可用数据：行业新闻雷达（research.newsradar.live）82 条候选均未审核，"
        "不能作为发行人事实。 [gap:G-INDUSTRY-PENDING]"
    )
    args = {"evidence": _evidence(gaps=[projected]), "calculations": _calculations(), "upstream": "complete"}
    assert validate_company_research_report(_report(sources_gaps=body), **args)["status"] == "complete"
    projected["details"] = {"note": "G-INDUSTRY-PENDING"}  # prose is not an ID field
    result = validate_company_research_report(
        _report(sources_gaps=body), evidence=_evidence(gaps=[projected]),
        calculations=_calculations(), upstream="complete",
    )
    assert "numeric_claim_uncited" in _codes(result)


@pytest.mark.parametrize("tamper", [
    "wrong_count", "wrong_source", "unknown_ref", "partly_reviewed",
    "confirmed", "missing_review_counts", "issuer_suffix", "financial_amount",
])
def test_live_acceptance_count_form_keeps_exact_receipt_and_context_boundaries(tamper):
    gap = _pending_industry_gap()
    body = (
        "无可用数据：行业新闻雷达（research.newsradar.live）82 条候选均未审核，"
        "不能作为发行人事实。 [gap:G-INDUSTRY-PENDING]"
    )
    if tamper == "wrong_count":
        body = body.replace("82 条", "83 条")
    elif tamper == "wrong_source":
        gap["source_id"] = "other.newsradar"
    elif tamper == "unknown_ref":
        body = body.replace("G-INDUSTRY-PENDING", "G-UNKNOWN")
    elif tamper == "partly_reviewed":
        gap = _pending_industry_gap(pending=81)
    elif tamper == "confirmed":
        gap["detail"] = gap["detail"].replace("0 are user-confirmed", "1 are user-confirmed")
    elif tamper == "missing_review_counts":
        gap["detail"] = gap["detail"].split(", 0 are unrelated")[0] + ","
    elif tamper == "issuer_suffix":
        body += "公司订单已确认。"
    elif tamper == "financial_amount":
        body = body.replace("82 条", "82亿元 条")
    result = validate_company_research_report(
        _report(sources_gaps=body), evidence=_evidence(gaps=[gap]),
        calculations=_calculations(), upstream="complete",
    )
    assert "numeric_claim_uncited" in _codes(result)


def test_observed_baseline_must_not_be_laundered_into_same_line_assumption():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "情景假设：收入增长 10%。[assumption:ASM-GROWTH]",
        "情景假设：收入增长 10%。[assumption:ASM-GROWTH] "
        "当前基线：公司收入为 100亿元。[evidence:CE-FIN]",
    )
    args = {"evidence": _evidence(), "calculations": _calculations(), "upstream": "complete"}
    assert "assumption_value_mismatch" in _codes(validate_company_research_report(report, **args))
    report["markdown_body"] = report["markdown_body"].replace(
        "[assumption:ASM-GROWTH] 当前基线", "[assumption:ASM-GROWTH]\n当前基线",
    )
    assert validate_company_research_report(report, **args)["status"] == "complete"


@pytest.mark.parametrize("count, claim", [
    (1, "无可用数据：一致预期仅 1 家机构，可靠性偏低。"),
    (233, "无可用数据：历史估值序列有 233 天缺测。"),
])
def test_unrelated_gap_id_cannot_support_real_count_from_another_source(count, claim):
    # cr_25c77603985d4c92 lines 175/177: these counts may be real, but a
    # research_views/chain missing-data receipt is not their provenance.
    evidence = _evidence(gaps=[{
        "gap_id": "G-SOURCE-MISSING", "kind": "source_missing",
        "source_id": "pack.chain", "detail": "section chain has no data",
        "severity": "optional",
    }])
    evidence["evidence_pack"]["sections"]["expectations"]["coverage_count"] = count
    args = {"evidence": evidence, "calculations": _calculations(), "upstream": "complete"}
    result = validate_company_research_report(
        _report(sources_gaps=claim + " [gap:G-SOURCE-MISSING]"), **args,
    )
    assert any(
        row["code"] == "numeric_claim_uncited"
        and row["details"]["number"] == str(count)
        for row in result["violations"]
    )
    # The same exact value closes only when its actual source is cited.
    corrected = validate_company_research_report(
        _report(sources_gaps=claim + " [evidence:CE-EST] [gap:G-SOURCE-MISSING]"), **args,
    )
    assert corrected["status"] == "complete"


@pytest.mark.parametrize("amount", [219_750_000, 607_310_000, 719_430_000])
def test_signed_source_net_flow_does_not_authorise_absolute_value_rewrite(amount):
    # Production source fields are signed main_net values.  The report gate
    # must not apply abs() to all source quantities merely because prose says
    # outflow; the writer can retain the exact sign and name the net metric.
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["company_facts"]["moneyflow"] = [{
        "date": "2026-08-18", "unit": "CNY", "main_net": -amount,
    }]
    args = {"evidence": evidence, "calculations": _calculations(), "upstream": "complete"}
    report = _report()
    original = "公司收入为 100亿元。[evidence:CE-FIN]"
    report["markdown_body"] = report["markdown_body"].replace(
        original, f"公司主力净流出 {amount:,} 元。[evidence:CE-PROFILE]",
    )
    result = validate_company_research_report(report, **args)
    assert "unsupported_numeric_claim" in _codes(result)
    report["markdown_body"] = report["markdown_body"].replace(
        f"主力净流出 {amount:,} 元", f"主力净流入 {-amount:,} 元",
    )
    assert validate_company_research_report(report, **args)["status"] == "complete"


def test_short_slash_dates_stay_ambiguous_until_writer_uses_iso_date():
    # 8/18 can also be a fraction.  Do not solve the production shorthand-date
    # false positive by globally stripping every a/b numeric pair.
    assert {row["value"] for row in _numbers_in_text("比率 8/18")} == {"8", "18"}
    assert [row["raw"] for row in _numbers_in_text("2026-08-18主力净流入-607310000元")] == [
        "-607310000元",
    ]


@pytest.mark.parametrize("tamper", [
    "wrong_count", "wrong_source", "not_all_pending", "confirmed_rows",
    "unrelated_rows", "missing_confirmation_count", "issuer_assertion",
])
def test_source_named_industry_count_requires_same_source_and_exact_review_state(tamper):
    gap = _pending_industry_gap(count=81, pending=81)
    body = (
        "无可用数据：行业新闻雷达（research.newsradar.live）81条候选均未评审，"
        "无用户确认相关条目 [gap:G-INDUSTRY-PENDING]。"
    )
    if tamper == "wrong_count":
        body = body.replace("81条", "82条")
    elif tamper == "wrong_source":
        gap["source_id"] = "another.newsradar"
    elif tamper == "not_all_pending":
        gap = _pending_industry_gap(count=81, pending=80)
    elif tamper == "confirmed_rows":
        gap["detail"] = gap["detail"].replace("0 are user-confirmed", "1 are user-confirmed")
    elif tamper == "unrelated_rows":
        gap["detail"] = gap["detail"].replace("0 are unrelated", "1 are unrelated")
    elif tamper == "missing_confirmation_count":
        gap["detail"] = gap["detail"].split(", 0 are unrelated")[0] + ","
    elif tamper == "issuer_assertion":
        body += "公司订单已经证实。"
    result = validate_company_research_report(
        _report(sources_gaps=body), evidence=_evidence(gaps=[gap]),
        calculations=_calculations(), upstream="complete",
    )
    assert "numeric_claim_uncited" in _codes(result)


@pytest.mark.parametrize(
    ("disclosure", "pending"),
    [
        ("行业新闻候选83条均未审核，不能作为发行人事实[gap:G-INDUSTRY-PENDING]。", 82),
        ("行业新闻候选82条均未审核，不能作为发行人事实[gap:G-NOT-REGISTERED]。", 82),
        ("行业新闻候选82条均未审核，不能作为发行人事实[gap:G-INDUSTRY-PENDING]。", 80),
        ("公司营收82亿元未核实[gap:G-INDUSTRY-PENDING]。", 82),
        ("行业新闻候选82条已经证实公司订单[gap:G-INDUSTRY-PENDING]。", 82),
    ],
)
def test_gap_reference_does_not_close_wrong_count_unresolved_or_issuer_claim(disclosure, pending):
    result = validate_company_research_report(
        _report(sources_gaps=disclosure),
        evidence=_evidence(gaps=[_pending_industry_gap(pending=pending)]),
        calculations=_calculations(), upstream="complete",
    )
    assert "numeric_claim_uncited" in _codes(result)


def test_gap_number_support_cannot_escape_sources_gaps_or_become_evidence():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司收入为 100亿元。[evidence:CE-FIN]",
        "行业新闻候选82条均未审核，不能作为发行人事实[gap:G-INDUSTRY-PENDING]。\n"
        "公司收入82亿元。[evidence:G-INDUSTRY-PENDING]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(gaps=[_pending_industry_gap()]),
        calculations=_calculations(), upstream="complete",
    )
    assert {"numeric_claim_uncited", "citation_unresolved"} <= _codes(result)


@pytest.mark.parametrize("changed", ["source", "message"])
def test_gap_id_alias_disclosure_does_not_cover_different_limitation_with_same_code(changed):
    original = _pending_industry_gap()
    other = {
        "code": original["kind"], "source_id": original["source_id"], "stage": "risk",
        "message": original["detail"], "severity": "optional",
    }
    if changed == "source":
        other["source_id"] = "another.newsradar"
    else:
        other["message"] = _pending_industry_gap(count=83, pending=83)["detail"]
    result = validate_company_research_report(
        _report(sources_gaps="行业候选待用户审核[gap:G-INDUSTRY-PENDING]。"),
        evidence=_evidence(gaps=[original, other]), calculations=_calculations(), upstream="complete",
    )
    missing = [row for row in result["warnings"] if row["code"] == "optional_gap_undisclosed"]
    assert len(missing) == 1
    assert missing[0]["details"]["gap"]["source_id"] == other["source_id"]
    assert missing[0]["details"]["gap"]["message"] == other["message"]


@pytest.mark.parametrize("claim", ["-14.88%", "-3.55%"])
def test_derived_yoy_requires_a_registered_calculation_not_just_raw_amounts(claim):
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["financials"] = {
        "unit": "CNY",
        "rows": [
            {"period": "2026-06-30", "revenue_cum": 13144293318.84, "net_profit_parent_cum": 315535600.73},
            {"period": "2025-06-30", "revenue_cum": 15441911409.78, "net_profit_parent_cum": 327133974.13},
        ],
    }
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司收入为 100亿元。[evidence:CE-FIN]", f"公司同比{claim}。[evidence:CE-FIN]",
    )
    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    assert "unsupported_numeric_claim" in _codes(result)


def test_prompt_contract_requires_source_local_numbers_and_isolated_assumptions():
    assert "not blanket support" in REPORT_GATE_PROMPT_CONTRACT
    assert "one logical numeric claim per Markdown line" in REPORT_GATE_PROMPT_CONTRACT
    assert "prompt-facing local payload" in REPORT_GATE_PROMPT_CONTRACT
    assert "[assumption:ASSUMPTION_ID]" in REPORT_GATE_PROMPT_CONTRACT
    assert "Never attach an evidence citation merely to legitimise" in REPORT_GATE_PROMPT_CONTRACT


def test_unknown_or_missing_citation_is_blocking_and_audited():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[evidence:CE-FIN]", "[evidence:CE-UNKNOWN]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert {"citation_unresolved", "section_citation_missing", "numeric_claim_uncited"} <= _codes(result)
    unknown = next(row for row in result["audit"]["citations"] if row["ref"] == "CE-UNKNOWN")
    assert unknown["resolved"] is False


@pytest.mark.parametrize(
    ("status", "usable", "future", "expected_reason"),
    [
        ("future", False, True, "future_source"),
        ("failed", False, False, "status_not_closing"),
        ("ok", False, False, "source_marked_unusable"),
    ],
)
def test_unusable_source_cannot_close_citation_or_numeric_claim(
        status, usable, future, expected_reason):
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    source.update({"status": status, "usable": usable, "future": future})

    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert {"evidence_source_unusable", "section_citation_missing"} <= _codes(result)
    citation = next(
        row for row in result["audit"]["citations"] if row["ref"] == "CE-FIN"
    )
    assert citation["resolved"] is True
    assert citation["usable"] is False
    finding = next(
        row for row in result["violations"]
        if row["code"] == "evidence_source_unusable"
        and row["details"]["ref"] == "CE-FIN"
    )
    assert finding["details"]["status"] == status
    assert finding["details"]["reason"] == expected_reason
    financial_claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "financials"
    )
    assert financial_claim["supported"] is False


@pytest.mark.parametrize(
    ("alias_field", "status", "future", "citation_ref"),
    [
        ("source_id", "future", True, "financials_pit_loader"),
        ("source_ref", "failed", False, "section:financials"),
    ],
)
def test_unusable_production_source_without_evidence_id_cannot_close_alias(
        alias_field, status, future, citation_ref):
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    assert source[alias_field] == citation_ref
    source.update({
        "evidence_id": None,
        "status": status,
        "usable": False,
        "future": future,
    })
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[evidence:CE-FIN]", f"[source:{citation_ref}]",
    )

    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert {"evidence_source_unusable", "section_citation_missing"} <= _codes(result)
    citation = next(
        row for row in result["audit"]["citations"]
        if row["ref"] == citation_ref
    )
    assert citation["resolved"] is True
    assert citation["usable"] is False
    financial_claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "financials"
    )
    assert financial_claim["supported"] is False


@pytest.mark.parametrize(
    ("payload_patch", "expected_reason"),
    [
        ({"status": "ok", "usable": True, "future": True}, "payload_future_source"),
        ({"status": "failed", "usable": True}, "payload_status_not_closing"),
        ({"status": "ok", "usable": False}, "payload_marked_unusable"),
        ({"status": "ok", "usable": 0}, "payload_usable_not_boolean"),
        ({"status": "ok", "usable": "false"}, "payload_usable_not_boolean"),
    ],
)
def test_top_level_section_payload_vetoes_source_and_harvested_numbers(
        payload_patch, expected_reason):
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["financials"].update(payload_patch)

    record = _source_inventory(evidence, _calculations())["refs"]["ce-fin"]

    assert record["usable"] is False
    assert record["unusable_reason"] == expected_reason
    assert record["numbers"] == []
    assert record["exact_values"] == []
    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    assert {"evidence_source_unusable", "section_citation_missing"} <= _codes(result)
    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "financials"
    )
    assert claim["supported"] is False


@pytest.mark.parametrize(
    ("payload_patch", "expected_reason"),
    [
        ({"status": "ok", "usable": True, "future": True}, "payload_future_source"),
        ({"status": "failed", "usable": False}, "payload_status_not_closing"),
        ({"status": "ok", "usable": False}, "payload_marked_unusable"),
    ],
)
def test_top_level_snapshot_payload_vetoes_source_and_harvested_numbers(
        payload_patch, expected_reason):
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    dataset_id = "research.financials.test.live"
    source.update({"source_id": dataset_id, "source_ref": f"dataset:{dataset_id}"})
    evidence["workspace_snapshots"] = [{
        "snapshot": {
            "dataset_id": dataset_id,
            "rows": [{"revenue": 10_000_000_000}],
            **payload_patch,
        },
    }]

    record = _source_inventory(evidence, _calculations())["refs"]["ce-fin"]

    assert record["usable"] is False
    assert record["unusable_reason"] == expected_reason
    assert record["numbers"] == []
    assert record["exact_values"] == []


def test_nested_bad_provider_does_not_veto_eligible_aggregate_payload():
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["financials"].update({
        "status": "ok",
        "usable": True,
        "sources": [{"status": "failed", "usable": False, "future": True}],
    })

    record = _source_inventory(evidence, _calculations())["refs"]["ce-fin"]

    assert record["usable"] is True
    assert "10000000000" in record["exact_values"]
    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    assert result["status"] == "complete"


def test_nested_bad_provider_number_cannot_close_aggregate_citation():
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["valuation"].update({
        "status": "ok",
        "usable": True,
        "sources": [{
            "status": "failed",
            "usable": False,
            "value": 999,
        }],
    })

    record = _source_inventory(evidence, _calculations())["refs"]["ce-val"]

    assert record["usable"] is True
    assert "20" in record["exact_values"]
    assert "999" not in record["exact_values"]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "公司 PE 为 999倍。[evidence:CE-VAL]",
    )
    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "valuation"
    )
    assert claim["number"] == "999倍"
    assert claim["supported"] is False
    assert "unsupported_numeric_claim" in _codes(result)


@pytest.mark.parametrize("invalid_usable", [0, "false"])
def test_source_register_usable_must_be_json_boolean_or_absent(invalid_usable):
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    source["usable"] = invalid_usable

    record = _source_inventory(evidence, _calculations())["refs"]["ce-fin"]

    assert record["usable"] is False
    assert record["unusable_reason"] == "source_usable_not_boolean"
    assert record["numbers"] == []


def test_later_usable_duplicate_alias_cannot_wash_out_unusable_source():
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    source["usable"] = False
    later = copy.deepcopy(source)
    later.update({
        "evidence_id": "CE-FIN-LATER",
        "source_id": "financials_later",
        "usable": True,
    })
    evidence["hub"]["source_register"].append(later)

    record = _source_inventory(evidence, _calculations())["refs"]["section:financials"]

    assert record["kind"] == "evidence"
    assert record["scope"] == "issuer"
    assert record["usable"] is False
    assert record["unusable_reason"] == "alias_usability_conflict"
    assert record["numbers"] == []
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[evidence:CE-FIN]", "[source:section:financials]",
    )
    result = validate_company_research_report(
        report, evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    assert {"evidence_source_unusable", "section_citation_missing"} <= _codes(result)


def test_duplicate_alias_with_issuer_context_conflict_is_unknown_and_unusable():
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-FIN"
    )
    source["usable"] = True
    context = copy.deepcopy(source)
    context.update({
        "evidence_id": "CE-FIN-CONTEXT",
        "source_id": "financials_context",
        "type": "context",
    })
    evidence["hub"]["source_register"].append(context)

    record = _source_inventory(evidence, _calculations())["refs"]["section:financials"]

    assert record["kind"] == "evidence"
    assert record["scope"] == "unknown"
    assert record["usable"] is False
    assert record["unusable_reason"] == "alias_scope_conflict"
    assert record["numbers"] == []


def test_duplicate_evidence_id_cannot_union_numbers_from_different_payloads():
    evidence = _evidence()
    duplicate = _source(
        "CE-FIN", "financials_aliasing_valuation", "valuation", "issuer",
    )
    evidence["hub"]["source_register"].append(duplicate)

    record = _source_inventory(evidence, _calculations())["refs"]["ce-fin"]

    assert record["kind"] == "evidence"
    assert record["scope"] == "issuer"
    assert record["usable"] is False
    assert record["unusable_reason"] == "alias_record_conflict"
    assert record["numbers"] == []
    assert record["exact_values"] == []
    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    citation = next(
        row for row in result["audit"]["citations"] if row["ref"] == "CE-FIN"
    )
    assert citation["usable"] is False
    assert {"evidence_source_unusable", "section_citation_missing"} <= _codes(result)


def test_calculation_id_cannot_overwrite_existing_evidence_alias():
    evidence = _evidence()
    collision = _source(
        "CE-CAL-COLLISION", "CAL-PE", "calculation_collision", "issuer",
    )
    evidence["hub"]["source_register"].append(collision)

    record = _source_inventory(evidence, _calculations())["refs"]["cal-pe"]

    assert record["kind"] == "reference_conflict"
    assert record["scope"] == "unknown"
    assert record["usable"] is False
    assert record["unusable_reason"] == "alias_kind_conflict"
    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )
    assert "citation_kind_mismatch" in _codes(result)


def test_calculation_depending_on_unusable_source_cannot_close_numeric_claim():
    evidence = _evidence()
    source = next(
        row for row in evidence["hub"]["source_register"]
        if row["evidence_id"] == "CE-VAL"
    )
    source.update({"status": "future", "usable": False, "future": True})

    result = validate_company_research_report(
        _report(), evidence=evidence, calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "calculation_output_unusable" in _codes(result)
    valuation_claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "valuation"
    )
    assert valuation_claim["supported"] is False
    lineage = result["audit"]["inventory"]["calculation_lineage"][0]
    assert lineage["unresolved_input_refs"] == ["CE-VAL"]


def test_context_number_cannot_close_an_issuer_section():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司收入为 100亿元。[evidence:CE-FIN]",
        "公司收入为 100亿元。[evidence:CE-CONTEXT]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert {"issuer_evidence_missing", "context_used_as_issuer_evidence"} <= _codes(result)
    # The context payload intentionally contained the same number.  It still
    # cannot support an issuer-specific number.
    financial_claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "financials"
    )
    assert financial_claim["supported"] is False
    assert "unsupported_numeric_claim" in _codes(result)


def test_calculation_must_use_calculation_citation_kind():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[calculation:CAL-PE]", "[evidence:CAL-PE]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert "citation_kind_mismatch" in _codes(result)
    assert "section_citation_missing" in _codes(result)
    assert "numeric_claim_uncited" in _codes(result)


def test_out_of_order_calculation_lineage_resolves_all_supported_ref_shapes():
    calculations = [
        # Deliberately reverse topological order.  This node uses the
        # ``calculation_id`` object form inside ``inputs``.
        {
            "calculation_id": "CAL-FINAL",
            "inputs": [{"calculation_id": "CAL-MID"}],
            "status": "complete",
            "result": 30.0,
        },
        # ``input_refs`` supports both string and reference-object forms.
        {
            "calculation_id": "CAL-MID",
            "input_refs": [{"ref": "CAL-BASE"}],
            "status": "complete",
            "result": 25.0,
        },
        # Evidence dependencies can use both ``ref`` and ``evidence_id``.
        {
            "calculation_id": "CAL-BASE",
            "inputs": [{"ref": "CE-VAL"}, {"evidence_id": "CE-FIN"}],
            "status": "complete",
            "result": 20.0,
        },
    ]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性计算得到目标估值 30倍。[calculation:CAL-FINAL]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "complete"
    lineage = {
        row["calculation_id"]: row
        for row in result["audit"]["inventory"]["calculation_lineage"]
    }
    assert lineage["CAL-BASE"]["scope"] == "issuer_derived"
    assert lineage["CAL-MID"]["scope"] == "issuer_derived"
    assert lineage["CAL-FINAL"]["scope"] == "issuer_derived"
    assert all(not row["unresolved_input_refs"] for row in lineage.values())
    final_citation = next(
        row for row in result["audit"]["citations"] if row["ref"] == "CAL-FINAL"
    )
    assert final_citation["scope"] == "issuer_derived"


def test_raw_formula_dag_calc_id_and_depends_on_propagate_issuer_scope():
    calculations = {
        "schema_version": "company-valuation-dag@1",
        "nodes": [
            {
                "calc_id": "CAL-DAG-FINAL",
                "depends_on": ["CAL-DAG-BASE"],
                "status": "complete",
                "result": 30.0,
            },
            {
                "calc_id": "CAL-DAG-BASE",
                # Raw Vibe/valuation DAGs retain field-level pack paths rather
                # than opaque CE ids; the containing section owns the lineage.
                "input_refs": ["pack.sections.valuation.pe_ttm"],
                "status": "complete",
                "result": 20.0,
            },
        ],
    }
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性计算得到目标估值 30倍。[calculation:CAL-DAG-FINAL]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "complete"
    lineage = {
        row["calculation_id"]: row
        for row in result["audit"]["inventory"]["calculation_lineage"]
    }
    assert lineage["CAL-DAG-BASE"]["scope"] == "issuer_derived"
    assert lineage["CAL-DAG-FINAL"]["scope"] == "issuer_derived"
    assert lineage["CAL-DAG-BASE"]["unresolved_input_refs"] == []
    assert lineage["CAL-DAG-FINAL"]["input_refs"] == ["CAL-DAG-BASE"]


def test_unresolved_calculation_dependency_stays_derived_unknown():
    calculations = [{
        "calculation_id": "CAL-ORPHAN",
        "input_refs": ["CAL-MISSING"],
        "status": "complete",
        "result": 30.0,
    }]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性计算得到目标估值 30倍。[calculation:CAL-ORPHAN]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "incomplete"
    lineage = result["audit"]["inventory"]["calculation_lineage"]
    assert lineage == [{
        "calculation_id": "CAL-ORPHAN",
        "status": "complete",
        "usable": True,
        "payload_key": "result",
        "unusable_reason": None,
        "scope": "derived_unknown",
        "input_refs": ["CAL-MISSING"],
        "unresolved_input_refs": ["CAL-MISSING"],
    }]
    assert "issuer_evidence_missing" in _codes(result)
    assert "unsupported_numeric_claim" in _codes(result)


@pytest.mark.parametrize("status,payload_key,payload", [
    ("invalid", "result", None),
    ("incomplete", "result", 20.0),
    ("complete", "result", None),
    ("failed", "output", {"value": 20.0}),
])
def test_unusable_calculation_cannot_close_from_numeric_inputs(
        status, payload_key, payload):
    calculations = [{
        "calculation_id": "CAL-BAD",
        "status": status,
        # The cited number is deliberately present only in inputs.  Inputs are
        # lineage, never a calculation output.
        "inputs": [{"ref": "CE-VAL"}, {"literal": 20.0}],
        payload_key: payload,
    }]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性计算得到 PE 20倍。[calculation:CAL-BAD]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "calculation_output_unusable" in _codes(result)
    valuation_claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "valuation"
    )
    assert valuation_claim["supported"] is False


def test_complete_calculation_uses_only_result_not_same_number_in_inputs():
    calculations = [{
        "calculation_id": "CAL-OUTPUT-ONLY",
        "status": "complete",
        "inputs": [{"ref": "CE-VAL"}, {"literal": 20.0}],
        "result": 30.0,
    }]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[calculation:CAL-PE]", "[calculation:CAL-OUTPUT-ONLY]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "unsupported_numeric_claim" in _codes(result)


@pytest.mark.parametrize("status", ["complete", "stale"])
def test_complete_or_stale_calculation_with_valid_output_can_close(status):
    calculations = [{
        "calculation_id": "CAL-VALID-OUTPUT",
        "status": status,
        "inputs": [{"ref": "CE-VAL"}],
        "output": {"status": "ok", "value": 20.0, "unit": "times"},
    }]
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "[calculation:CAL-PE]", "[calculation:CAL-VALID-OUTPUT]",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert result["status"] == "complete"
    assert "calculation_output_unusable" not in _codes(result)


def test_report_requires_final_structured_risk_contract_not_narrative_guessing():
    report = _report()
    report.pop("risk_contract")

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "final_risk_contract_missing" in _codes(result)


def test_final_risk_contract_can_be_supplied_by_upstream_envelope():
    report = _report()
    risk_contract = report.pop("risk_contract")
    upstream = {"status": "complete", "risk_contract": risk_contract}

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream=upstream,
    )

    assert result["status"] == "complete"
    assert result["audit"]["risk_contract"]["location"] == "upstream.risk_contract"


def test_self_reported_risk_gate_pass_cannot_hide_empty_strong_claim():
    report = _report()
    report["risk_contract"]["counter_evidence_groups"][0]["claim"] = ""

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "strong_claim_counter_group_incomplete" in _codes(result)


def test_risk_section_must_cover_every_contract_strong_claim():
    report = _report()
    report["risk_contract"]["counter_evidence_groups"].append({
        "claim_id": "claim-moat",
        "claim": "竞争优势可以维持。",
        "issuer_claim_source_refs": ["CE-PROFILE"],
        "counter_evidence": [{
            "counter_id": "counter-moat",
            "statement": "公司披露显示竞品份额提升。",
            "relative_direction": "disconfirming",
            "issuer_source_refs": ["CE-RISK"],
            "freshness": "fresh",
        }],
        "freshness": "fresh",
        "status": "complete",
    })
    report["risk_contract"]["gate"]["counter_evidence"].update({
        "required_groups": 2, "strong_claims": 2, "complete_groups": 2,
    })

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "strong_claim_not_covered" in _codes(result)
    finding = next(
        row for row in result["violations"] if row["code"] == "strong_claim_not_covered"
    )
    assert finding["details"]["claim_id"] == "claim-moat"


def test_three_textual_decision_markers_cannot_replace_three_eligible_contract_points():
    report = _report()
    report["risk_contract"]["decision_points"] = report["risk_contract"][
        "decision_points"
    ][:2]
    # Leave the self-reported gate counters untouched on purpose.  The report
    # gate must recompute from the actual structured rows.

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "eligible_decision_points_insufficient" in _codes(result)
    assert "decision_point_reference_unknown" in _codes(result)


@pytest.mark.parametrize("forbidden", [
    "目标价见附表。",
    "建议买入并加仓。",
    "建议卖出并减仓。",
    "止损位见交易计划。",
    "The price target is shown in the appendix.",
    "We recommend buying and adding to the position.",
    "Sell and reduce the position.",
    "Use an implied price as the valuation conclusion.",
    "The price anchor is management guidance.",
    "Rating: BUY.",
    "A target-price is shown in the appendix.",
    "Use position sizing in the final conclusion.",
    "Set a take-profit in the trading plan.",
])
def test_final_gate_blocks_trading_direction_and_price_anchors_in_both_languages(
        forbidden):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司诉讼风险来自公告。[evidence:CE-RISK]",
        f"公司诉讼风险来自公告。[evidence:CE-RISK]\n{forbidden}",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "prohibited_trading_or_price_anchor" in _codes(result)


@pytest.mark.parametrize("allowed", [
    "估值只讨论倍数和历史分位，不映射为证券价格。",
    "报告比较乐观、中性与审慎情景，不给出交易动作。",
    "Valuation is discussed as a multiple without mapping it to a security price.",
    "股东减持公告属于公司事实，不是读者交易方向。",
    "Distributor sell-through improved during the quarter.",
    "本报告不提供买卖或仓位建议。",
])
def test_final_gate_does_not_false_positive_on_non_directional_research_language(
        allowed):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "公司诉讼风险来自公告。[evidence:CE-RISK]",
        f"公司诉讼风险来自公告。[evidence:CE-RISK]\n{allowed}",
    )

    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "complete"
    assert "prohibited_trading_or_price_anchor" not in _codes(result)


def test_required_gap_blocks_and_optional_gap_warns_when_undisclosed():
    gaps = [
        {
            "gap_id": "G-REQUIRED", "stage": "valuation", "severity": "required",
            "detail": "missing deterministic calculation",
        },
        {
            "gap_id": "G-OPTIONAL", "stage": "risk", "severity": "optional",
            "detail": "optional radar unavailable",
        },
    ]
    result = validate_company_research_report(
        _report(), evidence=_evidence("complete", gaps),
        calculations=_calculations(), upstream="complete",
    )

    assert "required_gap_undisclosed" in _codes(result)
    assert "optional_gap_undisclosed" in _codes(result, warnings=True)
    assert result["status"] == "incomplete"


def test_status_overstatement_and_false_completeness_claim_are_blocking():
    gap = {
        "gap_id": "G-RISK", "stage": "risk", "critical": True,
        "message": "risk verdict pending",
    }
    report = _report(
        status="complete",
        sources_gaps="全部数据源已经接通。[evidence:CE-PROFILE]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence("incomplete", [gap]),
        calculations=_calculations(), upstream="incomplete",
    )

    assert {"research_status_overstated", "false_completeness_claim", "required_gap_undisclosed"} <= _codes(result)
    assert result["status"] == "incomplete"


def test_disclaimer_and_personalised_direction_are_enforced():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "本报告仅供研究和参考，不构成任何投资建议。本报告为非个性化投资建议，\n"
        "未考虑任何个人的财务状况、风险承受能力或投资目标。",
        "根据你的仓位，建议你买入。",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert {
        "research_only_disclaimer_missing",
        "investment_advice_disclaimer_missing",
        "non_personalised_disclaimer_missing",
        "personalised_investment_direction",
    } <= _codes(result)
    assert result["status"] == "incomplete"


def test_hub_without_source_payload_cannot_support_report_numbers():
    bare_hub = copy.deepcopy(_evidence()["hub"])
    result = validate_company_research_report(
        _report(), evidence=bare_hub, calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "incomplete"
    assert "unsupported_numeric_claim" in _codes(result)
    assert result["audit"]["inventory"]["sources"] == 6


def test_unregistered_explicit_assumption_warns_but_does_not_fabricate_fact():
    report = _report()
    report.pop("assumptions")
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert result["status"] == "complete"
    assert "assumption_not_registered" in _codes(result, warnings=True)
    claim = next(
        row for row in result["audit"]["numeric_claims"] if row["number"] == "10%"
    )
    assert claim["support_kind"] == "assumption"


def test_slash_research_reference_purpose_satisfies_only_that_disclaimer():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "本报告仅供研究和参考，不构成任何投资建议。本报告为非个性化投资建议，\n"
        "未考虑任何个人的财务状况、风险承受能力或投资目标。",
        "本报告为研究/参考用途。",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    assert "research_only_disclaimer_missing" not in _codes(result)
    assert {
        "investment_advice_disclaimer_missing",
        "non_personalised_disclaimer_missing",
    } <= _codes(result)


def _cny_calculation(*, output_value=6_486_491_972.14, output_unit="CNY", input_value=10_000_000_000):
    return [{
        "calculation_id": "CAL-CNY",
        "input_refs": ["CE-FIN"],
        "inputs": [{
            "ref": "CE-FIN", "value": input_value, "unit": "CNY",
        }],
        "status": "complete",
        "output": {"status": "ok", "value": output_value, "unit": output_unit},
    }]


def test_ref_backed_calculation_inputs_and_cny_display_rounding_are_supported():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "2026Q2 确定性金额为 64.86亿元，基期为 100亿元。"
        "[calculation:CAL-CNY]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_cny_calculation(), upstream="complete",
    )

    assert result["status"] == "complete"
    assert "unsupported_numeric_claim" not in _codes(result)


@pytest.mark.parametrize("bad_claim", ["64.87亿元", "648.65万元"])
def test_cny_display_requires_exact_scale_and_claim_precision(bad_claim):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        f"2026Q2 确定性金额为 {bad_claim}。[calculation:CAL-CNY]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_cny_calculation(), upstream="complete",
    )

    assert "unsupported_numeric_claim" in _codes(result)


@pytest.mark.parametrize("unit", ["%", "percent", "percentage", "pct"])
def test_native_percentage_calculation_rounds_at_the_claim_precision(unit):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "PE历史分位为96.52%。[calculation:CAL-PE]",
    )
    calculations = _calculations()
    calculations[0]["result"] = {"unit": unit, "value": 96.51995905834187}
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )
    claim = next(row for row in result["audit"]["numeric_claims"] if row["number"] == "96.52%")
    assert claim["supported"] is True
    assert claim["supporting_refs"] == ["cal-pe"]


@pytest.mark.parametrize("actual,unit,status,claim", [
    (96.51995905834187, "%", "complete", "96.53%"),
    (0.9651995905834187, "%", "complete", "96.52%"),
    (96.51995905834187, "ratio", "complete", "96.52%"),
    (96.51995905834187, "CNY", "complete", "96.52%"),
    (96.51995905834187, "%", "failed", "96.52%"),
    (96.51995905834187, "%", "complete", "96.52bp"),
])
def test_native_percentage_rounding_keeps_scale_units_precision_and_status_boundaries(actual, unit, status, claim):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        f"PE历史分位为{claim}。[calculation:CAL-PE]",
    )
    calculations = _calculations()
    calculations[0].update(status=status, result={"unit": unit, "value": actual},
                           output={"display": claim})
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )
    audited = next(row for row in result["audit"]["numeric_claims"] if row["number"] == claim)
    assert audited["supported"] is False
    assert "unsupported_numeric_claim" in _codes(result)


def test_native_percentage_display_does_not_make_future_source_lineage_usable():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "PE历史分位为96.52%。[calculation:CAL-PE]",
    )
    evidence = _evidence()
    evidence["hub"]["source_register"][3].update(future=True, usable=False)
    calculations = _calculations()
    calculations[0]["result"] = {"unit": "%", "value": 96.51995905834187}
    result = validate_company_research_report(
        report, evidence=evidence, calculations=calculations, upstream="complete",
    )
    assert "calculation_output_unusable" in _codes(result)
    audited = next(row for row in result["audit"]["numeric_claims"] if row["number"] == "96.52%")
    assert audited["supported"] is False


def test_calculation_input_value_must_be_corroborated_by_resolved_dependency():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "伪造输入为 999亿元。[calculation:CAL-CNY]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(),
        calculations=_cny_calculation(output_value=30, output_unit="倍", input_value=99_900_000_000),
        upstream="complete",
    )

    assert "unsupported_numeric_claim" in _codes(result)


def test_wrong_unit_and_uncited_cny_amount_remain_fail_closed():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "2026Q2 确定性金额为 64.86亿元。[calculation:CAL-CNY]\n"
        "另一个金额为 64.86亿元。",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(),
        calculations=_cny_calculation(output_unit="shares"), upstream="complete",
    )

    assert {"unsupported_numeric_claim", "numeric_claim_uncited"} <= _codes(result)


def test_distinct_values_cannot_match_through_coarse_rounding():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性计算得到 PE 20.4倍。[calculation:CAL-PE]",
    )
    calculations = _calculations()
    calculations[0]["result"] = 20.1
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=calculations, upstream="complete",
    )

    assert "unsupported_numeric_claim" in _codes(result)


def _scalar_cny_calculation(*, unit="CNY", status="complete"):
    return [{
        "calculation_id": "CAL-SCALAR-CNY",
        "input_refs": ["CE-FIN"],
        "status": status,
        "result": 6_486_491_972.14,
        "unit": unit,
    }]


def test_complete_scalar_calculation_preserves_explicit_cny_output_unit():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性金额为64.86亿元。[calculation:CAL-SCALAR-CNY]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_scalar_cny_calculation(),
        upstream="complete",
    )

    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["number"] == "64.86亿元"
    )
    assert claim["supported"] is True
    assert claim["supporting_refs"] == ["cal-scalar-cny"]


@pytest.mark.parametrize(
    ("unit", "status"),
    [(None, "complete"), ("shares", "complete"), ("CNY", "failed")],
)
def test_scalar_cny_rounding_requires_explicit_unit_and_usable_status(unit, status):
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "确定性金额为64.86亿元。[calculation:CAL-SCALAR-CNY]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(),
        calculations=_scalar_cny_calculation(unit=unit, status=status),
        upstream="complete",
    )

    assert {
        "unsupported_numeric_claim", "numeric_claim_uncited",
        "calculation_output_unusable",
    }.intersection(_codes(result))
    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["number"] == "64.86亿元"
    )
    assert claim["supported"] is False


def _typed_share_evidence() -> dict:
    evidence = _evidence()
    evidence["evidence_pack"]["sections"]["financials"]["shares_outstanding"] = 2_091_542_178
    evidence["contracts"] = {
        "valuation_extended_dag": {
            "schema_version": "company-valuation-dag@1",
            "input_schema_version": "company-valuation-input@1",
            "status": "complete",
            "extraction": {
                "schema_version": "company-valuation-input-extraction@1",
                "status": "complete",
            },
            "input_register": [{
                "key": "shares_outstanding",
                "ref": "pack.sections.financials.shares_outstanding",
                "unit": "shares",
                "value": 2_091_542_178,
            }],
        },
    }
    return evidence


def _share_input_calculation() -> list[dict]:
    return [{
        "calculation_id": "CAL-SHARES",
        "input_refs": ["pack.sections.financials.shares_outstanding"],
        "status": "complete",
        "result": 36_016_356_305.16,
        "unit": "CNY",
    }]


def _share_claim_report() -> dict:
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "公司总股本20.92亿股。[calculation:CAL-SHARES]",
    )
    return report


def test_closed_typed_share_input_supports_rounded_yigu_claim():
    result = validate_company_research_report(
        _share_claim_report(), evidence=_typed_share_evidence(),
        calculations=_share_input_calculation(), upstream="complete",
    )

    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["number"] == "20.92亿股"
    )
    assert claim["supported"] is True
    assert claim["supporting_refs"] == ["cal-shares"]


@pytest.mark.parametrize("tamper", ["contract_status", "unit", "value"])
def test_share_rounding_rejects_unclosed_untyped_or_uncorroborated_input(tamper):
    evidence = _typed_share_evidence()
    contract = evidence["contracts"]["valuation_extended_dag"]
    if tamper == "contract_status":
        contract["status"] = "incomplete"
    elif tamper == "unit":
        contract["input_register"][0]["unit"] = "CNY"
    else:
        contract["input_register"][0]["value"] = 2_091_542_179

    result = validate_company_research_report(
        _share_claim_report(), evidence=evidence,
        calculations=_share_input_calculation(), upstream="complete",
    )

    claim = next(
        row for row in result["audit"]["numeric_claims"]
        if row["number"] == "20.92亿股"
    )
    assert claim["supported"] is False
    assert "unsupported_numeric_claim" in _codes(result)


def test_chinese_adjacent_decimals_are_audited_whole_not_as_decimal_tails():
    report = _report()
    report["markdown_body"] = report["markdown_body"].replace(
        "确定性计算得到 PE 20倍。[calculation:CAL-PE]",
        "总市值360.16亿元（现价17.22元×总股本20.92亿股）。"
        "[calculation:CAL-PE]",
    )
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )

    valuation_numbers = {
        row["number"] for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "valuation"
    }
    assert valuation_numbers == {"360.16亿元", "17.22元", "20.92亿股"}
    assert {"16亿元", "22元", "92亿股"}.isdisjoint(valuation_numbers)
    assert all(
        not row["supported"] for row in result["audit"]["numeric_claims"]
        if row["section_id"] == "valuation"
    )


@pytest.mark.parametrize(
    "text",
    [
        "identifier360.16亿元",
        "360.16suffix",
        "x.16亿元",
        "残缺千分位1,23万元",
    ],
)
def test_numeric_scanner_does_not_recover_identifier_or_malformed_tails(text):
    assert _numbers_in_text(text) == []


@pytest.mark.parametrize("marker", ["[calculation:calc-?]", "[evidence:CE-?]"])
def test_placeholder_reference_is_explicitly_unknown_even_without_numbers(marker):
    report = _report()
    report["markdown_body"] += "\n待核实的定性说明。" + marker + "\n"
    result = validate_company_research_report(
        report, evidence=_evidence(), calculations=_calculations(), upstream="complete",
    )
    assert "citation_unresolved" in _codes(result)
    assert result["status"] != "complete"
