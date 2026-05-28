from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Tuple

_FWD_BY_FREQ = {"day": 1, "week": 5, "month": 21}
_PPY_BY_FREQ = {"day": 252, "week": 52, "month": 12}


@dataclass
class EvalConfig:
    universe: str = "csi500"
    freq: str = "month"               # day / week / month
    start: Optional[str] = None       # None → 今天 - 2y
    end: Optional[str] = None         # None → 今天
    fwd_days: Optional[int] = None    # None → 按 freq (1/5/21)
    n_groups: int = 10
    cost_bps: float = 0.0
    winsorize_q: float = 0.01
    standardize: bool = True
    neutralize: bool = False          # A.2; True 时 build_report 会进 warnings 并跳过
    decay_horizons: Tuple[int, ...] = (1, 3, 5, 10, 21, 42)

    def effective_fwd_days(self) -> int:
        if self.fwd_days is not None:
            return int(self.fwd_days)
        return _FWD_BY_FREQ.get(self.freq, 21)

    def periods_per_year(self) -> int:
        return _PPY_BY_FREQ.get(self.freq, 12)
