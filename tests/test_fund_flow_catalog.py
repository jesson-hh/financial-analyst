# tests/test_fund_flow_catalog.py
# 资金面族 6 因子:进了 _FACTOR_CATALOG/_FACTOR_CATS,表达式合法、只用白名单名、能解析;
# 且在选股目录 FAMILY_ORDER 里浮现。
import ast
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import validate_expr, _KNOWN_NAMES  # noqa: E402
from guanlan_v2.workflow.api import _FACTOR_CATALOG, _FACTOR_CATS  # noqa: E402


def _ff_factors():
    return [(n, e, c, d, desc) for (n, e, c, d, desc) in _FACTOR_CATALOG if c == "资金面"]


def test_six_fund_flow_factors_registered():
    assert len(_ff_factors()) == 6
    assert "资金面" in _FACTOR_CATS


def test_fund_flow_exprs_valid_and_whitelisted():
    for name, expr, cat, direction, desc in _ff_factors():
        validate_expr(expr)                       # 注册字段 + 无禁词
        compile(expr, f"<{name}>", "eval")        # python 语法可解析
        used = {n.id for n in ast.walk(ast.parse(expr, mode="eval"))
                if isinstance(n, ast.Name)}
        illegal = used - _KNOWN_NAMES
        assert not illegal, f"{name} 用了清单外名字: {illegal}"
        assert direction in ("正向", "反向"), f"{name} 方向标注不合规: {direction}"


def test_fund_flow_in_screen_family_order():
    from guanlan_v2.screen.catalog import FAMILY_ORDER
    assert "资金面" in FAMILY_ORDER


def test_fund_flow_factors_surface_in_screen_catalog():
    # 锁住 _KEEP_FAMILIES 闸:6 个资金面因子真出现在选股页 FACTOR_DEFS(不止 FAMILY_ORDER 列名)。
    from guanlan_v2.screen.catalog import FACTOR_DEFS
    ff = [v for v in FACTOR_DEFS.values() if v.get("family") == "资金面"]
    assert len(ff) == 6
