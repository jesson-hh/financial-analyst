"""Qlib Alpha158 — Microsoft Qlib's reference 158-feature handler, ported
to the zoo registry so it benches uniformly alongside alpha101 / gtja191.

v1.3.2 ships the first 20 of 158 features (the simple OHLC ratios +
moving averages + stochastic indicators that don't need linear-
regression operators). Remaining alphas land in 1.3.x patches.

Source: Microsoft Qlib, ``qlib.contrib.data.handler.Alpha158``
(github.com/microsoft/qlib). The naming convention follows Qlib's:

* ``KMID``, ``KLEN``, ``KUP``, ``KLOW``, ``KSFT`` — daily candle shape
* ``MA{N}``, ``STD{N}``, ``ROC{N}`` — moving stats vs current close
* ``RSV{N}``, ``CNTP{N}``, ``CORR{N}`` — stochastic / count / correlation
"""
from __future__ import annotations
from financial_analyst.factors.zoo.qlib158 import alphas  # noqa: F401
