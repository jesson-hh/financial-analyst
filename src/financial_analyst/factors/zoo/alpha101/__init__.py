"""WorldQuant 101 Formulaic Alphas (Kakushadze 2015, arXiv:1601.00991).

This package ships the most-cited subset of the 101 alphas in v1.3.0;
further alphas land in later releases. Each alpha is a frozen
``AlphaSpec`` registered with the central zoo on import.

Source paper: https://arxiv.org/abs/1601.00991
"""
from __future__ import annotations
from financial_analyst.factors.zoo.alpha101 import alphas  # noqa: F401  (triggers @register)
