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

_SYSTEM = (
    "你是 A 股行业研究抽取器。只依据给定研报原文抽取,禁止编造原文没有的数字/事件。"
    "只输出 JSON(无多余文字)。所有 id 必须取自给定框架白名单;quote 字段必须是原文的连续子串;"
    "研报与 AI 产业链无关时输出 {\"segments\": []}。"
)


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
    }
    return (
        f"## 框架白名单\n{digest}\n\n"
        f"## 研报元数据\n标题: {doc.get('title')}\n机构: {doc.get('org')}\n日期: {doc.get('publish_ts')}\n\n"
        f"## 研报原文\n{text}\n\n"
        f"## 输出 JSON 形状(字段名精确一致,列表可为空)\n{json.dumps(schema, ensure_ascii=False)}"
    )


def validate_extraction(raw: dict, fw: dict, text: str) -> dict:
    sids = {s["id"] for s in fw["segments"]}
    eids = {e["id"] for e in fw["edges"]}
    nids = {n["id"] for n in fw["narratives"]}
    out: dict = {"segments": [], "catalysts": [], "edges": [], "narratives": [], "global_updates": [], "stocks": []}
    for s in raw.get("segments") or []:
        sid = s.get("segment_id")
        if sid not in sids or s.get("stance") not in _STANCES:
            continue
        try:
            strength = max(1, min(3, int(s.get("strength") or 1)))
        except Exception:  # noqa: BLE001
            strength = 1
        quote = s.get("quote")
        dropped = False
        if not (isinstance(quote, str) and quote and quote in text):
            quote, dropped = None, True
        out["segments"].append({"segment_id": sid, "stance": s["stance"], "strength": strength,
                                "quote": quote, "quote_dropped": dropped})
    for c in raw.get("catalysts") or []:
        if c.get("type") in _CATALYSTS and c.get("desc"):
            out["catalysts"].append({"type": c["type"], "desc": str(c["desc"]), "date_hint": c.get("date_hint")})
    for e in raw.get("edges") or []:
        if e.get("edge_id") in eids and e.get("verdict") in _VERDICTS:
            out["edges"].append({"edge_id": e["edge_id"], "verdict": e["verdict"], "evidence": str(e.get("evidence") or "")})
    for n in raw.get("narratives") or []:
        if n.get("narrative_id") in nids and n.get("stance") in _STANCES:
            out["narratives"].append({"narrative_id": n["narrative_id"], "stance": n["stance"]})
    for g in raw.get("global_updates") or []:
        if g.get("segment_id") in sids and g.get("field") in _GLOBAL_FIELDS and g.get("content"):
            out["global_updates"].append({"segment_id": g["segment_id"], "field": g["field"], "content": str(g["content"])})
    for st in raw.get("stocks") or []:
        code = _norm_code(st.get("code") or "")
        if code and st.get("stance") in _STANCES:
            out["stocks"].append({"code": code, "stance": st["stance"], "logic": str(st.get("logic") or "")})
    return out


async def extract_one(doc: dict, text: str, fw: dict, client=None, timeout: float = 90.0) -> dict:
    from .framework import framework_digest
    if client is None:
        from financial_analyst.llm.client import LLMClient  # 延迟 import
        client = LLMClient.for_agent("industry_extract", config_path=_LLM_CONFIG)
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _prompt(doc, text, framework_digest(fw))}]
    try:
        resp = await asyncio.wait_for(
            client.chat(messages, response_format={"type": "json_object"}, temperature=0.1),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 真失败诚实
        return {"ok": False, "reason": f"LLM 调用失败: {exc}"}
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
    })
    return {"ok": True, "extraction": ex, "model": model,
            "prompt_tokens": getattr(client, "total_prompt_tokens", 0) or 0,
            "completion_tokens": getattr(client, "total_completion_tokens", 0) or 0}
