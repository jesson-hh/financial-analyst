"""炼因子 (SP-B): 自然语言 → 因子 + 用户因子持久化。"""
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult
from financial_analyst.factors.forge.store import UserFactorStore

__all__ = ["forge_factor", "ForgeResult", "UserFactorStore"]
