"""Guotai Junan Securities 191 Alphas (国泰君安 191 量化因子, 2017).

Designed for the Chinese A-share short-horizon prediction task —
generally outperforms WorldQuant 101 on rev_5 / fwd_5d-style targets
on CSI 300 / CSI 500.

v1.3.0 ships the most-cited subset; the remaining alphas land in
later releases.

Original handbook: 国泰君安证券研究所量化研究团队 (2017),
"191 短周期价量阿尔法因子".
"""
from __future__ import annotations
from financial_analyst.factors.zoo.gtja191 import alphas  # noqa: F401
