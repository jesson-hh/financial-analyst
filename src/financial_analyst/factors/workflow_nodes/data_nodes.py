"""数据节点 — universe 解析 + 面板加载.

SP-W2A spec §SP-W2A 表第 1/4 行.

两个节点:
- ``data.universe`` (params.name) → ``list[str]`` codes
- ``data.load_panel`` (params.freq/start/end, inputs.codes) → ``PanelData.df`` DataFrame

注释 a 在包级 __init__.py 已交代; 这里就细节实现.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from financial_analyst.workflow.registry import node


# ---------------------------------------------------------------------------
# 1. data.universe — params.name → list[str]
# ---------------------------------------------------------------------------


class UniverseParams(BaseModel):
    """universe 名 → 代码列表参数."""

    name: str = Field(
        "csi300_active",
        description="universe 名: csi300 / csi500 / csi800 / csi_fast / csi300_active / all / 或自定义路径",
    )


@node(
    type="data.universe",
    params_model=UniverseParams,
    risk="normal",
    pit=False,
    group="data",
    tag=["data"],
    description="按 universe 名 (csi300/csi500/csi800/csi_fast/csi300_active/all) 解析为股票代码列表.",
)
def data_universe(params: dict, inputs: dict) -> list[str]:
    """``resolve_universe_codes(params.name) -> list[str]``.

    缺 universe 文件 / parquet 时返 [] (resolve_universe_codes 的硬契约), 调用方
    自己判断 len(codes)==0 落错.
    """
    from financial_analyst.data.universe import resolve_universe_codes

    name = params.get("name", "csi300_active")
    codes = resolve_universe_codes(name)
    if not codes:
        raise ValueError(
            f"universe {name!r} 解析为空 (试 'fa data bootstrap' 或换 csi300_active)."
        )
    return list(codes)


# ---------------------------------------------------------------------------
# 2. data.load_panel — params 窗口 + inputs.codes → PanelData.df (DataFrame)
# ---------------------------------------------------------------------------


class LoadPanelParams(BaseModel):
    """面板加载窗口参数 (freq/start/end)."""

    freq: str = Field("day", description="行情频率: day / 5min / 1min")
    start: str = Field("2024-12-01", description="起始日期 (YYYY-MM-DD)")
    end: str = Field("2026-05-30", description="结束日期 (YYYY-MM-DD)")


@node(
    type="data.load_panel",
    params_model=LoadPanelParams,
    risk="normal",
    pit=False,
    group="data",
    tag=["data"],
    description="基于上游 codes + freq/start/end 加载行情面板, 返 MultiIndex DataFrame (datetime,code).",
)
def data_load_panel(params: dict, inputs: dict) -> pd.DataFrame:
    """``load_panel_cached(loader, codes, start, end, freq=day) -> PanelData``.

    返回 ``PanelData.df`` (单层 DataFrame), 因为 artifact_store 只支持 DataFrame /
    Series / 标量. 下游 factor 节点入口再 ``PanelData(df)`` 重包.
    """
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached

    upstream_codes: Any = inputs.get("codes")
    if upstream_codes is None:
        raise ValueError("data.load_panel 缺 inputs.codes (上游应是 data.universe 节点)")
    if not isinstance(upstream_codes, (list, tuple)):
        # artifact_store 把 JSON list 读回是 list, 不应是别的类型. 防御性 cast.
        raise TypeError(
            f"data.load_panel 期望 inputs.codes 是 list, 实际是 {type(upstream_codes).__name__}"
        )

    codes = [str(c) for c in upstream_codes]
    freq = params.get("freq", "day")
    start = params.get("start", "2024-12-01")
    end = params.get("end", "2026-05-30")

    loader = get_default_loader()

    # industry_loader 可选: 用于 indneutralize 算子. 缺工业映射时 None (PanelData 会
    # 用 "未知" 填). 失败也不阻断主流程.
    ind_loader: Any = None
    try:
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path

        if industry_map_path().exists():
            ind_loader = IndustryLoader()
    except Exception:
        ind_loader = None

    panel = load_panel_cached(
        loader, codes, start, end, freq=freq, industry_loader=ind_loader
    )
    # 返回 DataFrame (单层) — artifact_store 写 parquet. 不返 PanelData 对象 (artifact_store
    # 不支持自定义类), 下游再包.
    return panel.df


__all__ = ["UniverseParams", "LoadPanelParams", "data_universe", "data_load_panel"]
