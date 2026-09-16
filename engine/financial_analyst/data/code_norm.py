"""Shared bare-6-digit code -> exchange resolvers (ETF + 北交所号段).

A-share ETF listing prefixes (bare 6-digit codes):
  Shanghai (SH): 51x (510-519), 56x (560-563), 58x (580 / 588 STAR)
  Shenzhen (SZ): 15x (159 / 150-159)
Precise 2-char prefixes are used (not broad 5->SH / 1->SZ) so SH/SZ
convertible bonds (11x/12x/13x) are NOT misclassified as ETFs.

北交所股票号段：首位 4/8（旧号段）或精确 920 前缀（2024 起新号段）。
900xxx 沪 B 股不属于北交所。
"""
from __future__ import annotations
from typing import Optional


def is_bj_code(code6) -> bool:
    """Return whether a bare six-digit code belongs to a Beijing segment."""
    code = str(code6).strip() if code6 is not None else ""
    if not (code.isdigit() and len(code) == 6):
        return False
    return code[0] in "48" or code.startswith("920")


def etf_exchange(code6: str) -> Optional[str]:
    """Return 'SH' / 'SZ' for a bare 6-digit ETF code, else None.

    Returns None for non-ETF input (stocks, bonds, malformed) so callers
    fall through to their existing stock/bond logic unchanged.
    """
    c = str(code6).strip()
    if not (c.isdigit() and len(c) == 6):
        return None
    p2 = c[:2]
    if p2 in ("51", "56", "58"):
        return "SH"
    if p2 == "15":
        return "SZ"
    return None
