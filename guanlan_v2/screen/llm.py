# -*- coding: utf-8 -*-
"""选股 L4/L5 定性点评 —— 真 LLM(deepseek,经引擎 LLMClient)。

把 L5 收敛出的 ≤5 持仓 + L4 九视角确定性读数喂给真模型,按《分析师视角手册》九视角做
**定性点评**:补足确定性层判不出的视角(V5 消息反应 / V7 催化 / V9 比较的定性面),给
Bull / Bear / 投委会综合 + V10 操作。这是选股链路里**唯一**的真 LLM 调用(其余「LLM」皆演示)。

红线 / 诚实:
- 模型**无实时行情/新闻**,系统提示强制**禁止编造具体数字、涨跌幅、新闻事件、日期**;只能基于
  传入的结构化事实(评级/主线/位置/护盾)+ 行业框架知识做**框架性推断**,并标注「需盘面/材料确认」。
- 真失败(网络/key/超时)→ ``{ok:False, reason}``,**不伪造**点评(UI 诚实显示错误)。
- 配置走仓内 ``config/llm.yaml``(deepseek);**显式传 config_path**,否则引擎 workspace 查找会
  命中旧 qwen 配置(key 过期)。不改 engine/,只调其稳定 ``LLMClient`` 接口。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 仓内 deepseek 配置(显式优先于引擎 workspace 查找,避开旧 qwen)
LLM_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "llm.yaml"

_SYSTEM = (
    "你是 A 股盘后复盘分析师,严格按《分析师视角手册》九视角思维:"
    "V1节奏/V2梯队/V3强度/V4位置/V5反应/V6共振/V7催化/V8资金/V9比较/V10执行。"
    "下面给你今日量化系统收敛出的 ≤5 只持仓,每只已带**确定性**的 L4 读数(实=真数据/代=代理/缺=待补)"
    "与 L5 五维评级/护盾。你的任务是**定性点评**,补足确定性层判不出的视角。\n"
    "【硬约束 · 诚实】你没有实时行情/新闻/龙虎榜。**严禁编造**任何具体数字(涨跌幅/价格/成交额/北向额)、"
    "具体新闻事件、具体日期或财报数据。只能基于给定的结构化事实(行业/主线状态/位置/评级/护盾)+ 通用行业"
    "框架知识做**框架性推断**,凡前瞻判断必须缀『需盘面/材料确认』。宁可说『待确认』,绝不杜撰。\n"
    "【输出】严格输出 JSON 对象,不要任何额外文字,形如:"
    '{"market_read":"对市场节奏(V1)的一句话定性","holdings":['
    '{"code":"...","bull":"一句看多(挂 V# 视角)","bear":"一句看空(挂 V# 视角)",'
    '"synth":"投委会综合,矛盾按分仓分时解","op":"V10 操作提示(分批/波动容忍/护盾约束)"}]}'
)


def _fmt_views(views: List[Dict[str, Any]]) -> str:
    """把九视角读数压成一行(只给模型已知的实/代/缺,供其补缺)。"""
    if not views:
        return "(无九视角读数)"
    parts = []
    for v in views:
        parts.append(f"{v.get('v')}{v.get('name')}[{v.get('conf')}]{v.get('label')}")
    return " · ".join(parts)


def build_prompt(final: List[Dict[str, Any]], market: Optional[Dict[str, Any]]) -> str:
    """纯函数:把 ≤5 持仓 + 市场节奏拼成 user prompt(可单测,不触网)。"""
    lines: List[str] = []
    if market:
        lines.append(f"市场节奏(V1):{market.get('stage')}(涨停残差60日分位 "
                     f"{market.get('lu_pct60')},as_of {market.get('as_of')};口径可能偏旧,据此定性勿当今日实况)")
    else:
        lines.append("市场节奏(V1):无数据")
    lines.append(f"\n持仓(共 {len(final)} 只,按评级排序):")
    for i, f in enumerate(final, 1):
        band = f.get("band") or {}
        shields = "、".join(sh.get("name", "") for sh in (f.get("shields") or [])) or "无"
        ml = f.get("mainline") or "—"
        golden = "(★金信号)" if f.get("mainline_golden") else ""
        lines.append(
            f"{i}. {f.get('name','')}({f.get('code','')}) · 行业 {f.get('ind','—')} · "
            f"评级 {f.get('stars_str','')}{f.get('label','')} · 五维分 {f.get('v4_total')} · "
            f"建议仓位 {band.get('tier','')} {band.get('lo','')}-{band.get('hi','')}% · "
            f"主线 {ml}{golden} · 护盾 {shields}\n"
            f"   九视角读数:{_fmt_views(f.get('views') or [])}"
        )
    lines.append("\n请对每只票逐一点评,并先给一句市场节奏定性(market_read)。仅输出 JSON。")
    return "\n".join(lines)


def _effective_timeout(seat_timeout, fallback: float) -> float:
    """座席配置了 timeout(deep 档)→ 用它 +5s 缓冲(让 SDK per-request 超时先抛,错误信息更准);
    否则用调用方 fallback(旧行为逐字节不变)。"""
    return float(seat_timeout) + 5.0 if seat_timeout else float(fallback)


async def _call_llm_json(system: str, user: str, *, timeout: float = 45.0,
                         temperature: float = 0.2, agent: str = "screen") -> Dict[str, Any]:
    """共用:deepseek JSON 模式调用。成功 → {ok,data,model,tokens};失败 → {ok:False,reason}(不伪造)。

    显式传 LLM_CONFIG_PATH(deepseek),避开引擎 workspace 旧 qwen 配置。
    """
    try:
        from financial_analyst.llm.client import LLMClient
        client = LLMClient.for_agent(agent, config_path=LLM_CONFIG_PATH)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"LLM 配置/引擎加载失败:{type(exc).__name__}: {exc}"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    eff = _effective_timeout(getattr(client, "default_timeout", None), timeout)
    try:
        resp = await asyncio.wait_for(
            client.chat(messages, response_format={"type": "json_object"}, temperature=temperature),
            timeout=eff,
        )
        content = resp["choices"][0]["message"]["content"]
        data = json.loads(content)
    except asyncio.TimeoutError:
        return {"ok": False, "reason": f"LLM 超时(>{int(eff)}s)"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"LLM 返回非 JSON:{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:240]}"}
    return {"ok": True, "data": data, "model": f"{client.provider}/{client.model}",
            "tokens": client.total_tokens}


async def screen_commentary(final: List[Dict[str, Any]], market: Optional[Dict[str, Any]],
                            *, timeout: float = 60.0) -> Dict[str, Any]:
    """对 ≤5 持仓做真 LLM 定性点评。失败诚实返回 ok:False + reason(不伪造)。"""
    if not final:
        return {"ok": False, "reason": "无 ≤5 持仓可点评(决策层未收敛出 ★★★★+ 标的)"}

    r = await _call_llm_json(_SYSTEM, build_prompt(final, market), timeout=timeout, temperature=0.3)
    if not r["ok"]:
        return r
    data = r["data"] if isinstance(r["data"], dict) else {}
    holdings = data.get("holdings")
    return {
        "ok": True,
        "model": r["model"],
        "market_read": data.get("market_read"),
        "holdings": holdings if isinstance(holdings, list) else [],
        "tokens": r["tokens"],
        "note": "真 LLM 定性点评(deepseek);模型无实时行情,前瞻判断均需盘面/材料确认",
    }


# ═══════════ B:LLM 选因子 / 调约束(替换前端正则;失败前端回落本地正则)═══════════

# 固定因子库(legacy 兜底;LLM 只能从目录中挑选,不得发明)。
FACTOR_CATALOG: Dict[str, Dict[str, str]] = {
    "fa_reversal": {"short": "缩量反转", "cat": "价量", "desc": "超跌/震荡环境缩量企稳反转弹性,常作主 alpha"},
    "fa_north":    {"short": "北向动量", "cat": "资金", "desc": "北向资金回流确认资金面、过滤技术噪声提胜率,偏蓝筹/龙头"},
    "fa_pead":     {"short": "PEAD 漂移", "cat": "基本面", "desc": "财报超预期后约 60 日漂移,补基本面/成长/景气"},
    "fa_distrib":  {"short": "退潮风控", "cat": "风控", "desc": "高位放量滞涨惩罚层(方向为负),用于控回撤/防退潮"},
}


def _factor_catalog() -> Dict[str, Dict[str, str]]:
    """动态因子目录(选股页 2.0,~56 因子·11 族,supported only)→ {id:{short,cat,desc}};
    目录不可用时退 legacy 4 条(诚实降级)。"""
    try:
        from guanlan_v2.screen.catalog import FACTOR_DEFS
        out = {fid: {"short": m["short"], "cat": m.get("family", ""), "desc": m.get("desc", "")}
               for fid, m in FACTOR_DEFS.items() if m.get("expr")}
        return out or FACTOR_CATALOG
    except Exception:  # noqa: BLE001
        return FACTOR_CATALOG

_PICK_SYSTEM = (
    "你是 A 股量化选股**因子配置**助手。下面给因子库(含动量反转/估值/财务质量/成长/波动率/"
    "流动性/技术/情绪/规模/大盘共振/跟随等族),你**只能用这些 id,严禁发明新因子**。"
    "根据用户选股思路,挑选 2-5 个因子并配权重(0.1-2.0,主导因子权重更高),互补成组合"
    "(如 alpha 主线 + 质量/风控辅助;提到大盘/共振/跟随时用共振族),给每个所选因子一句中文理由。"
    "严格输出 JSON,不要任何额外文字:"
    '{"factors":[{"id":"fa_xxx","w":1.0}],"reasons":{"fa_xxx":"一句理由"},"summary":"一句话配置总览"}'
)
_PHRASE_SYSTEM = (
    "你是 A 股量化选股**约束调节**助手。把用户一句话调整意图翻译成约束补丁。"
    "可调字段及范围:topN(选股数量,整数 5-40)、industryNeutral(行业中性,布尔)、indCap(单行业上限,0.1-0.5)、"
    "liqMin(成交额下限·亿,0-50)、exclST/exclHalt/exclLimit/exclNew(剔除 ST/停牌/涨跌停/次新,布尔)、"
    "factors(可选,仅限因子库内 id)。**只输出发生变化的字段**,不要发明字段、不要回传未变字段。"
    "注意:indCap 仅在 industryNeutral=true 时生效——若用户意在『行业均衡/别扎堆/分散行业』,"
    "必须同时把 industryNeutral 设为 true(并可调 indCap)。"
    "严格输出 JSON:" '{"patch":{...},"hit":["简短中文变更标签"]}'
)


def _catalog_text() -> str:
    cat = _factor_catalog()
    return "因子库(按族):\n" + "\n".join(
        f"- {fid}({m['short']} · {m['cat']}):{m['desc']}" for fid, m in cat.items()
    )


def build_pick_prompt(prompt: str) -> str:
    """纯函数(可单测):选因子 user prompt。"""
    return _catalog_text() + "\n\n用户选股思路:" + (prompt or "").strip() + "\n\n仅输出 JSON。"


def build_phrase_prompt(phrase: str, cfg: Dict[str, Any]) -> str:
    """纯函数(可单测):调约束 user prompt(带当前约束摘要)。"""
    cur = (f"当前约束:选股数 topN={cfg.get('topN')} · 行业中性={cfg.get('industryNeutral')} · "
           f"单业上限 indCap={cfg.get('indCap')} · 成交额下限 liqMin={cfg.get('liqMin')}亿 · "
           f"剔除 ST={cfg.get('exclST')}/停牌={cfg.get('exclHalt')}/涨跌停={cfg.get('exclLimit')}/次新={cfg.get('exclNew')}")
    return (_catalog_text() + "\n\n" + cur + "\n\n用户调整意图:" + (phrase or "").strip()
            + "\n\n只回变化字段,仅输出 JSON。")


def _clamp(v, lo, hi, default=None, *, integer=False):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    f = max(lo, min(hi, f))
    return int(round(f)) if integer else f


def _validate_factors(raw) -> List[Dict[str, Any]]:
    """只保留因子库内 id + 钳权重 [0.1,2.0];去重;封顶 6 个(复合分稀释防呆)。"""
    cat = _factor_catalog()
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in (raw or []):
        if not isinstance(it, dict):
            continue
        fid = it.get("id")
        if fid in cat and fid not in seen:
            w = _clamp(it.get("w", 1.0), 0.1, 2.0, 1.0)
            out.append({"id": fid, "w": round(float(w), 1)})
            seen.add(fid)
        if len(out) >= 6:
            break
    return out


async def pick_factors(prompt: str, *, timeout: float = 45.0) -> Dict[str, Any]:
    """LLM 选因子(替换前端正则 llmPick)。失败 → ok:False(前端回落本地正则)。"""
    if not (prompt or "").strip():
        return {"ok": False, "reason": "空描述"}
    r = await _call_llm_json(_PICK_SYSTEM, build_pick_prompt(prompt), timeout=timeout, temperature=0.2)
    if not r["ok"]:
        return r
    data = r["data"] if isinstance(r["data"], dict) else {}
    factors = _validate_factors(data.get("factors"))
    if not factors:
        return {"ok": False, "reason": "模型未选出有效因子(已回落本地)"}
    reasons = {k: str(v) for k, v in (data.get("reasons") or {}).items() if k in _factor_catalog()}
    return {"ok": True, "model": r["model"], "factors": factors, "reasons": reasons,
            "summary": str(data.get("summary") or ""), "tokens": r["tokens"]}


_PHRASE_BOOL_KEYS = ("industryNeutral", "exclST", "exclHalt", "exclLimit", "exclNew")


async def parse_phrase(phrase: str, cfg: Optional[Dict[str, Any]] = None,
                       *, timeout: float = 45.0) -> Dict[str, Any]:
    """LLM 调约束(替换前端正则 parsePhrase)。返回校验后的 patch;失败 → ok:False(前端回落本地)。"""
    if not (phrase or "").strip():
        return {"ok": False, "reason": "空描述"}
    r = await _call_llm_json(_PHRASE_SYSTEM, build_phrase_prompt(phrase, cfg or {}),
                             timeout=timeout, temperature=0.1)
    if not r["ok"]:
        return r
    data = r["data"] if isinstance(r["data"], dict) else {}
    raw = data.get("patch") if isinstance(data.get("patch"), dict) else {}
    patch: Dict[str, Any] = {}
    if "topN" in raw:
        v = _clamp(raw["topN"], 5, 40, None, integer=True)
        if v is not None:
            patch["topN"] = v
    if "indCap" in raw:
        v = _clamp(raw["indCap"], 0.1, 0.5, None)
        if v is not None:
            patch["indCap"] = round(float(v), 2)
    if "liqMin" in raw:
        v = _clamp(raw["liqMin"], 0, 50, None)
        if v is not None:
            patch["liqMin"] = round(float(v), 1)
    for k in _PHRASE_BOOL_KEYS:
        if k in raw:
            patch[k] = bool(raw[k])
    if "factors" in raw:
        fs = _validate_factors(raw["factors"])
        if fs:
            patch["factors"] = fs
    if not patch:
        return {"ok": False, "reason": "未识别出可调整的约束(已回落本地)"}
    hit = [str(h) for h in (data.get("hit") or [])][:8]
    return {"ok": True, "model": r["model"], "patch": patch, "hit": hit, "tokens": r["tokens"]}
