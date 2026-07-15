from __future__ import annotations
from guanlan_v2.orchestration import enums as e


def test_portfolio_rating_five_values():
    assert [r.value for r in e.PortfolioRating] == ["Buy", "Overweight", "Hold", "Underweight", "Sell"]


def test_node_status_has_all_lifecycle_states():
    got = {s.value for s in e.NodeStatus}
    assert got == {"pending", "ready", "running", "completed", "degraded",
                   "incomplete", "failed", "timed_out", "blocked", "skipped", "cancelled"}


def test_data_mode_values():
    assert {m.value for m in e.DataMode} == {"online", "pit_replay"}


def test_enums_are_str_backed():
    assert e.Confidence.LOW == "low"
    assert e.DataStatus.OK == "ok"


def test_action_domains_are_distinct_and_complete():
    assert [x.value for x in e.ResearchAction] == ["buy", "accumulate", "hold", "avoid", "sell"]
    assert [x.value for x in e.PositionAction] == ["buy", "add", "hold", "reduce", "sell"]
    assert not hasattr(e, "Action")


def test_rotation_and_legacy_cycle_are_distinct():
    assert "分化" in {x.value for x in e.RotationStage}
    assert "分化" in {x.value for x in e.LegacyMarketCycleStage}
    assert e.RotationStage is not e.LegacyMarketCycleStage


def test_approval_decision_is_not_policy():
    assert {x.value for x in e.ApprovalDecision} == {"approved", "rejected"}
    assert e.ApprovalDecision is not e.ApprovalPolicy
