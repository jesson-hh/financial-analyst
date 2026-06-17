# -*- coding: utf-8 -*-
"""L5 决策层 —— 五维评级 → 仓位档 + 3 护盾 + ≤5 收敛。

把 L1-L4 的"清单"收敛成"持仓"。规则全部来自 vendored ``rating_system.md``:

- **5 档持仓**(总分评级表):≥6 ★★★★★ 重仓 / 4-5 ★★★★ 标准 / 2-3 ★★★☆ 轻仓 /
  0-1 ★★★ 观望 / -2~-1 ★★☆ 减仓 / ≤-3 ★★ 清仓。
- **护盾 v4.1**(V3 just_switched 金信号):mainline_golden → 评级强制下限 ★★★★,操作禁清仓、
  必分批留 1/3、持有≥1 月;例外(铁底)= RSI>90 / 单日≤-10%(chip>95% 无数据,标注)。
- **护盾 v4.2**(V4 仕佳 4 要素):a 涨幅≥30% + b 催化兑现完 + c V5 反应弱 + d V3 退潮,
  **4 要素须同时满足**才判仕佳降级。本期仅 a/d 可由数据算(b/c 缺 → gap),故**永不硬降级**,
  只出"仕佳风险警示"(playbook 红线:勿用滞后单维否决前瞻,汉缆/立昂微教训)。
- **护盾 v4.3**(涨停日 5 重共振):仅对涨停股适用;当前候选非涨停 → 休眠(同 board_scorer 之窄)。

纯计算,不读数据文件;输入是 L1-L4 已组装的候选 dict。决策只"定",不"看"(看在 perspectives.py)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 总分评级表(rating_system.md「总分评级」),band=仓位区间(%)。stars 用 0.5 表示半星(☆)。
# 阈值降序匹配:total ≥ thr → 该档。
_RATING_TABLE = [
    (6,  5.0, "★★★★★", "强烈看多", "重仓", 25, 35),
    (4,  4.0, "★★★★",  "偏多",     "标准", 15, 20),
    (2,  3.5, "★★★☆",  "中性偏多", "轻仓", 8,  12),
    (0,  3.0, "★★★",    "中性",     "观望", 0,  5),
    (-2, 2.5, "★★☆",    "中性偏空", "减仓", 0,  0),
]
_RATING_FLOOR = (2.0, "★★", "偏空", "清仓", 0, 0)   # total ≤ -3

# 金信号护盾下限(★★★★)
_GOLDEN_FLOOR_STARS = 4.0


def rate_v4(v4_total: Optional[float]) -> Dict[str, Any]:
    """五维总分 → 评级档(stars/stars_str/label/band)。total 缺失 → 中性档(观望)。"""
    if v4_total is None:
        t = 0.0
        unknown = True
    else:
        t = float(v4_total)
        unknown = False
    for thr, stars, sstr, label, tier, lo, hi in _RATING_TABLE:
        if t >= thr:
            return {"stars": stars, "stars_str": sstr, "label": label,
                    "band": {"tier": tier, "lo": lo, "hi": hi}, "v4_total": (None if unknown else t)}
    stars, sstr, label, tier, lo, hi = _RATING_FLOOR
    return {"stars": stars, "stars_str": sstr, "label": label,
            "band": {"tier": tier, "lo": lo, "hi": hi}, "v4_total": (None if unknown else t)}


def _stars_str(stars: float) -> str:
    full = int(stars)
    return "★" * full + ("☆" if (stars - full) >= 0.5 else "")


# 分位制评级表(评级池内相对强弱;阈值=池内分位下限)。背景:v4_total 绝对阈值 ≥6 在顶200
# 内几乎人人达标 → 星级失去区分度(2026-06-10 诊断)。分位制让 ★ 重新携带信息:
# 前10% ★★★★★ / 10-30% ★★★★ / 30-60% ★★★☆ / 60-85% ★★★ / 后15% ★★☆。
# 仓位档沿用 _RATING_TABLE 同星档(语义=池内分位,UI 标注口径)。
_PCT_RATING_TABLE = [
    (0.90, 5.0, "★★★★★", "池内前10%", "重仓", 25, 35),
    (0.70, 4.0, "★★★★",  "池内前30%", "标准", 15, 20),
    (0.40, 3.5, "★★★☆",  "池内中段",   "轻仓", 8,  12),
    (0.15, 3.0, "★★★",    "池内偏后",   "观望", 0,  5),
    (-1.0, 2.5, "★★☆",    "池内末15%", "减仓", 0,  0),
]


def rate_from_pool_pct(pool_pct: float, v4_total: Optional[float] = None) -> Dict[str, Any]:
    """池内分位 → 评级档(与 rate_v4 同形 dict,可作 apply_shields 的 base 注入)。

    ``pool_pct`` ∈ [0,1] = 在当日评级池(v4_total 非空集合)内按 (v4_total, lgb_pct)
    字典序的分位(1=最强)。绝对阈值版 rate_v4 保留不动(其他消费方/旧语义不受扰)。"""
    p = max(0.0, min(1.0, float(pool_pct)))
    for thr, stars, sstr, label, tier, lo, hi in _PCT_RATING_TABLE:
        if p >= thr:
            return {"stars": stars, "stars_str": sstr, "label": label,
                    "band": {"tier": tier, "lo": lo, "hi": hi},
                    "v4_total": (None if v4_total is None else float(v4_total)),
                    "pool_pct": round(p, 4)}
    raise AssertionError("unreachable")  # 表底 -1.0 必命中


def apply_shields(s: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None,
                  base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对单候选套 3 护盾,返回 ``{base, stars, stars_str, label, band, shields, notes}``。

    ``base`` = 原始评级:缺省 rate_v4(绝对阈值,旧语义);调用方可注入预算好的分位制评级
    (rate_from_pool_pct,选股页 2.0 用,解决顶200全5星无区分度)。``stars`` = 护盾后评级。
    护盾只能**上调下限**(金信号)或**出风险警示**(仕佳/涨停),不在数据不全时硬降级(playbook 红线)。
    """
    m = metrics or {}
    if base is None:
        base = rate_v4(s.get("v4_total"))
    stars = base["stars"]
    label = base["label"]
    band = dict(base["band"])
    shields: List[Dict[str, Any]] = []
    notes: List[str] = []

    # —— 护盾 v4.1 金信号(强制下限 ★★★★)——
    if s.get("mainline_golden"):
        rsi = m.get("rsi")
        chg1 = s.get("chg")   # 当日涨跌(%)
        iron = []
        if rsi is not None and rsi > 90:
            iron.append(f"RSI {round(rsi)}>90")
        if chg1 is not None and chg1 <= -10:
            iron.append(f"单日 {round(chg1,1)}%≤-10%")
        if iron:
            # rating_system.md v4.1「例外情形」:铁底信号下评级可下调,但下限仍是 ★★★(非取消下限)
            if stars < 3.0:
                stars = 3.0
                r = rate_v4(0.0)   # 借 ★★★ 观望档取标签/仓位区间
                label, band = r["label"], dict(r["band"])
            shields.append({"id": "v4.1", "name": "金信号护盾", "level": "exception",
                            "text": f"金信号触发,但铁底例外({' / '.join(iron)})→ 可下调至 ★★★(下限);chip>95% 无数据"})
            notes.append("V3 金信号护盾被技术面铁底信号触发例外(评级下限 ★★★)")
        else:
            if stars < _GOLDEN_FLOOR_STARS:
                stars = _GOLDEN_FLOOR_STARS
                r = rate_v4(4.0)   # 借 ★★★★ 档取仓位区间/标签
                label, band = r["label"], dict(r["band"])
            shields.append({"id": "v4.1", "name": "金信号护盾", "level": "floor",
                            "text": "上月启动→本月主线(fwd_60d +5.54pp 胜率87%):评级下限★★★★,禁清仓,必分批留1/3,持有≥1月"})
            notes.append("金信号:操作必 V10c 分批、留 ≥1/3 仓让 V3 兑现")

    # —— 护盾 v4.2 仕佳风险(4 要素,仅 a/d 可算 → 只警示不降级)——
    ret60 = m.get("ret60")
    elem_a = (ret60 is not None and ret60 >= 0.30)
    elem_d = (s.get("mainline") in ("cold", "decay"))
    if elem_a and elem_d and not s.get("mainline_golden"):
        distr = s.get("vol_regime") in ("distr", "super_distr")
        confirmed = "a(涨幅≥30%)✓ d(板块退潮)✓" + (" · 量能派发✓" if distr else "")
        shields.append({"id": "v4.2", "name": "仕佳风险", "level": "warn",
                        "text": f"4 要素中 {confirmed};b(催化兑现)/c(V5反应)需材料 → 未达 4/4,不降级,"
                                + ("派发证据加重警示" if distr else "保持观察")
                                + ";未核 V8 控盘方向(若 VR>5+OBV升+MFI>70 吸筹则本警示作废,rating_system v4.2 强制项)"})
        notes.append("仕佳风险:满足部分要素,买前补 V5 反应 + 催化兑现度再判")

    # —— 护盾 v4.3 涨停日 5 重共振(仅涨停股适用)——
    if s.get("limit"):
        shields.append({"id": "v4.3", "name": "涨停5重", "level": "info",
                        "text": "涨停股:需查 筹码/主力/催化/板块/V5质量 5 维(缺 5min 微结构 → 仅板块维可判)"})

    return {"base": base, "stars": stars, "stars_str": _stars_str(stars),
            "label": label, "band": band, "shields": shields, "notes": notes}


def converge(rows: List[Dict[str, Any]], metrics_by_code: Optional[Dict[str, Dict]] = None,
             max_n: int = 5, base_by_code: Optional[Dict[str, Dict]] = None) -> Dict[str, Any]:
    """≤5 收敛 —— 从入选清单挑 ≤max_n 持仓(评级 ★★★★+,行业去重,带仓位档 + 操作)。

    ``rows`` = chosen 列表(每项含 ``s``);``metrics_by_code`` = code→metrics(算护盾用);
    ``base_by_code``(可选)= code→分位制基础评级(rate_from_pool_pct;缺省走 rate_v4 旧语义)。
    返回 ``{final, n_actionable, notes}``。final 每项 = {code,name,ind,stars,stars_str,band,shields,op}。
    """
    mbc = metrics_by_code or {}
    bbc = base_by_code or {}
    enriched = []
    for x in rows:
        s = x.get("s", {})
        dec = apply_shields(s, mbc.get(s.get("code")), base=bbc.get(s.get("code")))
        enriched.append((x, s, dec))

    # 可执行 = 护盾后 ≥ ★★★★(标准仓位档及以上)
    actionable = [(x, s, d) for (x, s, d) in enriched if d["stars"] >= 4.0]
    actionable.sort(key=lambda t: (t[2]["stars"], (t[1].get("v4_total") or -99)), reverse=True)

    final: List[Dict[str, Any]] = []
    seen_ind = set()
    for x, s, d in actionable:
        if len(final) >= max_n:
            break
        ind = s.get("ind", "—")
        if ind in seen_ind:                 # 行业去重:每业仅取最优 1 只(集中度护盾)
            continue
        seen_ind.add(ind)
        golden = any(sh["id"] == "v4.1" and sh["level"] == "floor" for sh in d["shields"])
        op = (("分批建仓:首仓 1/3,回撤加仓;留 ≥1/3 持有 ≥1 月(金信号)"
               if golden else
               "分批建仓:首仓 1/3,回撤 X% 加 1/3,不一锤子;定可接受回撤再动手"))
        final.append({
            "code": s.get("code"), "name": s.get("name"), "ind": ind,
            "stars": d["stars"], "stars_str": d["stars_str"], "label": d["label"],
            "band": d["band"], "v4_total": s.get("v4_total"),
            "mainline": s.get("mainline"), "mainline_golden": bool(s.get("mainline_golden")),
            "shields": d["shields"], "op": op,
        })

    notes = [
        "收敛规则:护盾后评级 ★★★★+ → 行业去重 → 取前 ≤5(集中度护盾)",
        "V10 执行:好票回撤≈涨幅 30-50%,分批不一锤子;短线做辅助、中线大波段做主",
    ]
    if bbc:
        notes.insert(0, "评级口径:当日评级池内分位(前10% ★★★★★ / 前30% ★★★★)——相对强弱,非绝对档")
    return {"final": final, "n_actionable": len(actionable), "notes": notes}
