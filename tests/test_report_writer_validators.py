"""ReportOutput Pydantic validators — guard 配置一致性.

诱因: introspector 在 14 份大盘报告里发现 quality_flag
"rating_overall (-2) ≠ sum(rating_dimensions) (-4)" + "action 'sell' with
position_pct=0.0 contradicts ..." + "action_target_price_mismatch" 等.

这层 validator 是 hard guard, 配合 ``_execute`` 的 sanity_notes auto-fix 双保险.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from financial_analyst.agent.tier3.report_writer import ReportOutput


def _make(**overrides):
    """生成一个合法的 ReportOutput, 测试时用 overrides 改特定字段."""
    base = dict(
        output_md_path="/tmp/x.md",
        output_json_path="/tmp/x.json",
        rating_overall=3,
        rating_dimensions={"fund": 1, "tech": 2},
        action="hold",
        target_price=100.0,
        stop_loss=80.0,
        position_pct=0.03,
    )
    base.update(overrides)
    return ReportOutput(**base)


class TestValidActions:
    def test_buy_with_position(self):
        r = _make(action="buy", position_pct=0.05, rating_overall=6)
        assert r.action == "buy"

    def test_hold_with_position(self):
        r = _make(action="hold", position_pct=0.03)
        assert r.action == "hold"

    def test_sell_zero_position(self):
        r = _make(action="sell", position_pct=0.0, rating_overall=-3)
        assert r.action == "sell"

    def test_avoid_zero_position(self):
        r = _make(action="avoid", position_pct=0.0, rating_overall=-7)
        assert r.action == "avoid"

    def test_accumulate_valid(self):
        r = _make(action="accumulate", position_pct=0.04)
        assert r.action == "accumulate"


class TestRangeConstraints:
    def test_rating_too_low_rejected(self):
        with pytest.raises(ValidationError, match="greater than or equal"):
            _make(rating_overall=-15)

    def test_rating_too_high_rejected(self):
        with pytest.raises(ValidationError, match="less than or equal"):
            _make(rating_overall=15)

    def test_position_negative_rejected(self):
        with pytest.raises(ValidationError):
            _make(position_pct=-0.01)

    def test_position_over_10pct_rejected(self):
        with pytest.raises(ValidationError):
            _make(position_pct=0.15)

    def test_target_price_zero_rejected(self):
        with pytest.raises(ValidationError):
            _make(target_price=0.0)

    def test_target_price_negative_rejected(self):
        with pytest.raises(ValidationError):
            _make(target_price=-50.0)

    def test_stop_loss_negative_rejected(self):
        with pytest.raises(ValidationError):
            _make(stop_loss=-10.0)


class TestCrossFieldConsistency:
    def test_avoid_with_position_rejected(self):
        """introspector 反馈: action='avoid' 但 position_pct>0 是逻辑矛盾."""
        with pytest.raises(ValidationError, match="position_pct.*> 0"):
            _make(action="avoid", position_pct=0.03)

    def test_buy_zero_position_rejected(self):
        """buy 必须有正仓位, 否则是 hold/sell."""
        with pytest.raises(ValidationError, match="buy 必须有正仓位"):
            _make(action="buy", position_pct=0.0)

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError, match="不在合法集"):
            _make(action="YOLO")


class TestBoundaryValues:
    def test_rating_exactly_10(self):
        r = _make(rating_overall=10)
        assert r.rating_overall == 10

    def test_rating_exactly_neg_10(self):
        r = _make(rating_overall=-10, action="avoid", position_pct=0.0)
        assert r.rating_overall == -10

    def test_position_exactly_10pct(self):
        r = _make(position_pct=0.10, action="buy", rating_overall=8)
        assert r.position_pct == 0.10

    def test_position_zero_with_avoid(self):
        r = _make(position_pct=0.0, action="avoid", rating_overall=-5)
        assert r.position_pct == 0.0
