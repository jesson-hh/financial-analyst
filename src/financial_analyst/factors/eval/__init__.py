"""单因子业内标准评测引擎 (SP-A)。"""
from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import FactorReport, build_report, factor_report

__all__ = ["EvalConfig", "FactorReport", "build_report", "factor_report"]
