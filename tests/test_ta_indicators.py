# tests/test_ta_indicators.py
# TA 指标库(guanlan_v2/factorlib/base/ta_indicators.json)的快速门禁:
# 不碰数据、不连服务器 —— schema + 无禁词 + Python 语法可解析 + 仅用引擎白名单名。
# 真·能否在真实面板上算出 KPI,由 scripts/verify_ta_indicators.py(POST /factor/report)把关。
import ast
import json
import sys
from pathlib import Path

# 优先用在仓 engine/(venv 里的可编辑安装是旧分支)。validate_expr 版本无关,
# 这里只是确保引擎可导入;真正运行期算子由 live /factor/report 用在仓 engine 校验。
_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import validate_expr  # noqa: E402

_TA_JSON = _REPO / "guanlan_v2" / "factorlib" / "base" / "ta_indicators.json"

# 引擎 compile_factor 受限命名空间允许的名字(字段 + 算子),见 engine/.../factors/zoo/expr.py。
_ALLOWED_NAMES = {
    "close", "open", "high", "low", "volume", "vwap", "amount", "returns", "industry",
    "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate",
    "rank", "scale", "ts_sum", "ts_mean", "stddev", "ts_max", "ts_min",
    "ts_argmax", "ts_argmin", "ts_rank", "delta", "delay", "correlation",
    "covariance", "decay_linear", "sma", "wma", "signedpower", "log", "sign",
    "abs", "abs_", "product", "power", "indneutralize", "max_pair", "min_pair",
    "filter_where", "cross",
}


def _entries():
    return json.loads(_TA_JSON.read_text(encoding="utf-8"))


def test_json_exists_and_nonempty():
    assert _TA_JSON.is_file(), f"缺 {_TA_JSON}"
    assert len(_entries()) >= 15


def test_every_entry_has_required_fields():
    for e in _entries():
        assert e.get("name", "").startswith("ta_"), f"name 不合规: {e}"
        assert e.get("family") == "ta", f"family 必须 ta: {e}"
        assert e.get("expr", "").strip(), f"expr 为空: {e}"
        assert e.get("description", "").strip(), f"description 为空: {e}"


def test_names_unique():
    names = [e["name"] for e in _entries()]
    assert len(names) == len(set(names)), "name 有重复"


def test_expr_passes_validate_and_syntax():
    for e in _entries():
        validate_expr(e["expr"])                      # 无 __ / import / lambda
        compile(e["expr"], f"<{e['name']}>", "eval")   # Python 语法可解析


def test_expr_only_uses_whitelisted_names():
    for e in _entries():
        tree = ast.parse(e["expr"], mode="eval")
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        illegal = used - _ALLOWED_NAMES
        assert not illegal, f"{e['name']} 用了清单外名字: {illegal}"
