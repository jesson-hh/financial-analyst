# tests/test_seats_watcher_hold_context.py
# 盯盘 watcher 的**持仓语境接线**(2026-08-03 真机验证驱动):
#   真机查出:生产 watcher 的 _build_payload 从不带 hold_entry/hold_bars,
#   于是盘中每一拍都只问「是否进场」,出场问题在生产自动化里**结构上从未被问过**
#   —— 卖出率恒 0 与经验卡、与 position_action 必答字段都无关(它们都到不了)。
#   本族钉住:台账已持有该票 → payload 带真入场价(台账 avg_cost,READ-ONLY 回放);
#   未持有 / 台账不可读 → payload 逐字节回到旧形状(旧记录红线)。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
from guanlan_v2.seats import watcher as W  # noqa: E402

_CW = {"code": "SZ300308", "name": "进攻·十卡PA", "strategy_id": "strat_x",
       "creed": "trend", "refs": [], "pa": True, "w": 0}


def _now():
    from datetime import datetime
    return datetime(2026, 8, 3, 10, 30)


def test_payload_carries_entry_price_when_ledger_holds(monkeypatch):
    """台账持有 → hold_entry = 该票 avg_cost(真值,不是猜的)。"""
    monkeypatch.setattr(W, "_positions_for_payload", lambda: {"SZ300308": 880.5})
    p = W._build_payload(_CW, _now(), {"name": "中际旭创"})
    assert p["hold_entry"] == 880.5


def test_payload_unchanged_when_flat(monkeypatch):
    """未持有 → 不落 hold_entry/hold_bars 键(旧 payload 形状逐字不变)。"""
    monkeypatch.setattr(W, "_positions_for_payload", lambda: {})
    p = W._build_payload(_CW, _now(), {"name": "中际旭创"})
    assert "hold_entry" not in p and "hold_bars" not in p


def test_ledger_failure_degrades_silently_not_fabricates(monkeypatch):
    """台账不可读 → 诚实无持仓语境,绝不编造一个入场价,更不能让整拍崩。"""
    def _boom():
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(W, "_positions_for_payload", _boom)
    p = W._build_payload(_CW, _now(), {"name": "中际旭创"})
    assert "hold_entry" not in p


def test_position_lookup_matches_by_numeric_core(monkeypatch):
    """台账键与盯盘码写法可能不同(300308 / SZ300308)→ 按数字核匹配,不因写法漏掉持仓。"""
    monkeypatch.setattr(W, "_positions_for_payload", lambda: {"300308": 880.5})
    p = W._build_payload(_CW, _now(), {"name": "中际旭创"})
    assert p["hold_entry"] == 880.5


def test_nonpositive_cost_is_not_a_position(monkeypatch):
    monkeypatch.setattr(W, "_positions_for_payload", lambda: {"SZ300308": 0.0})
    p = W._build_payload(_CW, _now(), {"name": "中际旭创"})
    assert "hold_entry" not in p


def test_positions_source_is_read_only_replay():
    """持仓来源必须是台账 READ-ONLY 回放(不新增写路径、不碰交易红线)。"""
    import inspect
    src = inspect.getsource(W._positions_for_payload)
    assert "_ledger_replay" in src and "_ledger_events" in src
    for banned in ("write", "append", "post", "order", "_persist"):
        assert banned not in src.lower(), f"持仓读取路径出现写形状: {banned}"
