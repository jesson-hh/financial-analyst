# tests/test_fund_flow_expr.py
# 资金面字段已注册进 DSL 白名单 + VOCAB,validate_expr 不再误判为未知字段。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import (  # noqa: E402
    _FIELD_NAMES, validate_expr, FACTOR_VOCAB,
)

_FF = [
    "main_net_amount", "main_net_pct",
    "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct",
    "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",
]


def test_all_fund_flow_fields_whitelisted():
    for f in _FF:
        assert f in _FIELD_NAMES, f"{f} 未进 _FIELD_NAMES"


def test_validate_accepts_fund_flow_expr():
    # 之前会抛「未知字段」;注册后不抛。
    validate_expr("rank(ts_mean(main_net_pct,5))")
    validate_expr("rank((super_large_net_pct+large_net_pct)-(medium_net_pct+small_net_pct))")
    validate_expr("rank(ts_sum(sign(main_net_amount),10))")


def test_vocab_mentions_fund_flow():
    assert "main_net_pct" in FACTOR_VOCAB
