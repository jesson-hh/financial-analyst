"""单因子业内标准评测引擎 (SP-A) + 事件研究 (SP-B.2)。"""
from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import FactorReport, build_report, factor_report
from financial_analyst.factors.eval.event import (
    EventReport, EventHorizon, build_event_report, event_report)

__all__ = ["EvalConfig", "FactorReport", "build_report", "factor_report",
           "EventReport", "EventHorizon", "build_event_report", "event_report"]
