# -*- coding: utf-8 -*-
"""DeepSeek 逐篇研报结构化抽取(照 screen/llm.py 模式)。

红线:显式 config_path=仓内 llm.yaml;json_object;真失败 ok:False 绝不伪造;
quote 必须是原文子串,不是则降级 quote=None+quote_dropped 标注。

实现前必核结论(2026-07-02,对照 engine/financial_analyst/llm/client.py):
- ``LLMClient.for_agent(agent_name, config_path=None)``——参数名确为 ``config_path``,
  接受 ``Optional[Path]``(经 ``find_config`` 用于 ``Path`` 语义比较,传 ``Path`` 而非 str)。
- ``client.chat(messages, tools=None, response_format=None, temperature=0.2)`` 返回值
  **不是**带 ``.content/.model/.prompt_tokens/.completion_tokens`` 属性的对象,而是
  **dict**:OpenAI 兼容路径(qwen/deepseek/openai/openrouter,本模块用的 deepseek 走这条)
  内部 ``response.model_dump()``;litellm 兜底路径直接返回 litellm ``ModelResponse``
  (dict-like)。仓内既有全部真调用点(``guanlan_v2/cards/refine.py``、
  ``guanlan_v2/screen/llm.py::_call_llm_json``、``guanlan_v2/seats/api.py``)都统一按
  ``resp["choices"][0]["message"]["content"]`` 取正文,从未出现 ``resp.content``。
  token 用量与所用 model 名也不在响应对象上,而是 ``LLMClient`` 实例的累计属性
  ``client.total_prompt_tokens`` / ``client.total_completion_tokens`` / ``client.model``。
  → 本文件与其 fake client 已按此真实契约实现(而非任务书示例里假设的属性式 resp)。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM_CONFIG = _REPO_ROOT / "config" / "llm.yaml"

_STANCES = {"多", "中", "空"}
_CATALYSTS = {"订单", "涨价", "扩产", "技术突破", "政策", "业绩", "认证", "新品"}
_GLOBAL_FIELDS = {"国产化率", "份额", "技术差距", "认证"}
_VERDICTS = {"支持", "否证"}

def _system(industry: str) -> str:
    """系统提示按框架行业名生成(2026-07-03 多框架:硬编码「AI 产业链」会误导其他链的抽取)。"""
    return (
        "你是 A 股行业研究抽取器。只依据给定研报原文抽取,禁止编造原文没有的数字/事件。"
        "只输出 JSON(无多余文字)。segments/edges/narratives 等核心字段的 id 必须取自给定框架白名单;"
        f"quote 字段必须是原文的连续子串;研报与 {industry} 无关时输出 {{\"segments\": []}}。"
        f"重要:若研报包含框架白名单未覆盖、但对 {industry} 投研重要的信息"
        "(新环节/新传导关系/新叙事/环节全球坐标应修正的证据),写进 observations 字段提案,"
        "不要硬塞进白名单 id,也不要因为框架没覆盖就丢弃。"
        "同时尽量抽取硬指标:评级及其变化与目标价(report_meta)、盈利预测修正方向(forecast_revisions)、"
        "带数值的量化数据点如价格/产能/渗透率/订单/国产化率(datapoints,能挂靠环节或传导边就挂)、"
        "宏观驱动读数证据(driver_updates)。没有就留空,禁止编造数字。"
    )


_SYSTEM = _system("AI 产业链")   # 兼容旧引用(深回填冒烟脚本等);extract_one 按 fw 名现生成

_OBS_KINDS = {"新环节", "新边", "新叙事", "坐标修正", "其他"}
_RATING_CHANGES = {"首次覆盖", "上调", "下调", "维持", "无"}
_FC_METRICS = {"营收", "净利润", "EPS"}
_FC_DIRS = {"上调", "下调", "新增"}
_DP_KINDS = {"价格", "产能", "渗透率", "订单", "出货量", "国产化率", "份额", "市场规模", "资本开支", "其他"}


def _norm_code(c: str) -> Optional[str]:
    c = (c or "").strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    m = re.match(r"^(SH|SZ|BJ)?(\d{6})$", c)
    if not m:
        return None
    pre, num = m.group(1), m.group(2)
    if pre:
        return pre + num
    if num.startswith("6"):
        return "SH" + num
    if num.startswith(("0", "3")):
        return "SZ" + num
    if num.startswith(("4", "8")):
        return "BJ" + num
    return None  # 1/2/5/7/9 开头非A股常规板块码,拒绝而非猜测


def _prompt(doc: dict, text: str, digest: str) -> str:
    schema = {
        "segments": [{"segment_id": "C2", "stance": "多|中|空", "strength": 1, "quote": "原文子串"}],
        "catalysts": [{"type": "订单|涨价|扩产|技术突破|政策|业绩|认证|新品", "desc": "一句话", "date_hint": "可空"}],
        "edges": [{"edge_id": "T4", "verdict": "支持|否证", "evidence": "一句话"}],
        "narratives": [{"narrative_id": "N4", "stance": "多|中|空"}],
        "global_updates": [{"segment_id": "C2", "field": "国产化率|份额|技术差距|认证", "content": "一句话"}],
        "stocks": [{"code": "SH688498", "stance": "多|中|空", "logic": "一句话"}],
        "observations": [{"kind": "新环节|新边|新叙事|坐标修正|其他", "note": "一句话(框架未覆盖的重要信息)", "suggest_id": "建议挂靠的现有id,可空"}],
        "report_meta": {"rating": "研报评级原词,可空", "rating_change": "首次覆盖|上调|下调|维持|无", "target_price": "目标价数字,可空", "target_code": "目标价对应个股代码,可空"},
        "forecast_revisions": [{"code": "SH688498", "metric": "营收|净利润|EPS", "direction": "上调|下调|新增", "year": "2026E"}],
        "datapoints": [{"kind": "价格|产能|渗透率|订单|出货量|国产化率|份额|市场规模|资本开支|其他", "subject": "指标对象如 DRAM合约价", "value": "原文数值如 +58~63%", "period": "期间如 25Q4,可空", "segment_id": "关联环节id,可空", "edge_id": "验证的传导边id,可空"}],
        "driver_updates": [{"driver_id": "D1", "note": "驱动最新读数证据一句话(带数字)"}],
    }
    return (
        f"## 框架白名单\n{digest}\n\n"
        f"## 研报元数据\n标题: {doc.get('title')}\n机构: {doc.get('org')}\n日期: {doc.get('publish_ts')}\n\n"
        f"## 研报原文\n{text}\n\n"
        f"## 输出 JSON 形状(字段名精确一致,列表可为空)\n{json.dumps(schema, ensure_ascii=False)}"
    )


def _ws_free(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _quote_in_text(quote, text: str) -> bool:
    """逐字连续匹配,但忽略空白差异:PDF 解析文本句中常有换行/空格,
    模型复述同内容会挂纯子串检查(2026-07-03 kimi 首批实测大量误丢)。
    去空白后仍要求字符连续一致 → 依旧防编造。"""
    if not (isinstance(quote, str) and quote):
        return False
    if quote in text:
        return True
    q = _ws_free(quote)
    return bool(q) and q in _ws_free(text)


def validate_extraction(raw: dict, fw: dict, text: str) -> dict:
    sids = {s["id"] for s in fw["segments"]}
    eids = {e["id"] for e in fw["edges"]}
    nids = {n["id"] for n in fw["narratives"]}
    out: dict = {"segments": [], "catalysts": [], "edges": [], "narratives": [], "global_updates": [], "stocks": []}
    seen_seg: set = set()
    for s in raw.get("segments") or []:
        sid = s.get("segment_id")
        if sid not in sids or s.get("stance") not in _STANCES or sid in seen_seg:
            continue  # 同环节重复条目只留首条(kimi 首批实测出现 G1×2,聚合会双计)
        seen_seg.add(sid)
        try:
            strength = max(1, min(3, int(s.get("strength") or 1)))
        except Exception:  # noqa: BLE001
            strength = 1
        quote = s.get("quote")
        dropped = False
        if not _quote_in_text(quote, text):
            quote, dropped = None, True
        out["segments"].append({"segment_id": sid, "stance": s["stance"], "strength": strength,
                                "quote": quote, "quote_dropped": dropped})
    for c in raw.get("catalysts") or []:
        if c.get("type") in _CATALYSTS and c.get("desc"):
            out["catalysts"].append({"type": c["type"], "desc": str(c["desc"]), "date_hint": c.get("date_hint")})
    seen_edge: set = set()
    for e in raw.get("edges") or []:
        if e.get("edge_id") in eids and e.get("verdict") in _VERDICTS and e["edge_id"] not in seen_edge:
            seen_edge.add(e["edge_id"])
            out["edges"].append({"edge_id": e["edge_id"], "verdict": e["verdict"], "evidence": str(e.get("evidence") or "")})
    seen_narr: set = set()
    for n in raw.get("narratives") or []:
        if n.get("narrative_id") in nids and n.get("stance") in _STANCES and n["narrative_id"] not in seen_narr:
            seen_narr.add(n["narrative_id"])
            out["narratives"].append({"narrative_id": n["narrative_id"], "stance": n["stance"]})
    for g in raw.get("global_updates") or []:
        if g.get("segment_id") in sids and g.get("field") in _GLOBAL_FIELDS and g.get("content"):
            out["global_updates"].append({"segment_id": g["segment_id"], "field": g["field"], "content": str(g["content"])})
    for st in raw.get("stocks") or []:
        code = _norm_code(st.get("code") or "")
        if code and st.get("stance") in _STANCES:
            out["stocks"].append({"code": code, "stance": st["stance"], "logic": str(st.get("logic") or "")})
    # 框架外观察通道:白名单不适用(它就是给框架没覆盖的信息的),轻消毒防注水:
    # kind 归 enum、note 截 300 字、最多 8 条。Kimi 提案 → 人审 → 改 YAML,框架保持活的。
    out["observations"] = []
    for ob in (raw.get("observations") or [])[:8]:
        note = str(ob.get("note") or "").strip()
        if not note:
            continue
        kind = ob.get("kind") if ob.get("kind") in _OBS_KINDS else "其他"
        sug = ob.get("suggest_id")
        out["observations"].append({"kind": kind, "note": note[:300],
                                    "suggest_id": (str(sug)[:8] if sug else None)})
    # ── 硬指标(2026-07-03 扩展):评级/目标价、盈利预测修正、量化数据点、驱动读数证据 ──
    rm = raw.get("report_meta") or {}
    tp = rm.get("target_price")
    try:
        tp = round(float(str(tp).replace("元", "").replace(",", "")), 2) if tp not in (None, "", "无") else None
    except Exception:  # noqa: BLE001 — 解析不了=没有,不猜
        tp = None
    out["report_meta"] = {
        "rating": (str(rm.get("rating"))[:16] if rm.get("rating") else None),
        "rating_change": (rm.get("rating_change") if rm.get("rating_change") in _RATING_CHANGES else "无"),
        "target_price": tp,
        "target_code": _norm_code(str(rm.get("target_code") or "")),
    }
    out["forecast_revisions"] = []
    for fr in (raw.get("forecast_revisions") or [])[:12]:
        code = _norm_code(str(fr.get("code") or ""))
        if fr.get("metric") in _FC_METRICS and fr.get("direction") in _FC_DIRS:
            out["forecast_revisions"].append({"code": code, "metric": fr["metric"],
                                              "direction": fr["direction"],
                                              "year": (str(fr.get("year"))[:8] if fr.get("year") else None)})
    out["datapoints"] = []
    for dp in (raw.get("datapoints") or [])[:12]:
        subject = str(dp.get("subject") or "").strip()
        value = str(dp.get("value") or "").strip()
        if not subject or not value:
            continue
        sid = dp.get("segment_id")
        eid = dp.get("edge_id")
        out["datapoints"].append({
            "kind": (dp.get("kind") if dp.get("kind") in _DP_KINDS else "其他"),
            "subject": subject[:60], "value": value[:60],
            "period": (str(dp.get("period"))[:16] if dp.get("period") else None),
            "segment_id": (sid if sid in sids else None),
            "edge_id": (eid if eid in eids else None),
        })
    dids = {d["id"] for d in fw["drivers"]}
    out["driver_updates"] = []
    for du in (raw.get("driver_updates") or [])[:7]:
        note = str(du.get("note") or "").strip()
        if du.get("driver_id") in dids and note:
            out["driver_updates"].append({"driver_id": du["driver_id"], "note": note[:200]})
    return out


async def extract_one(doc: dict, text: str, fw: dict, client=None, timeout: float = 600.0) -> dict:
    # timeout 600s:kimi-k2.6 带 reasoning,4k字研报实测 170s/近8k completion tokens(2026-07-03);
    # 须 > httpx 单次超时(domestic profile 600s),否则 wait_for 会掐掉本可成功的尝试。deepseek 时代为90s。
    from .framework import framework_digest
    if client is None:
        from financial_analyst.llm.client import LLMClient  # 延迟 import
        client = LLMClient.for_agent("industry_extract", config_path=_LLM_CONFIG)
    industry = str(((fw.get("meta") or {}).get("name")) or "AI 产业链")
    messages = [{"role": "system", "content": _system(industry)},
                {"role": "user", "content": _prompt(doc, text, framework_digest(fw))}]
    # token 记账取增量:client 计数是实例累计属性,共享 client 跑批时直接取会逐篇重复累计
    # (2026-07-03 深回填日志虚高实证)。快照前后差 = 本篇真实用量。
    pt0 = getattr(client, "total_prompt_tokens", 0) or 0
    ct0 = getattr(client, "total_completion_tokens", 0) or 0
    try:
        resp = await asyncio.wait_for(
            client.chat(messages, response_format={"type": "json_object"}, temperature=0.1),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 真失败诚实(带异常类型:TimeoutError 的 str 为空)
        return {"ok": False, "reason": f"LLM 调用失败: {type(exc).__name__}: {exc}"}
    # 真 LLMClient.chat() 返回 dict(OpenAI 兼容路径 response.model_dump() /
    # litellm 兜底路径 ModelResponse,均按 dict 取);模型名与 token 用量在
    # client 实例累计属性上,不在响应对象上(见模块 docstring「实现前必核结论」)。
    try:
        content = resp["choices"][0]["message"]["content"] or ""
    except Exception:  # noqa: BLE001 — 响应形状异常也是诚实失败
        return {"ok": False, "reason": f"LLM 返回非 JSON: 响应形状异常 {type(resp).__name__}"}
    try:
        raw = json.loads(content)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", content)
        if not m:
            return {"ok": False, "reason": "LLM 返回非 JSON"}
        try:
            raw = json.loads(m.group(0))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"LLM JSON 解析失败: {exc}"}
    import pandas as pd
    ex = validate_extraction(raw, fw, text)
    model = getattr(client, "model", None)
    ex.update({
        "doc_id": doc.get("doc_id"), "title": doc.get("title"), "org": doc.get("org"),
        "publish_ts": doc.get("publish_ts"), "doc_type": doc.get("doc_type"),
        "extracted_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "model": model,
        # 原始响应落盘:框架 v2(加环节/边/叙事)后可对存量 raw 重新校验聚合,
        # 历史研报不用重新花钱再读(白名单当时丢弃的信息全在这里)。
        "raw": raw,
    })
    pt1 = getattr(client, "total_prompt_tokens", 0) or 0
    ct1 = getattr(client, "total_completion_tokens", 0) or 0
    return {"ok": True, "extraction": ex, "model": model,
            "prompt_tokens": max(0, pt1 - pt0),
            "completion_tokens": max(0, ct1 - ct0)}
