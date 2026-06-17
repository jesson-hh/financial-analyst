# -*- coding: utf-8 -*-
"""L4 九视角(V1-V10)—— **观察读数**,不是 if-else 决策树。

playbook(``vendor/knowledge/analyst_playbook.md`` §8)红线:**"别把 V1-V9 套成 if-else,
它们是观察角度,不是决策树"** + **"即使缺数据,也应先用视角推理,标注'数据补全后可更精确'"**。
故本模块产出的是**每个视角能否从现有数据评估** + 评估结论 + **置信标签**:

- ``conf='data'``  —— 由真实数据直接得出(V1 节奏 / V2 梯队 / V3 强度 / V5 反应 / V6 共振 /
  V7 催化「近期财报」/ V8 资金「龙虎榜在场」/ V9 比较)
- ``conf='proxy'`` —— 由弱代理估算,仅供参考(V4 位置 / V7 催化「无近期业绩」/ V8 资金「市值层」)
- ``conf='gap'``   —— 无运行期数据,需材料/定性(V10 执行;V7/V8 在缺源时回退)
  注:V2 梯队(连板高度/封板)+ V5 反应(gap×开收)由日线 OHLC 现算;V7 催化由迁后财务
  ann_date+YoY 现读(业绩维);V8 资金优先龙虎榜真净买归因(北向 2024-08 停披不可用)。
  均由 ``_panel_enrich`` 供数,缺则回退。

决策(评级/仓位/护盾/≤5 收敛)在 ``decision.py``(L5);本模块只"看",不"定"。
数据只读(经 vendored 产物),本期独立于其他界面,不与 chat/cards/factor/seats 交互。
"""
from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from guanlan_v2.strategy.paths import MARKET_BREADTH_PARQUET

# 主线 status → V3 强度视角标签(与 ranking.mainline_status_map 同源)
_V3_LABEL = {
    "mainline": "主线", "revival": "二波", "initiation": "启动",
    "decay": "退潮", "cold": "冷门", "neutral": "轮动/中性",
}
# V3 看多向状态(主升期前瞻 alpha)
_V3_BULL = {"mainline", "revival", "initiation"}


@functools.lru_cache(maxsize=1)
def market_cycle() -> Optional[Dict[str, Any]]:
    """V1 节奏视角 —— 市场情绪周期阶段(读 market_breadth_resid 最新截面)。

    判据(R27,见 playbook V1):``lu_resid_pct60`` = 涨停残差 60 日分位,
    ``amt_resid_pct60`` = 成交额残差 60 日分位。lu>0.95 A 级情绪;amt>0.90 分化警示区。
    返回 ``{stage,label,lu_pct60,amt_pct60,as_of}``;缺文件/全空 → None。
    """
    import pandas as pd

    if not MARKET_BREADTH_PARQUET.exists():
        return None
    try:
        df = pd.read_parquet(MARKET_BREADTH_PARQUET)
    except Exception:  # noqa: BLE001
        return None
    if "lu_resid_pct60" not in df.columns or df.empty:
        return None
    sub = df.dropna(subset=["lu_resid_pct60"])
    if sub.empty:
        return None
    row = sub.iloc[-1]
    lu = float(row["lu_resid_pct60"])
    amt = float(row["amt_resid_pct60"]) if "amt_resid_pct60" in df.columns and pd.notna(row.get("amt_resid_pct60")) else None
    try:
        as_of = str(pd.Timestamp(sub.index[-1]).date())
    except Exception:  # noqa: BLE001
        as_of = None

    # 周期阶段(粗分,honest):lu 60 日分位为主轴,amt 高分位作分化叠加
    if lu < 0.10:
        stage = "冰点"
    elif lu >= 0.90 and (amt is not None and amt >= 0.90):
        stage = "分化"          # 顶部警示区(R27:amt p90+)
    elif lu >= 0.70:
        stage = "逼空"
    elif lu >= 0.35:
        stage = "发酵"
    else:
        stage = "回踩/启动"
    return {"stage": stage, "label": f"V1 节奏 · {stage}",
            "lu_pct60": round(lu, 3), "amt_pct60": (round(amt, 3) if amt is not None else None),
            "as_of": as_of}


def resonance_count(s: Dict[str, Any], metrics: Optional[Dict[str, Any]]) -> int:
    """V6 共振计数 —— 几个独立维度同时看多(1-4)。playbook V6:行情可持续性 ∝ 共振维度数。

    维度:① L1+L5 五维分 v4_total≥4(模型+评级看多)② L2 主线在场(mainline/revival/initiation)
    ③ L3 量能非派发(不在 distr/super_distr)④ V4 位置偏低(pos_pct≤0.5,安全垫)。
    """
    m = metrics or {}
    dims = 0
    v4t = s.get("v4_total")
    if v4t is not None and v4t >= 4:
        dims += 1
    if s.get("mainline") in _V3_BULL:
        dims += 1
    if s.get("vol_regime") not in ("distr", "super_distr"):
        dims += 1
    pos = m.get("pos_pct")
    if pos is not None and pos <= 0.5:
        dims += 1
    return dims


def _v4_position(s: Dict[str, Any], metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """V4 位置视角(proxy)—— 历史位置 × 反应粗判(立讯/仕佳/信维/中性)。

    仅有日线 close 序列 → 只能估"位置高低 + 涨幅",**反应模式(gap)缺**,故 conf=proxy。
    """
    m = metrics or {}
    pos = m.get("pos_pct")
    ret60 = m.get("ret60")
    ret20 = m.get("ret20")
    if pos is None or ret60 is None:
        return {"label": "位置数据待补", "conf": "gap", "evidence": "面板不足以算 60 日位置"}
    if pos >= 0.85 and ret60 >= 0.30:
        return {"label": "高位涨多 · 仕佳警示", "conf": "proxy",
                "evidence": f"位置 {round(pos*100)}% · 60日 {round(ret60*100)}%(需 V5 反应+V3 退潮 4 要素确认,见 L5 仕佳护盾)"}
    if pos <= 0.35 and (ret20 is not None and ret20 >= 0):
        return {"label": "低位企稳 · 信维/立讯候选", "conf": "proxy",
                "evidence": f"位置 {round(pos*100)}% · 20日 {round(ret20*100)}%(低位回升,位置安全垫)"}
    if pos <= 0.35:
        return {"label": "低位调整", "conf": "proxy",
                "evidence": f"位置 {round(pos*100)}% · 尚未企稳"}
    return {"label": "中位震荡", "conf": "proxy",
            "evidence": f"位置 {round(pos*100)}%"}


def _v8_capital(s: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """V8 资金视角 —— 优先用龙虎榜真资金归因(机构/游资 净买),否则市值层代理。

    playbook V8:大票涨=机构进场(数周+);小票涨=游资(3-5 日)。
    龙虎榜(``lhb_*``,新鲜到 ~T-数日)在场 → conf=data;否则市值层+当日涨跌代理 → conf=proxy。
    (北向持股 2024-08 起停披,不可用。)
    """
    m = metrics or {}
    net = m.get("lhb_net")
    if net is not None:
        amt = (f"{net / 1e8:+.2f}亿" if abs(net) >= 1e8 else f"{net / 1e4:+.0f}万")
        who = "机构" if m.get("lhb_inst") else "游资"
        date = m.get("lhb_date")
        n = m.get("lhb_n") or 1
        pct = m.get("lhb_pct")
        nstr = f" · 近12日{n}次上榜" if n > 1 else ""
        pstr = f"(占成交 {pct:.1f}%)" if pct is not None else ""
        if net > 0:
            return {"label": f"龙虎榜·{who}净买", "conf": "data",
                    "evidence": f"{date} 上榜 净买 {amt}{pstr}{nstr} · {who}席位主导"}
        return {"label": "龙虎榜·净卖出(出货)", "conf": "data",
                "evidence": f"{date} 上榜 净卖 {amt}{pstr}{nstr} · 资金离场"}

    layer = str(s.get("v4_layer") or "")
    chg = s.get("chg")
    up = (chg is not None and chg > 0)
    big = ("大" in layer) or ("中盘" in layer)
    small = ("小" in layer)
    if not layer:
        return {"label": "资金属性待补", "conf": "gap", "evidence": "缺市值层/龙虎榜/北向"}
    if big and up:
        return {"label": "大票上行 · 机构倾向", "conf": "proxy", "evidence": f"{layer} · 当日 +{round(chg,2)}%(大市值上行多为机构)"}
    if small and up:
        return {"label": "小票上行 · 游资倾向", "conf": "proxy", "evidence": f"{layer} · 当日 +{round(chg,2)}%(小市值上行多为游资,持续性 3-5 日)"}
    return {"label": f"{layer} · 中性", "conf": "proxy", "evidence": "当日无明确方向"}


def nine_view_scan(s: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None,
                   market: Optional[Dict[str, Any]] = None,
                   v9: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """逐股九视角扫描(V1-V10),返回有序读数列表。每项 ``{v,name,label,evidence,conf}``。

    ``s``       候选 s-dict(含 mainline/mainline_golden/v4_total/v4_layer/vol_regime/chg/ind)
    ``metrics`` {ret60,ret20,rsi,pos_pct}(``_panel_enrich`` 算)
    ``market``  ``market_cycle()`` 结果(V1,全市场同值)
    ``v9``      {pct,n}(本股 60 日涨幅在**同业候选**内的分位;比较视角)
    """
    out: List[Dict[str, Any]] = []
    m = metrics or {}

    # V1 节奏(市场级)
    if market:
        out.append({"v": "V1", "name": "节奏", "label": market["stage"], "conf": "data",
                    "evidence": f"涨停残差60日分位 {market['lu_pct60']}"
                                + (f" · 成交额分位 {market['amt_pct60']}" if market.get('amt_pct60') is not None else "")})
    else:
        out.append({"v": "V1", "name": "节奏", "label": "市场周期待补", "conf": "gap",
                    "evidence": "缺 market_breadth 产物"})

    # V2 梯队(连板/首板高度)—— 由日线现算(limit_streak/max_streak_20/sealed)
    streak = m.get("limit_streak")
    if streak is not None:
        sealed = m.get("sealed")
        max20 = m.get("max_streak_20") or 0
        if streak >= 2:
            out.append({"v": "V2", "name": "梯队", "label": f"{streak}连板", "conf": "data",
                        "evidence": f"连续 {streak} 日涨停" + ("(今日封死)" if sealed else "(今日炸板/未封)")})
        elif streak == 1:
            out.append({"v": "V2", "name": "梯队", "label": "首板" + ("·封死" if sealed else "·炸板"),
                        "conf": "data",
                        "evidence": "今日首次涨停" + ("(封死)" if sealed else "(盘中开板)")})
        else:
            out.append({"v": "V2", "name": "梯队", "label": "非涨停梯队", "conf": "data",
                        "evidence": ("今日未涨停" + (f",近20日最高 {max20} 连板(梯队退潮)"
                                     if max20 >= 2 else ",模型选股·非打板路径"))})
    else:
        out.append({"v": "V2", "name": "梯队", "label": "连板/首板结构需材料", "conf": "gap",
                    "evidence": "缺当日连板高度(面板无 OHLC)"})

    # V3 强度(板块主线)—— 真数据
    ml = s.get("mainline")
    if ml:
        lab = _V3_LABEL.get(ml, ml)
        golden = bool(s.get("mainline_golden"))
        out.append({"v": "V3", "name": "强度", "label": (("★金信号 " if golden else "") + lab),
                    "conf": "data",
                    "evidence": f"行业「{s.get('ind','—')}」月级状态={ml}"
                                + ("(上月启动→本月主线,fwd_60d +5.54pp 胜率87%)" if golden else "")})
    else:
        out.append({"v": "V3", "name": "强度", "label": "行业未在主线面板", "conf": "gap",
                    "evidence": f"行业「{s.get('ind','—')}」无月度主线状态"})

    # V4 位置(历史位置×反应)—— proxy
    p4 = _v4_position(s, m)
    out.append({"v": "V4", "name": "位置", **p4})

    # V5 反应(盯反应不盯消息)—— 由日线 OHLC 现算(gap 高开/低开 × 开→收)
    gap = m.get("gap")
    intra = m.get("intraday")
    if gap is not None and intra is not None:
        amp = m.get("amp")
        if gap >= 0.03 and intra <= -0.02:
            lab = "高开低走 · 弱反应"
        elif gap <= -0.03 and intra >= 0.02:
            lab = "低开高走 · 强承接"
        elif gap >= 0.01 and intra >= 0:
            lab = "高开走强"
        elif abs(gap) < 0.01 and abs(intra) < 0.01:
            lab = "平开窄幅"
        elif intra >= 0.02:
            lab = "盘中走强"
        elif intra <= -0.02:
            lab = "盘中走弱"
        else:
            lab = "中性反应"
        ev = (f"高开 {gap*100:+.1f}% · 开→收 {intra*100:+.1f}%"
              + (f" · 振幅 {amp*100:.1f}%" if amp is not None else "")
              + "(日线 OHLC;日内分时可再细化)")
        out.append({"v": "V5", "name": "反应", "label": lab, "conf": "data", "evidence": ev})
    else:
        out.append({"v": "V5", "name": "反应", "label": "消息反应需日内/gap", "conf": "gap",
                    "evidence": "仅日线收盘,缺 gap_up_fade/gap_down_recover 标签"})

    # V6 共振(独立维度计数)—— 真数据
    rc = resonance_count(s, m)
    rc_lab = {0: "无共振", 1: "一重", 2: "二重", 3: "三重", 4: "四重(强)"}.get(rc, f"{rc}重")
    out.append({"v": "V6", "name": "共振", "label": f"{rc_lab}共振", "conf": "data",
                "evidence": f"看多维度 {rc}/4(五维分≥4 / 主线在场 / 量能非派发 / 位置偏低)"})

    # V7 催化(业绩日历)—— 由迁后财务 ann_date + 同期 YoY 现读;事件/龙虎榜链仍待补
    cdays = m.get("cat_days")
    if cdays is not None:
        np_yoy, rev_yoy, ann = m.get("cat_np_yoy"), m.get("cat_rev_yoy"), m.get("cat_ann")
        # 净利同比为主;|>500%| 视为低基数畸变 → 回退营收同比定级
        base = np_yoy if (np_yoy is not None and abs(np_yoy) < 5) else (
            rev_yoy if rev_yoy is not None else None)

        def _pc(x):
            if x is None:
                return "—"
            return "畸变" if abs(x) >= 5 else f"{x * 100:+.0f}%"

        det = f"{ann} 财报(净利 {_pc(np_yoy)}/营收 {_pc(rev_yoy)})·{cdays}天前"
        if cdays <= 60 and base is not None:        # 近 ~2 月财报 = 业绩催化,按强弱定级
            if base >= 0.5:
                lab = "业绩催化 · A级高增"
            elif base >= 0.2:
                lab = "业绩催化 · B级增长"
            elif base >= -0.1:
                lab = "业绩催化 · C级平稳"
            else:
                lab = "业绩下滑 · 利空兑现"
            out.append({"v": "V7", "name": "催化", "label": lab, "conf": "data",
                        "evidence": det + "(公告/龙虎榜事件链待补)"})
        else:                                        # 财报已远 / YoY 缺 → 无近期业绩催化(其它事件未覆盖→proxy)
            out.append({"v": "V7", "name": "催化", "label": "无近期业绩催化", "conf": "proxy",
                        "evidence": f"距上次财报({ann}){cdays}天,无新业绩催化(公告/龙虎榜事件链待补)"})
    else:
        out.append({"v": "V7", "name": "催化", "label": "催化日历需材料", "conf": "gap",
                    "evidence": "缺事件日历(S/A/B/C 级催化链)"})

    # V8 资金(谁在主导)—— 龙虎榜真归因优先,否则市值层代理
    p8 = _v8_capital(s, m)
    out.append({"v": "V8", "name": "资金", **p8})

    # V9 比较(相对强度)—— 真数据(同业候选内)
    if v9 and v9.get("n", 0) >= 2 and v9.get("pct") is not None:
        pc = round(v9["pct"] * 100)
        verdict = "同业占优(补涨钝化)" if pc >= 70 else ("同业偏弱(调整更充分)" if pc <= 30 else "同业居中")
        out.append({"v": "V9", "name": "比较", "label": verdict, "conf": "data",
                    "evidence": f"60 日涨幅在同业 {v9['n']} 只候选内分位 {pc}%"})
    else:
        out.append({"v": "V9", "name": "比较", "label": "同业样本不足", "conf": "gap",
                    "evidence": "同业候选 <2,无法相对比较"})

    # V10 执行纪律(元视角)—— gap(纯纪律,无个股数据;L5 给出分批/波动建议)
    out.append({"v": "V10", "name": "执行", "label": "分批 · 波动容忍(见 L5 操作)", "conf": "gap",
                "evidence": "好票回撤≈涨幅 30-50%;建仓前定可接受回撤,分批不一锤子"})
    return out
