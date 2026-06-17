from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_ACTIONS = {"buy", "add", "hold", "reduce", "sell"}  # 与 backtest.decision.DecisionLeg 一致


@dataclass
class WatchItem:
    code: str
    avg_cost: Optional[float] = None     # 用户关注成本 (可选)
    stop_loss: Optional[float] = None    # 用户设的止损 -> 启用 stop_break 触发


@dataclass
class WatchContext:
    code: str
    name: str
    now_ts: str
    trigger: Dict[str, Any]              # {kind, detail, metric}
    realtime: Dict[str, Any]             # Tencent 快照子集
    bars_5min: List[Dict[str, Any]]      # 近 N 根 {datetime,open,high,low,close,vol}
    factors_eod: Dict[str, Any] = field(default_factory=dict)
    news_today: List[str] = field(default_factory=list)
    item: Optional[WatchItem] = None


@dataclass
class WatchRec:
    code: str
    action: str
    reason: str
    trigger_kind: str
    ts: str
    target_price: float = 0.0
    stop_loss: float = 0.0
    confidence: float = 0.0
    error: str = ""

    def __post_init__(self):
        if self.action not in _ACTIONS:
            raise ValueError(f"bad action {self.action!r}, must be {_ACTIONS}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
