from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

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
    # SP-2 FDR 多重检验校正: 跑 bench_runner 时, 对所有因子 IC p 值做 BH 或 Bonferroni
    # 校正, 回填 fdr_q + is_significant. None 关闭校正 (向后兼容). 单因子模式
    # (factor_report) 不会用到 fdr_method, 只填 p_value, fdr_q 留 None.
    fdr_method: Optional[Literal["bh", "bonferroni"]] = "bh"
    fdr_alpha: float = 0.05

    def effective_fwd_days(self) -> int:
        if self.fwd_days is not None:
            return int(self.fwd_days)
        return _FWD_BY_FREQ.get(self.freq, 21)

    def periods_per_year(self) -> int:
        return _PPY_BY_FREQ.get(self.freq, 12)
