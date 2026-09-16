from __future__ import annotations

import copy
import hashlib
import json

from guanlan_v2.research.company_risk_gate import (
    SCHEMA_VERSION,
    build_company_risk_contract,
    risk_stage_evidence_from_contract,
)


AS_OF = "2026-08-30"


def _point(index: int, *, ref: str = "EV-ISSUER", deadline: str = "2026-10-30") -> dict:
    return {
        "kind": "decision_point",
        "id": f"dp-{index}",
        "evidence_id": f"EV-DP-{index}",
        "stale": False,
        "question": f"公开数据能否验证核心判断 {index}？",
        "metric": f"metric_{index}",
        "threshold": f"threshold_{index}",
        "deadline": deadline,
        "status": "scheduled",
        "source_refs": [ref],
        "impact": f"触发后将结论 {index} 降级为待重估情景。",
    }


def _complete_issuer_evidence() -> list[dict]:
    rows = [
        {
            "kind": "strong_claim",
            "claim_id": "claim-growth",
            "evidence_id": "EV-ISSUER",
            "stale": False,
            "claim": "盈利修复可以延续。",
            "direction": "positive",
        },
        {
            "kind": "counter_evidence",
            "claim_id": "claim-growth",
            "evidence_id": "EV-COUNTER",
            "stale": False,
            "relative_direction": "disconfirming",
            "counter_evidence": "最新单季扣非增速低于此前修复路径。",
            "risk_type": "earnings_quality",
            "priority": "high",
            "impact": "若持续则盈利修复结论降级。",
        },
        {
            "function": "forward_vs_ttm_judgement",
            "evidence_id": "EV-FWD",
            "stale": False,
            "output": {"judgement": "forward_above"},
        },
        {
            "function": "consensus_dispersion",
            "evidence_id": "EV-DISP",
            "stale": False,
            "output": {"min": 1.0, "mean": 1.3, "max": 1.8},
        },
        {
            "kind": "risk",
            "risk_id": "risk-customer",
            "evidence_id": "EV-RISK",
            "stale": False,
            "risk_type": "customer_concentration",
            "risk": "主要客户集中度仍高。",
            "priority": "medium",
            "impact": "客户资本开支变化会放大业绩波动。",
        },
        _point(1),
        _point(2),
        _point(3),
        {
            "kind": "catalyst",
            "catalyst_id": "cat-report",
            "evidence_id": "EV-CAT",
            "stale": False,
            "catalyst_type": "兑现型",
            "event": "下一份定期报告披露。",
            "verification_data": "单季营收与扣非净利润",
            "endpoint": "fetch_financials",
            "deadline": "2026-10-30",
            "impact_if_realized": "验证盈利修复情景。",
            "impact_if_missed": "盈利修复情景降级。",
        },
    ]
    for row in rows:
        row.setdefault("status", "ok")
    return rows


def test_complete_contract_has_true_counter_three_points_ranked_risks_and_catalyst():
    result = build_company_risk_contract(
        issuer_evidence=_complete_issuer_evidence(), as_of=AS_OF,
        conflicts=[], events=[],
    )

    assert result["schema_version"] == SCHEMA_VERSION
    assert result["status"] == "complete"
    assert result["gate"]["passed"] is True
    assert result["gate"]["context_can_close_issuer_gate"] is False
    assert result["gate"]["counter_evidence"]["complete_groups"] == 1
    assert result["gate"]["decision_points"]["eligible_issuer"] == 3
    assert len(result["counter_evidence_groups"][0]["counter_evidence"]) == 1
    assert result["counter_evidence_groups"][0]["downgraded_to_scenario"] is True
    assert result["catalysts"][0]["type"] == "realization"
    assert result["catalysts"][0]["status"] == "complete"
    assert [row["rank"] for row in result["risk_ranking"]] == list(
        range(1, len(result["risk_ranking"]) + 1)
    )
    assert all("synthetic score" in row["rank_basis"] for row in result["risk_ranking"])


def test_same_direction_risk_is_not_accepted_as_counter_evidence():
    evidence = _complete_issuer_evidence()
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    counter["kind"] = "risk"
    counter["relative_direction"] = "confirming"

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["gate"]["counter_evidence"]["passed"] is False
    assert result["counter_evidence_groups"][0]["counter_evidence"] == []
    assert "not_explicitly_disconfirming" in result["rejected_counter_evidence"][0]["reasons"]
    assert any(row["code"] == "true_counter_group_missing" for row in result["gaps"])


def test_same_issuer_source_can_support_distinct_claim_and_counter_facts():
    evidence = _complete_issuer_evidence()
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    counter.pop("evidence_id")
    counter["source_refs"] = ["EV-ISSUER"]

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert result["gate"]["counter_evidence"]["passed"] is True
    group = result["counter_evidence_groups"][0]
    assert group["claim_source_refs"] == ["EV-ISSUER"]
    assert group["counter_evidence"][0]["source_refs"] == ["EV-ISSUER"]


def test_literal_dual_role_record_is_not_accepted_as_its_own_counter():
    evidence = [
        row for row in _complete_issuer_evidence()
        if row.get("kind") not in {"strong_claim", "counter_evidence"}
    ]
    evidence.append({
        "kind": "counter_evidence",
        "strong_claim": True,
        "claim_id": "claim-growth",
        "evidence_id": "EV-DUAL",
        "stale": False,
        "claim": "盈利修复可以延续。",
        "relative_direction": "disconfirming",
        "counter_evidence": "最新单季扣非增速低于此前修复路径。",
    })

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["gate"]["counter_evidence"]["passed"] is False
    assert result["counter_evidence_groups"][0]["counter_evidence"] == []
    assert "counter_not_independent_record" in (
        result["rejected_counter_evidence"][0]["reasons"]
    )


def test_context_counter_cannot_close_issuer_claim_gate():
    evidence = _complete_issuer_evidence()
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    evidence.remove(counter)

    result = build_company_risk_contract(
        issuer_evidence=evidence,
        context_evidence=[counter],
        as_of=AS_OF,
        conflicts=[],
        events=[],
    )

    assert result["status"] == "incomplete"
    assert result["gate"]["counter_evidence"]["complete_groups"] == 0
    rejected = result["rejected_counter_evidence"]
    assert rejected and rejected[0]["scope"] == "context"
    assert "issuer_source_ref_missing" in rejected[0]["reasons"]


def test_context_decision_points_do_not_satisfy_three_issuer_verdicts():
    issuer = [
        row for row in _complete_issuer_evidence()
        if row.get("kind") != "decision_point"
    ]
    context = [_point(1), _point(2), _point(3)]
    for row in context:
        row["source_refs"] = [row["evidence_id"]]

    result = build_company_risk_contract(
        issuer_evidence=issuer, context_evidence=context,
        as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["gate"]["decision_points"]["observed"] == 3
    assert result["gate"]["decision_points"]["eligible_issuer"] == 0
    assert all(row["scope"] == "context" for row in result["decision_points"])


def test_decision_point_requires_every_contract_field():
    evidence = _complete_issuer_evidence()
    bad = next(row for row in evidence if row.get("id") == "dp-3")
    bad.pop("deadline")
    bad.pop("impact")

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    point = next(row for row in result["decision_points"] if row["point_id"] == "dp-3")
    assert point["eligible"] is False
    assert set(point["ineligibility_reasons"]) >= {
        "deadline_missing_or_not_iso_date", "impact_missing",
    }
    assert result["gate"]["decision_points"]["eligible_issuer"] == 2


def test_every_detected_strong_claim_needs_its_own_true_counter():
    evidence = _complete_issuer_evidence()
    evidence.append({
        "kind": "strong_claim",
        "claim_id": "claim-moat",
        "evidence_id": "EV-MOAT",
        "stale": False,
        "claim": "技术壁垒不可替代。",
    })

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["gate"]["counter_evidence"]["strong_claims"] == 2
    assert result["gate"]["counter_evidence"]["complete_groups"] == 1
    moat = next(row for row in result["counter_evidence_groups"] if row["claim_id"] == "claim-moat")
    assert moat["status"] == "incomplete"


def test_structurally_complete_but_stale_closing_evidence_returns_stale():
    evidence = _complete_issuer_evidence()
    for row in evidence:
        row["stale"] = True

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "stale"
    assert result["gate"]["passed"] is False
    stage = risk_stage_evidence_from_contract(result)
    assert stage["stale"] is True
    assert stage["evidence"][0]["available"] is True
    assert stage["evidence"][0]["stale"] is True


def test_overdue_open_decision_point_makes_complete_structure_stale():
    evidence = _complete_issuer_evidence()
    next(row for row in evidence if row.get("id") == "dp-1")["deadline"] = "2026-08-01"

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "stale"
    point = next(row for row in result["decision_points"] if row["point_id"] == "dp-1")
    assert point["overdue"] is True
    assert point["status"] == "overdue"


def test_empty_data_fails_and_never_means_no_risk():
    result = build_company_risk_contract(as_of=AS_OF)

    assert result["status"] == "failed"
    assert result["gate"]["passed"] is False
    assert result["risk_ranking"]
    assert any("not evidence that risk is absent" in row["statement"] for row in result["risk_ranking"])
    assert result["unavailable_gaps"]
    stage = risk_stage_evidence_from_contract(result)
    assert "failure" in stage


def test_single_failed_issuer_source_is_failed_not_no_risk():
    hub = {
        "status": "failed",
        "as_of": AS_OF,
        "source_register": [{
            "source_id": "pack.announcements",
            "source_ref": "section:announcements",
            "type": "issuer",
            "status": "failed",
            "stale": None,
            "evidence_id": None,
        }],
        "gaps": [{
            "kind": "source_failed", "severity": "required",
            "source_id": "pack.announcements", "detail": "timeout",
        }],
        "conflicts": [],
    }

    result = build_company_risk_contract(hub=hub, as_of=AS_OF, events=[], conflicts=[])

    assert result["status"] == "failed"
    assert result["summary"]["usable_issuer_sources"] == 0
    assert any(row["category"] == "data_availability" for row in result["risk_ranking"])
    assert any("absence is not evidence of no risk" in row["statement"] for row in result["risk_ranking"])


def test_source_must_have_explicit_good_status_and_not_be_marked_unusable():
    for status, usable in ((None, True), ("future", True), ("pending", True), ("ok", False)):
        evidence = _complete_issuer_evidence()
        for row in evidence:
            if status is None:
                row.pop("status", None)
            else:
                row["status"] = status
            row["usable"] = usable

        result = build_company_risk_contract(
            issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
        )

        assert result["status"] != "complete", (status, usable)
        assert result["summary"]["usable_issuer_sources"] == 0, (status, usable)
        assert result["gate"]["counter_evidence"]["passed"] is False
        assert result["gate"]["decision_points"]["eligible_issuer"] == 0


def test_failed_optional_issuer_feed_stays_visible_without_overriding_complete_contract():
    hub = {
        "status": "complete",
        "as_of": AS_OF,
        "source_register": [{
            "source_id": "fetch_kline", "source_ref": "dataset:fetch_kline",
            "type": "issuer", "status": "failed", "stale": None,
            "evidence_id": None,
        }],
        "gaps": [{
            "kind": "source_failed", "severity": "optional",
            "source_id": "fetch_kline", "detail": "optional K-line timeout",
        }],
        "conflicts": [],
    }

    result = build_company_risk_contract(
        hub=hub, issuer_evidence=_complete_issuer_evidence(),
        as_of=AS_OF, events=[], conflicts=[],
    )

    assert result["status"] == "complete"
    assert any(
        row["category"] == "data_availability"
        and "absence is not evidence of no risk" in row["statement"]
        and row["scope"] == "issuer"
        for row in result["risk_ranking"]
    )
    assert any(
        row["code"] == "issuer_source_unavailable" and row["severity"] == "optional"
        for row in result["gaps"]
    )


def test_unstructured_legacy_risk_text_is_conservatively_ignored():
    pack = {
        "schema_version": "company-research-evidence@2",
        "generated_at": AS_OF,
        "sections": {"risk": {"content": "风险不大，继续观察后续进展。"}},
    }

    result = build_company_risk_contract(pack, as_of=AS_OF, events=[], conflicts=[])

    assert result["status"] != "complete"
    assert result["counter_evidence_groups"] == []
    assert result["decision_points"] == []
    assert any(row["code"] == "legacy_risk_text_unstructured" for row in result["gaps"])
    assert all("风险不大" not in row["statement"] for row in result["risk_ranking"])


def test_required_checks_are_not_satisfied_by_context_calculations():
    issuer = [
        row for row in _complete_issuer_evidence()
        if row.get("function") not in {"forward_vs_ttm_judgement", "consensus_dispersion"}
    ]
    context = [
        {
            "function": "forward_vs_ttm_judgement", "evidence_id": "CTX-FWD",
            "stale": False, "output": {"judgement": "forward_above"},
        },
        {
            "function": "consensus_dispersion", "evidence_id": "CTX-DISP",
            "stale": False, "output": {"min": 1.0, "max": 2.0},
        },
    ]

    result = build_company_risk_contract(
        issuer_evidence=issuer, context_evidence=context,
        as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["checks"]["forward_vs_ttm_judgement"]["status"] == "incomplete"
    assert result["checks"]["consensus_dispersion"]["status"] == "incomplete"


def test_omitted_conflict_register_is_not_silently_read_as_no_conflicts():
    result = build_company_risk_contract(
        issuer_evidence=_complete_issuer_evidence(), as_of=AS_OF, events=[],
    )

    assert result["status"] == "incomplete"
    assert result["conflicts"] == []
    assert any(row["code"] == "conflict_register_unavailable" for row in result["gaps"])
    assert any(
        row["category"] == "data_availability"
        and "omission is not proof" in row["statement"]
        for row in result["risk_ranking"]
    )


def test_unresolved_required_conflict_is_listed_ranked_and_blocks_gate():
    conflict = {
        "kind": "conflicting_snapshots",
        "severity": "required",
        "status": "open",
        "source_ids": ["EV-ISSUER", "EV-COUNTER"],
        "detail": "同一报告期归母净利润两源不一致。",
    }
    result = build_company_risk_contract(
        issuer_evidence=_complete_issuer_evidence(), as_of=AS_OF,
        conflicts=[conflict], events=[],
    )

    assert result["status"] == "incomplete"
    assert len(result["conflicts"]) == 1
    risk = next(row for row in result["risk_ranking"] if row["category"] == "data_source_conflict")
    assert risk["priority"] == "high"
    assert risk["scope"] == "issuer"
    assert any(row["code"] == "required_conflict_unresolved" for row in result["gaps"])


def test_invalid_catalyst_is_retained_with_optional_gap_not_fabricated():
    evidence = _complete_issuer_evidence()
    catalyst = next(row for row in evidence if row.get("kind") == "catalyst")
    catalyst.pop("deadline")
    catalyst.pop("impact_if_missed")

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert result["catalysts"][0]["status"] == "incomplete"
    assert set(result["catalysts"][0]["gaps"]) >= {
        "deadline_missing_or_not_iso_date", "impact_if_missed_missing",
    }
    gap = next(row for row in result["gaps"] if row["code"] == "catalyst_incomplete")
    assert gap["severity"] == "optional"


def test_evidence_pack_and_hub_are_directly_consumable():
    pack = {
        "schema_version": "company-research-evidence@2",
        "generated_at": AS_OF,
        "sections": {
            "valuation": {
                "risk_contract": {
                    "strong_claims": [{
                        "claim_id": "claim-growth", "claim": "盈利修复可以延续。",
                        "strong_claim": True,
                    }],
                    "forward_vs_ttm_judgement": {"judgement": "forward_above"},
                },
            },
            "expectations": {
                "consensus_dispersion": {"min": 1.0, "mean": 1.3, "max": 1.8},
            },
            "announcements": {
                "risk_contract": {
                    "counter_evidence": [{
                        "claim_id": "claim-growth",
                        "relative_direction": "disconfirming",
                        "counter_evidence": "最新公告显示客户验收延后。",
                        "priority": "high",
                    }],
                    "decision_points": [
                        {**_point(1, ref="EV-ANN"), "evidence_id": None},
                        {**_point(2, ref="EV-ANN"), "evidence_id": None},
                        {**_point(3, ref="EV-ANN"), "evidence_id": None},
                    ],
                },
            },
        },
    }
    hub = {
        "status": "complete",
        "as_of": AS_OF,
        "source_register": [
            {"source_id": "pack.valuation", "source_ref": "section:valuation",
             "type": "issuer", "status": "ok", "stale": False, "evidence_id": "EV-VAL"},
            {"source_id": "pack.expectations", "source_ref": "section:expectations",
             "type": "issuer", "status": "ok", "stale": False, "evidence_id": "EV-EST"},
            {"source_id": "pack.announcements", "source_ref": "section:announcements",
             "type": "issuer", "status": "ok", "stale": False, "evidence_id": "EV-ANN"},
        ],
        "gaps": [],
        "conflicts": [],
    }

    result = build_company_risk_contract(pack, hub, as_of=AS_OF, events=[], conflicts=[])

    assert result["status"] == "complete"
    assert result["gate"]["counter_evidence"]["passed"] is True
    assert result["gate"]["decision_points"]["eligible_issuer"] == 3
    assert result["checks"]["forward_vs_ttm_judgement"]["status"] == "complete"
    assert result["checks"]["consensus_dispersion"]["status"] == "complete"


def test_event_decision_point_can_resolve_issuer_source_alias_from_hub():
    evidence = [
        row for row in _complete_issuer_evidence()
        if row.get("kind") != "decision_point"
    ]
    hub = {
        "status": "complete",
        "as_of": AS_OF,
        "source_register": [{
            "source_id": "cninfo_announcements",
            "source_ref": "section:announcements",
            "type": "issuer", "status": "ok", "stale": False,
            "evidence_id": "EV-ANN",
        }],
        "gaps": [], "conflicts": [],
    }
    event_points = []
    for index in range(1, 4):
        row = _point(index, ref="pack.announcements")
        row["source_id"] = "pack.announcements"
        row["scope"] = "context"  # source alias, not this caller hint, owns the gate.
        event_points.append(row)

    result = build_company_risk_contract(
        hub=hub, issuer_evidence=evidence, events=event_points,
        conflicts=[], as_of=AS_OF,
    )

    assert result["gate"]["decision_points"]["eligible_issuer"] == 3
    assert all("pack.announcements" in row["issuer_source_refs"] for row in result["decision_points"])


def test_output_is_deterministic_and_inputs_are_not_mutated():
    evidence = _complete_issuer_evidence()
    original = copy.deepcopy(evidence)

    first = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )
    second = build_company_risk_contract(
        issuer_evidence=list(reversed(evidence)), as_of=AS_OF, conflicts=[], events=[],
    )

    assert evidence == original
    assert first == second


def test_invalid_contract_inputs_fail_closed():
    result = build_company_risk_contract(
        evidence_pack="legacy prose", hub=["not", "a", "hub"],
        issuer_evidence="risk is low", events="soon", conflicts="none",
        as_of=AS_OF,
    )

    assert result["status"] == "failed"
    assert result["gate"]["passed"] is False
    assert result["risk_ranking"]
    assert any(row["code"] == "invalid_evidence_pack" for row in result["gaps"])
    assert any(row["code"] == "invalid_evidence_hub" for row in result["gaps"])


def test_explicit_chinese_contract_labels_are_supported_without_prose_inference():
    evidence = _complete_issuer_evidence()
    claim = next(row for row in evidence if row.get("kind") == "strong_claim")
    claim["kind"] = "强结论"
    claim.pop("strong_claim", None)
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    counter["kind"] = "反证"
    counter["relative_direction"] = "反证"
    counter["priority"] = "高"
    for point in (row for row in evidence if row.get("kind") == "decision_point"):
        point["kind"] = "裁决点"
        point["status"] = "待验证"

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert result["gate"]["counter_evidence"]["passed"] is True
    assert result["gate"]["decision_points"]["eligible_issuer"] == 3
    assert result["counter_evidence_groups"][0]["counter_evidence"][0][
        "relative_direction"
    ] == "disconfirming"


def test_empty_primary_ref_field_does_not_discard_other_exact_reference_fields():
    evidence = _complete_issuer_evidence()
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    counter.pop("evidence_id")
    counter["source_refs"] = []
    counter["evidence_refs"] = [{"evidence_id": "EV-ISSUER"}]
    counter["inputs_refs"] = ["EV-ISSUER"]

    result = build_company_risk_contract(
        issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    resolved_counter = result["counter_evidence_groups"][0]["counter_evidence"][0]
    assert resolved_counter["source_refs"] == ["EV-ISSUER"]
    assert resolved_counter["reference_resolution"][0]["resolution"] == "issuer"


def test_sidecar_semantic_ids_do_not_become_sources_or_availability_failures():
    evidence = _complete_issuer_evidence()
    hub = {
        "status": "complete", "as_of": AS_OF, "gaps": [], "conflicts": [],
        "source_register": [{
            "source_id": "pack.financials", "source_ref": "section:financials",
            "type": "issuer", "status": "ok", "stale": False,
            "evidence_id": "EV-FIN",
        }],
    }
    sidecar = {"decision_points": []}
    for index in range(1, 4):
        point = _point(index, ref="EV-FIN")
        point.pop("evidence_id")
        point.pop("stale")
        sidecar["decision_points"].append(point)
    evidence = [row for row in evidence if row.get("kind") != "decision_point"]

    result = build_company_risk_contract(
        {"risk_contract": sidecar}, hub, issuer_evidence=evidence,
        as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert all(row["source_refs"] == ["EV-FIN"] for row in result["decision_points"])
    assert not any(
        ref.startswith("dp-")
        for gap in result["gaps"] for ref in gap["source_refs"]
    )


def test_rejected_context_and_unknown_counter_refs_have_actionable_diagnostics():
    evidence = _complete_issuer_evidence()
    counter = next(row for row in evidence if row.get("kind") == "counter_evidence")
    counter.pop("evidence_id")
    counter["source_refs"] = ["CE-QUANT", "CE-NOT-REGISTERED"]
    hub = {
        "status": "complete", "as_of": AS_OF, "gaps": [], "conflicts": [],
        "source_register": [{
            "source_id": "pack.quant", "source_ref": "section:quant",
            "type": "context", "status": "ok", "stale": False,
            "evidence_id": "CE-QUANT",
        }],
    }

    result = build_company_risk_contract(
        hub=hub, issuer_evidence=evidence, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    rejected = result["rejected_counter_evidence"][0]
    assert "issuer_source_ref_missing" in rejected["reasons"]
    diagnostics = {row["source_ref"]: row for row in rejected["reference_resolution"]}
    assert diagnostics["CE-QUANT"]["resolution"] == "context_only"
    assert diagnostics["CE-QUANT"]["source_ids"] == ["pack.quant"]
    assert diagnostics["CE-NOT-REGISTERED"]["resolution"] == "unknown_reference"
    assert all(row["issuer_eligible"] is False for row in diagnostics.values())


def test_unregistered_semantic_id_does_not_self_authorize_sidecar_point():
    point = _point(1)
    point.pop("evidence_id")
    point.pop("stale")
    point["status"] = "resolved"
    point["source_refs"] = []
    result = build_company_risk_contract(
        {"risk_contract": {"decision_points": [point]}},
        issuer_evidence=_complete_issuer_evidence()[:5],
        as_of=AS_OF, conflicts=[], events=[],
    )

    result_point = result["decision_points"][0]
    assert result_point["eligible"] is False
    assert result_point["source_refs"] == []
    assert "source_refs_missing" in result_point["ineligibility_reasons"]


def test_complete_contract_discloses_semantic_limit_and_hashes_validation_boundary():
    result = build_company_risk_contract(
        issuer_evidence=_complete_issuer_evidence(), as_of=AS_OF,
        conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert result["gate"]["passed"] is True
    assert result["validation_scope"] == {
        "mode": "deterministic",
        "checks": [
            "structured_fields", "registered_reference_resolution",
            "issuer_context_boundary", "supplied_freshness",
        ],
        "source_content_refetched": False,
    }
    semantic = result["semantic_review"]
    assert semantic["status"] == "not_independently_verified"
    assert semantic["counter_relation_basis"] == "declared_by_structured_input"
    assert semantic["counter_direction_verified"] is False
    assert semantic["citation_entailment_verified"] is False
    assert semantic["affects_gate_status"] is False
    assert "尚未经过独立语义核验" in semantic["detail"]

    identity = {
        "schema_version": result["schema_version"],
        "validation_scope": result["validation_scope"],
        "semantic_review": semantic,
        "as_of": result["as_of"],
        "counter_groups": result["counter_evidence_groups"],
        "decision_points": result["decision_points"],
        "checks": result["checks"],
        "conflicts": result["conflicts"],
        "gaps": result["gaps"],
    }

    def identity_id(payload):
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "RISK-" + hashlib.sha256(encoded).hexdigest()[:16].upper()

    assert result["contract_id"] == identity_id(identity)
    identity.pop("validation_scope")
    identity.pop("semantic_review")
    assert result["contract_id"] != identity_id(identity)


def test_failed_contract_retains_same_non_blocking_semantic_boundary():
    result = build_company_risk_contract(as_of=AS_OF, conflicts=[], events=[])

    assert result["status"] == "failed"
    assert result["validation_scope"]["mode"] == "deterministic"
    assert result["semantic_review"]["status"] == "not_independently_verified"
    assert result["semantic_review"]["affects_gate_status"] is False
    assert not any(
        row["code"] == "semantic_review_missing" for row in result["gaps"]
    )


def _model_handoff_and_hub():
    source = {
        "source_id": "pack.financials", "source_ref": "section:financials",
        "evidence_id": "EV-REAL-ISSUER", "type": "issuer", "status": "ok",
        "stale": False, "usable": True,
    }
    hub = {
        "status": "complete", "as_of": AS_OF, "source_register": [source],
        "gaps": [], "conflicts": [],
    }
    model = {"strong_claims": [], "counter_evidence": [], "decision_points": []}
    for raw in _complete_issuer_evidence():
        kind = raw.get("kind")
        container = {
            "strong_claim": "strong_claims", "counter_evidence": "counter_evidence",
            "decision_point": "decision_points",
        }.get(kind)
        if container:
            row = dict(raw)
            row.pop("evidence_id", None)
            row["source_refs"] = ["EV-REAL-ISSUER"]
            model[container].append(row)
    return model, hub


def test_raw_model_handoff_cannot_supply_required_checks_even_with_real_issuer_refs():
    model, hub = _model_handoff_and_hub()
    model["checks"] = [{
        "function": "forward_vs_ttm_judgement",
        "source_refs": ["EV-REAL-ISSUER"],
        "output": {"judgement": "fabricated"},
    }, {
        "function": "consensus_dispersion", "source_refs": ["EV-REAL-ISSUER"],
        "output": {"min": 1, "max": 2},
    }]
    # Alternate/nested/oversized spellings must be inert because the model
    # channel is not a calculation input at all, not because a blacklist wins.
    model["risks"] = [{
        "kind": "risk", "risk": "synthetic calculation injection",
        "source_refs": ["EV-REAL-ISSUER"],
        "consensus_dispersion" + " " * 100 + "spoof": {"min": 1, "max": 2},
        "nested": {"forward_vs_ttm_judgement": {"judgement": "fabricated"}},
    }]
    before = copy.deepcopy(model)
    result = build_company_risk_contract(
        hub=hub, model_risk_input=model, as_of=AS_OF, conflicts=[], events=[],
    )

    assert model == before
    assert result["status"] == "incomplete"
    assert result["gate"]["counter_evidence"]["passed"] is True
    assert result["gate"]["decision_points"]["eligible_issuer"] == 3
    assert result["checks"]["forward_vs_ttm_judgement"]["candidates"] == []
    assert result["checks"]["consensus_dispersion"]["candidates"] == []
    assert {row["code"] for row in result["gaps"]} >= {
        "forward_vs_ttm_judgement_missing", "consensus_dispersion_missing",
    }
    assert result["summary"]["usable_issuer_sources"] == 1


def test_raw_model_handoff_cannot_register_fabricated_issuer_provenance():
    model, hub = _model_handoff_and_hub()
    for rows in model.values():
        for row in rows:
            row.update({
                "evidence_id": "EV-FABRICATED", "source_id": "made.up.source",
                "source_refs": ["EV-FABRICATED"], "scope": "issuer", "type": "issuer",
                "usable": True, "stale": False, "status": "ok", "_scope": "issuer",
                "_source_id": "made.up.source", "_default_ref": "EV-FABRICATED",
            })
    result = build_company_risk_contract(
        hub=hub, model_risk_input=model, as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "incomplete"
    assert result["summary"]["usable_issuer_sources"] == 1
    assert result["gate"]["counter_evidence"]["passed"] is False
    assert result["gate"]["decision_points"]["eligible_issuer"] == 0
    assert all(
        diagnostic["resolution"] == "unknown_reference"
        for group in result["counter_evidence_groups"]
        for diagnostic in group["claim_reference_resolution"]
    )


def test_model_structures_can_close_gate_only_with_separate_trusted_checks():
    model, hub = _model_handoff_and_hub()
    trusted_checks = [
        row for row in _complete_issuer_evidence() if row.get("function")
    ]
    result = build_company_risk_contract(
        hub=hub, issuer_evidence=trusted_checks, model_risk_input=model,
        as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "complete"
    assert result["gate"]["counter_evidence"]["passed"] is True
    assert result["gate"]["decision_points"]["eligible_issuer"] == 3


def test_non_object_model_handoff_is_rejected_without_mutating_trusted_adapter():
    trusted = {"risk_contract": {"checks": [{
        "function": "consensus_dispersion", "source_refs": ["EV-REAL-ISSUER"],
        "output": {"min": 1, "max": 2},
    }]}}
    _, hub = _model_handoff_and_hub()
    result = build_company_risk_contract(
        trusted, hub, model_risk_input="not structured",
        as_of=AS_OF, conflicts=[], events=[],
    )

    assert result["status"] == "failed"
    assert result["checks"]["consensus_dispersion"]["status"] == "complete"
    assert any(row["code"] == "invalid_model_risk_input" for row in result["gaps"])
