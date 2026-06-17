"""TDX F10 本地语料:确定性解析 + PIT。唯一读 G:\\stocks F10 的地方。

设计文档:docs/superpowers/specs/2026-06-16-weiwo-f10-report-enrichment-design.md
所有数字/日期走确定性抽取,LLM 不碰。诚实降级,绝不伪造。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# 跨仓默认路径(可经环境变量覆盖)
CORPUS_ROOT = Path(os.environ.get("GL_F10_ROOT", r"G:\stocks\news_data\tdx_f10"))
INDEX_PATH = Path(os.environ.get("GL_F10_INDEX", r"G:\stocks\stock_data\parquet\tdx_f10_index.parquet"))

_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")
_NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(亿|万|%)?")


def _num(s: Optional[str]) -> Optional[float]:
    """'134.0947亿'->1.340947e10, '177.86万'->1778600, '3.59%'->3.59, '-'/''->None。"""
    s = (s or "").strip()
    if not s or s in {"-", "－", "—"}:
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit == "亿":
        v = round(v * 1e8, 4)
    elif unit == "万":
        v = round(v * 1e4, 4)
    return v  # % 直接返回百分数本身(如 3.59)


def _cells(line: str) -> List[str]:
    """按全角/半角竖线切单元,去首尾空单元。"""
    parts = re.split(r"[｜|]", line)
    out = [p.strip() for p in parts]
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return out


def _find_date(s: str) -> Optional[str]:
    m = _DATE_RE.search(s or "")
    return m.group(1) if m else None


def _visible_date(period: str) -> str:
    """季报 报告期 -> 标准披露截止日(防回测看未披露财报)。"""
    y, m, d = period.split("-")
    key = (m, d)
    if key == ("03", "31"):
        return f"{y}-04-30"
    if key == ("06", "30"):
        return f"{y}-08-31"
    if key == ("09", "30"):
        return f"{y}-10-31"
    if key == ("12", "31"):
        return f"{int(y) + 1}-04-30"
    return period


_VAL_LABELS = {
    "每股收益": "eps",
    "每股净资产": "bvps",
    "净资产收益率": "roe",
    "总股本": "total_shares",
    "流通A股": "float_shares",
}


def _parse_valuation(text: str, asof: Optional[str]) -> Optional[Dict[str, Any]]:
    lines = text.splitlines()
    periods: List[str] = []
    by_metric: Dict[str, Dict[str, Optional[float]]] = {}
    rev_by_period: Dict[str, Dict[str, Optional[float]]] = {}

    for ln in lines:
        if "最新主要指标" in ln:
            periods = [d for c in _cells(ln) if (d := _find_date(c))]
            continue
        cells = _cells(ln)
        if periods and cells:
            label = cells[0]
            key = next((v for k, v in _VAL_LABELS.items() if label.startswith(k)), None)
            if key:
                vals = [_num(c) for c in cells[1:1 + len(periods)]]
                by_metric[key] = dict(zip(periods, vals))
        d = _find_date(ln)
        if d and "营业总收入" in ln:
            rev = rev_by_period.setdefault(d, {})
            m = re.search(r"营业总收入\(元\):([\d.]+)亿", ln)
            y = re.search(r"营业总收入.*?同比增(-?[\d.]+)%", ln)
            if m:
                rev["revenue"] = round(float(m.group(1)) * 1e8, 4)
            if y:
                rev["revenue_yoy"] = float(y.group(1))
        if d and "净利润(元)" in ln:
            rev = rev_by_period.setdefault(d, {})
            m = re.search(r"净利润\(元\):([\d.]+)亿", ln)
            y = re.search(r"净利润.*?同比增(-?[\d.]+)%", ln)
            if m:
                rev["net_profit"] = round(float(m.group(1)) * 1e8, 4)
            if y:
                rev["net_profit_yoy"] = float(y.group(1))

    if not periods:
        return None

    def visible(p: str) -> bool:
        return asof is None or _visible_date(p) <= asof

    eligible = [p for p in periods if visible(p)]
    if not eligible:
        return None
    target = max(eligible)   # 'YYYY-MM-DD' 字符串可比

    out: Dict[str, Any] = {"report_period": target}
    for key, series in by_metric.items():
        out[key] = series.get(target)
    rev = rev_by_period.get(target, {})
    for k in ("revenue", "revenue_yoy", "net_profit", "net_profit_yoy"):
        out[k] = rev.get(k)
    return out


def _parse_events(text: str, category: str, asof: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ln in text.splitlines():
        cells = _cells(ln)
        if len(cells) < 2:
            continue
        d = _find_date(cells[0])
        if not d or _find_date(cells[1]):   # 第二格也是日期 -> 数据表行,非事件
            continue
        title = cells[1].strip()
        if not title or title in {"评级机构", "预测机构"}:
            continue
        if asof is not None and d > asof:
            continue
        out.append({"date": d, "title": title, "category": category})
    out.sort(key=lambda e: e["date"], reverse=True)
    return out


def _parse_broker(text: str, asof: Optional[str]) -> Dict[str, Any]:
    """只取含'目标价格'那张评级表。列:发生日期|评级机构|本期|上期|报告日价格|目标价格。"""
    ratings: List[Dict[str, Any]] = []
    in_table = False
    for ln in text.splitlines():
        cells = _cells(ln)
        if "发生日期" in ln and "目标价格" in ln:
            in_table = True
            continue
        if in_table:
            if "｜" not in ln and "|" not in ln:
                continue          # 空行 / box 分隔线(├──┼──┤)-> 跳过,不结束表
            d = _find_date(cells[0]) if cells else None
            if not d:
                in_table = False  # ｜行但首格非日期 = 下一子表/表头,结束本表
                continue
            if len(cells) < 6:
                continue          # 畸形行跳过,不结束表
            if asof is not None and d > asof:
                continue
            ratings.append({
                "date": d,
                "org": cells[1],
                "rating": None if cells[2] in {"-", ""} else cells[2],
                "prev": None if cells[3] in {"-", ""} else cells[3],
                "report_price": _num(cells[4]),
                "target_price": _num(cells[5]),
            })
    ratings.sort(key=lambda r: r["date"], reverse=True)
    return {"ratings": ratings}


_ABNORMAL_RE = re.compile(
    r"【交易日期】(20\d\d-\d\d-\d\d).*?振幅:([\d.]+)%.*?成交量:([\d.]+)亿股.*?成交金额:([\d.]+)亿元"
)


def _parse_lhb(text: str, asof: Optional[str]) -> Dict[str, Any]:
    """龙虎榜单:§1 融资融券日表 + §2 资金流向 + §3 涨跌幅异动。

    §1 列:交易日期|融资余额|融资买入额|融券余额|融券卖出量|融资融券余额。
    §2 列:日期|主力净额(净额|净占比)|超大单(净额|净占比)|大单(净额|净占比)。
    §3 自由行:【交易日期】... 振幅:..%  成交量:..亿股 成交金额:..亿元(仅 `振幅:` 行)。
    §4 大宗交易:真文件常无独立数据段 -> 诚实空 + provenance 记录,绝不伪造。
    """
    margin: List[Dict[str, Any]] = []
    moneyflow: List[Dict[str, Any]] = []
    abnormal: List[Dict[str, Any]] = []
    notes: List[str] = []
    in_margin = False
    in_money = False
    for ln in text.splitlines():
        cells = _cells(ln)
        # §1 融资融券表头
        if "交易日期" in ln and "融资余额" in ln:
            in_margin, in_money = True, False
            continue
        # §2 资金流向子表头行(日期｜净额(元)｜净占比(%)｜...);组标题"主力净额"在上一行
        if "净额(元)" in ln and "净占比" in ln:
            in_margin, in_money = False, True
            continue
        # §3 涨跌幅异动(自由行,不属任何表)
        am = _ABNORMAL_RE.search(ln)
        if am:
            in_margin = in_money = False
            d = am.group(1)
            if asof is not None and d > asof:
                continue
            abnormal.append({
                "date": d,
                "amplitude_pct": float(am.group(2)),
                "volume": round(float(am.group(3)) * 1e8, 4),
                "amount": round(float(am.group(4)) * 1e8, 4),
            })
            continue
        if in_margin:
            if "｜" not in ln and "|" not in ln:
                continue          # 空行 / box 分隔线(├──┼──┤)-> 跳过,不结束表
            d = _find_date(cells[0]) if cells else None
            if not d:
                in_margin = False  # ｜行但首格非日期 = 下一子表/表头,结束本表
                continue
            if len(cells) < 6:
                continue          # 畸形行跳过,不结束表
            if asof is not None and d > asof:
                continue
            margin.append({
                "date": d,
                "margin_balance": _num(cells[1]),
                "margin_buy": _num(cells[2]),
                "short_balance": _num(cells[3]),
                "short_sell_vol": _num(cells[4]),
                "total_balance": _num(cells[5]),
            })
        elif in_money:
            if "｜" not in ln and "|" not in ln:
                continue          # 空行 / box 分隔线 -> 跳过,不结束表
            d = _find_date(cells[0]) if cells else None
            if not d:
                in_money = False
                continue
            if len(cells) < 7:    # 日期 + 3 组(净额,净占比)
                continue
            if asof is not None and d > asof:
                continue
            moneyflow.append({
                "date": d,
                "main_net": _num(cells[1]),
                "main_pct": _num(cells[2]),
                "super_net": _num(cells[3]),
                "super_pct": _num(cells[4]),
                "big_net": _num(cells[5]),
                "big_pct": _num(cells[6]),
            })
    margin.sort(key=lambda r: r["date"], reverse=True)
    moneyflow.sort(key=lambda r: r["date"], reverse=True)
    abnormal.sort(key=lambda r: r["date"], reverse=True)
    # §4 大宗交易:本地 F10 龙虎榜文件无独立大宗交易数据段(仅栏目索引提及)
    notes.append("§4大宗交易:本地F10无独立数据段,未解析(诚实空)")
    return {"margin": margin, "moneyflow": moneyflow, "block_trades": [], "abnormal": abnormal, "notes": notes}


def _parse_holders(text: str, asof: Optional[str]) -> Optional[Dict[str, Any]]:
    """股东研究:控股股东/实控人(§1)+ 十大流通股东(§4 首张表)。

    照抄 _parse_broker idiom:in_table 内跳空行/box 分隔(无 ｜ -> continue);
    持股数单元为空 = 折行续行 -> 跳过;首格非名字或表尾 -> 结束。PIT 按披露滞后。
    """
    lines = text.splitlines()
    controlling: Optional[str] = None
    actual_ctrl: Optional[str] = None
    report_date: Optional[str] = None
    a_share_holders: Optional[float] = None
    top: List[Dict[str, Any]] = []

    in_table = False
    captured = False  # 只取第一张流通股东表
    for ln in lines:
        cells = _cells(ln)
        # §1 控股股东 / 实际控制人(两行单元表)
        if cells and cells[0].startswith("控股股东") and len(cells) >= 2:
            controlling = cells[1]
            continue
        if cells and cells[0].startswith("实际控制人") and len(cells) >= 2:
            actual_ctrl = cells[1]
            continue
        # §4 抬头自由行:截至日期 + 十大流通股东 + A股户数
        if "截至日期" in ln and "流通股东" in ln and not captured:
            report_date = _find_date(ln)
            m = re.search(r"A股户数:([\d.]+)万", ln)
            if m:
                a_share_holders = round(float(m.group(1)) * 1e4, 4)
            continue
        # 流通股东表表头:含 股东名称 + 占流通股比
        if "股东名称" in ln and "占流通股比" in ln and report_date and not captured:
            in_table = True
            continue
        if in_table:
            if "｜" not in ln and "|" not in ln:
                continue          # 空行 / box 分隔线 -> 跳过,不结束表
            if not cells:
                in_table = False
                captured = True
                continue
            shares = _num(cells[1]) if len(cells) >= 2 else None
            if shares is None:
                # 折行续行(持股数空)-> 跳过;但首格非名字/表尾标记 -> 结束
                if len(cells) < 2:
                    continue
                in_table = False
                captured = True
                continue
            name = cells[0]
            pct = None
            if len(cells) >= 3:
                pm = re.match(r"(-?\d+(?:\.\d+)?)", cells[2].strip())
                pct = float(pm.group(1)) if pm else None
            top.append({"name": name, "shares": shares, "pct": pct})

    if report_date is None and controlling is None:
        return None
    if asof is not None and report_date is not None and _visible_date(report_date) > asof:
        return None

    return {
        "report_date": report_date,
        "a_share_holders": a_share_holders,
        "controlling_holder": controlling,
        "actual_controller": actual_ctrl,
        "top_holders": top,
    }


def _parse_main_capital(text: str, asof: Optional[str]) -> Optional[Dict[str, Any]]:
    """主力追踪:§1 机构持股汇总(期列表表)+ §2 股东户数变化(行表)。

    §1:抓 `报告日期` 行得期;每 label 行对齐期值;含 `未完/更新中` 的单元 ->
    该期标不完整跳过。选 _visible_date(p)<=asof 且完整的最近期。
    §2:逐行 {date, count(万→×1e4), change_pct},PIT 裁 date>asof,倒序。
    """
    lines = text.splitlines()
    periods: List[str] = []
    by_label: Dict[str, List[str]] = {}   # label -> per-period raw cells
    trend: List[Dict[str, Any]] = []
    in_trend = False

    _MC_LABELS = {
        "机构数量": "inst_count",
        "累计持仓比例": "inst_holding_pct",
        "基金持仓比例": "fund_holding_pct",
    }

    for ln in lines:
        # "0.01%|未完" / "X|更新中" 用半角竖线 | 标记未完成,会被 _cells 误切成两格→列错位。
        # 先把标记前的半角竖线并掉(0.01%|未完 -> 0.01%未完),complete() 仍能识别"未完/更新中"。
        cells = _cells(re.sub(r"\|(未完|更新中)", r"\1", ln))
        if "报告日期" in ln:
            periods = [d for c in cells if (d := _find_date(c))]
            continue
        if periods and cells:
            label = cells[0]
            key = next((v for k, v in _MC_LABELS.items() if label.startswith(k)), None)
            if key:
                by_label[key] = cells[1:1 + len(periods)]
        # §2 户数变化行表
        if "截止日期" in ln and "股东户数" in ln:
            in_trend = True
            continue
        if in_trend:
            if "｜" not in ln and "|" not in ln:
                continue
            d = _find_date(cells[0]) if cells else None
            if not d:
                in_trend = False
                continue
            if len(cells) < 4:
                continue
            if asof is not None and d > asof:
                continue
            cnt = _num(cells[1])
            chg = _num(cells[3])
            trend.append({"date": d, "count": cnt, "change_pct": chg})

    out: Dict[str, Any] = {}
    # §1 选可见 + 完整的最近期
    target = None
    if periods:
        def complete(idx: int) -> bool:
            for raw in by_label.values():
                if idx < len(raw) and re.search(r"未完|更新中", raw[idx]):
                    return False
            return True

        def visible(p: str) -> bool:
            return asof is None or _visible_date(p) <= asof

        eligible = [(p, i) for i, p in enumerate(periods) if visible(p) and complete(i)]
        if eligible:
            p, idx = max(eligible, key=lambda x: x[0])
            target = p
            out["report_period"] = p
            ic = by_label.get("inst_count")
            out["inst_count"] = int(_num(ic[idx])) if ic and idx < len(ic) and _num(ic[idx]) is not None else None
            ihp = by_label.get("inst_holding_pct")
            out["inst_holding_pct"] = _num(ihp[idx]) if ihp and idx < len(ihp) else None
            fhp = by_label.get("fund_holding_pct")
            out["fund_holding_pct"] = _num(fhp[idx]) if fhp and idx < len(fhp) else None

    trend.sort(key=lambda r: r["date"], reverse=True)
    out["holder_count_trend"] = trend

    if target is None and not trend:
        return None
    out.setdefault("report_period", None)
    out.setdefault("inst_count", None)
    out.setdefault("inst_holding_pct", None)
    out.setdefault("fund_holding_pct", None)
    return out


_CAT_PARSERS = {
    "最新提示": "valuation",
    "公司大事": "events",
    "业内点评": "events",
    "研究报告": "broker",
    "龙虎榜单": "lhb",
    "股东研究": "holders",
    "主力追踪": "main_capital",
}


def _norm_code(code: str) -> str:
    s = code.strip().upper()
    if re.fullmatch(r"\d{6}", s):
        s = ("SH" if s[0] in "6859" else "SZ") + s
    return s


@dataclass
class F10Facts:
    code: str
    asof: Optional[str] = None
    snapshot_date: Optional[str] = None
    valuation: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    broker: Dict[str, Any] = field(default_factory=lambda: {"ratings": []})
    lhb: Dict[str, Any] = field(default_factory=lambda: {"margin": [], "moneyflow": [], "block_trades": [], "abnormal": [], "notes": []})
    holders: Optional[Dict[str, Any]] = None
    main_capital: Optional[Dict[str, Any]] = None
    provenance: List[Dict[str, Any]] = field(default_factory=list)
    honest_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def locate(code: str, *, root=None) -> Optional[Dict[str, Any]]:
    """glob {root}/{code小写}/*.txt -> {category: (path, snapshot_date)},每类取最新快照。"""
    base = Path(root) if root else CORPUS_ROOT
    cdir = base / _norm_code(code).lower()
    if not cdir.exists():
        return None
    found: Dict[str, tuple] = {}
    for p in cdir.glob("*.txt"):
        stem = p.stem
        if "_" not in stem:
            continue
        cat, date = stem.rsplit("_", 1)
        if not re.fullmatch(r"\d{8}", date):
            continue
        prev = found.get(cat)
        if prev is None or date > prev[1]:
            found[cat] = (str(p), date)
    return found or None


def load_facts(code: str, asof: Optional[str] = None, *, root=None) -> F10Facts:
    norm = _norm_code(code)
    facts = F10Facts(code=norm, asof=asof)
    snap = locate(code, root=root)
    if not snap:
        facts.honest_note = f"F10 无此股({norm})语料"
        return facts

    snap_dates = sorted({d for _, d in snap.values()}, reverse=True)
    facts.snapshot_date = snap_dates[0] if snap_dates else None

    for cat, (path, sdate) in snap.items():
        kind = _CAT_PARSERS.get(cat)
        if not kind:
            continue
        try:
            txt = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            facts.provenance.append({"category": cat, "snapshot_date": sdate, "error": str(exc)[:80]})
            continue
        try:
            if kind == "valuation":
                facts.valuation = _parse_valuation(txt, asof)
            elif kind == "events":
                facts.events.extend(_parse_events(txt, cat, asof))
            elif kind == "broker":
                facts.broker = _parse_broker(txt, asof)
            elif kind == "lhb":
                facts.lhb = _parse_lhb(txt, asof)
            elif kind == "holders":
                facts.holders = _parse_holders(txt, asof)
            elif kind == "main_capital":
                facts.main_capital = _parse_main_capital(txt, asof)
            facts.provenance.append({"category": cat, "snapshot_date": sdate})
        except Exception as exc:  # noqa: BLE001  解析失败不拖垮整体
            facts.provenance.append({"category": cat, "snapshot_date": sdate, "error": f"{type(exc).__name__}: {str(exc)[:80]}"})

    facts.events.sort(key=lambda e: e["date"], reverse=True)
    if asof and not (facts.valuation or facts.events or facts.broker["ratings"] or facts.lhb["margin"]):
        facts.honest_note = f"asof {asof} 早于 F10 快照内容,无可用料"
    return facts
