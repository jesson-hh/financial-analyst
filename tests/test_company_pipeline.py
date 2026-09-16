from __future__ import annotations

import json

import pytest

from guanlan_v2.research.company_pipeline import (
    CompanyResearchPipeline,
    OverallStatus,
    STAGE_ORDER,
    StageStatus,
    run_company_research,
)


def _complete_evidence() -> dict[str, object]:
    return {
        "profile": {
            "evidence": {
                "profile": {"evidence_id": "profile-1"},
                "quote": {"evidence_id": "quote-1"},
                "trade_calendar": {"evidence_id": "calendar-1"},
            },
        },
        "financials": {"evidence": {"financials": {"evidence_id": "fin-1"}}},
        "estimates": {"evidence": {"estimates": {"evidence_id": "est-1"}}},
        "valuation": {"evidence": {"quote": {"evidence_id": "quote-1"}}},
        "risk": {
            "gaps": [{
                "code": "optional_kline_unavailable",
                "message": "No K-line snapshot was supplied.",
                "critical": False,
            }],
        },
        "report": {},
    }


def test_fixed_six_stage_order_and_in_memory_artifact_skeleton() -> None:
    calls: list[str] = []

    def handler(context):
        calls.append(context.stage)
        row = {"id": f"calc-{context.stage}"}
        if context.stage == "report":
            return {"report": {"markdown": "# Company"}}
        return {"calculations": [row], "events": [{"event": "computed"}]}

    pipeline = CompanyResearchPipeline({stage: handler for stage in STAGE_ORDER})
    result = pipeline.run(_complete_evidence())

    assert result.status is OverallStatus.COMPLETE
    assert tuple(row.stage for row in result.stages) == STAGE_ORDER
    assert all(row.status is StageStatus.COMPLETE for row in result.stages)
    assert calls == list(STAGE_ORDER)
    assert set(result.artifacts.as_dict()) == {
        "manifest",
        "events",
        "calculations",
        "conflicts",
        "report",
    }
    assert result.artifacts.manifest["stage_order"] == list(STAGE_ORDER)
    assert result.artifacts.report["content"] == {"markdown": "# Company"}
    assert len(result.artifacts.calculations) == 5
    assert [event["sequence"] for event in result.artifacts.events] == list(
        range(1, len(result.artifacts.events) + 1)
    )
    # Public return shape is ready for later workspace persistence, but this
    # state-machine layer itself performs no file I/O.
    json.dumps(result.as_dict())


def test_missing_critical_profile_input_blocks_all_handlers_and_downstream_stages() -> None:
    evidence = _complete_evidence()
    evidence["profile"] = {
        "evidence": {
            "profile": {"evidence_id": "profile-1"},
            "quote": {"evidence_id": "quote-1"},
        }
    }
    calls: list[str] = []
    pipeline = CompanyResearchPipeline({
        stage: lambda context: calls.append(context.stage) for stage in STAGE_ORDER
    })

    result = pipeline.run(evidence)

    assert result.status is OverallStatus.INCOMPLETE
    assert result.stages[0].status is StageStatus.INCOMPLETE
    assert [row.status for row in result.stages[1:]] == [StageStatus.SKIPPED] * 5
    assert calls == []
    gap = next(
        gap for gap in result.stages[0].gaps if gap.code == "missing_required_input"
    )
    assert gap.input_key == "trade_calendar"
    assert gap.critical is True
    assert result.artifacts.calculations == ()


def test_a_later_gate_never_calls_its_or_downstream_calculators() -> None:
    evidence = _complete_evidence()
    evidence["financials"] = {"evidence": {}}
    calls: list[str] = []

    def handler(context):
        calls.append(context.stage)
        return {"calculations": {"id": context.stage}}

    result = CompanyResearchPipeline({stage: handler for stage in STAGE_ORDER}).run(evidence)

    assert calls == ["profile"]
    assert [row.status for row in result.stages] == [
        StageStatus.COMPLETE,
        StageStatus.INCOMPLETE,
        StageStatus.SKIPPED,
        StageStatus.SKIPPED,
        StageStatus.SKIPPED,
        StageStatus.SKIPPED,
    ]
    assert [row["id"] for row in result.artifacts.calculations] == ["profile"]


def test_evidence_hub_failure_has_precedence_and_is_reported_not_raised() -> None:
    evidence = _complete_evidence()
    evidence["estimates"] = {"status": "failed", "error": "provider timeout"}

    result = run_company_research(evidence)

    assert result.status is OverallStatus.FAILED
    assert result.stages[2].status is StageStatus.FAILED
    assert result.stages[2].error == "provider timeout"
    assert result.stages[3].status is StageStatus.SKIPPED
    assert result.artifacts.manifest["status"] == "failed"
    assert result.artifacts.report["status"] == "failed"
    assert any(gap["code"] == "evidence_hub_failed" for gap in result.artifacts.report["gaps"])


def test_handler_exception_becomes_failed_stage_and_blocks_downstream() -> None:
    def broken(_context):
        raise RuntimeError("bad denominator")

    result = CompanyResearchPipeline({"valuation": broken}).run(_complete_evidence())

    assert result.status is OverallStatus.FAILED
    assert result.stages[3].status is StageStatus.FAILED
    assert result.stages[3].error == "RuntimeError: bad denominator"
    assert result.stages[4].status is StageStatus.SKIPPED
    assert result.artifacts.calculations == ()


def test_stale_precedes_incomplete_in_the_report_status() -> None:
    evidence = _complete_evidence()
    evidence["profile"] = {
        "evidence": {
            "profile": {"evidence_id": "profile-1", "stale": True},
            "quote": {"evidence_id": "quote-1"},
            "trade_calendar": {"evidence_id": "calendar-1"},
        }
    }
    evidence["financials"] = {"evidence": {}}

    result = run_company_research(evidence)

    assert result.stages[0].status is StageStatus.COMPLETE
    assert result.stages[1].status is StageStatus.INCOMPLETE
    assert result.status is OverallStatus.STALE
    assert result.artifacts.manifest["status"] == "stale"


def test_report_cannot_complete_without_nonempty_handler_output() -> None:
    missing_handler = run_company_research(_complete_evidence())
    assert missing_handler.stages[-1].status is StageStatus.INCOMPLETE
    assert missing_handler.stages[-1].gaps[-1].code == "report_handler_missing"

    handlers = {stage: (lambda _context: None) for stage in STAGE_ORDER}
    empty_report = CompanyResearchPipeline(handlers).run(_complete_evidence())
    assert empty_report.stages[-1].status is StageStatus.INCOMPLETE
    assert empty_report.stages[-1].gaps[-1].code == "report_content_missing"


def test_explicit_critical_gap_blocks_but_optional_gap_does_not() -> None:
    blocked = _complete_evidence()
    blocked["estimates"] = {
        "evidence": {"estimates": {"evidence_id": "est-1"}},
        "gaps": [{
            "code": "consensus_period_mismatch",
            "message": "Consensus periods cannot be aligned.",
            "input_key": "estimates",
            "critical": True,
        }],
    }
    blocked_result = run_company_research(blocked)
    assert blocked_result.stages[2].status is StageStatus.INCOMPLETE

    allowed_result = run_company_research(_complete_evidence())
    assert allowed_result.stages[4].status is StageStatus.COMPLETE
    assert allowed_result.stages[4].gaps[0].critical is False


def test_unknown_stages_and_handlers_are_rejected_deterministically() -> None:
    with pytest.raises(ValueError, match="unknown company research handlers"):
        CompanyResearchPipeline({"mystery": lambda _context: None})
    with pytest.raises(ValueError, match="unknown company research stages"):
        run_company_research({"mystery": {}})
