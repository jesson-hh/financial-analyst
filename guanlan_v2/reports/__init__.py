"""观澜自有「报告库」(reports)模块 —— 工作流 run 结果存盘 + 浏览/重看/删除。

``build_reports_router()`` 返回无 prefix 的 APIRouter(``/report/save|list|get/{id}|delete``),
由 ``guanlan_v2.server`` include。报告落 ``guanlan_v2/reports/store/*.json``(仓内自有 JSON,
非 engine、不拷 stocks 数据)。
"""
from guanlan_v2.reports.api import build_reports_router

__all__ = ["build_reports_router"]
