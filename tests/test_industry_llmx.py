# -*- coding: utf-8 -*-
# guanlan_v2.industry.llmx · DeepSeek 逐篇研报结构化抽取(照 screen/llm.py 模式)。
# 真 LLMClient.chat() 返回 dict(见 financial_analyst/llm/client.py::_chat_openai_compat
# 用 response.model_dump()、_chat_litellm 直返 litellm ModelResponse——两条路径调用方
# 都按 resp["choices"][0]["message"]["content"] 取内容;既有 refine.py/screen/llm.py
# 的假 client 也都是这个形状)。fake client 必须镜像这个真实契约,不能凭空造
# .content/.model 属性对象。
import asyncio
import json


class _FakeClient:
    """镜像真 LLMClient 契约:chat() 返回 dict {"choices":[{"message":{"content":...}}]};
    model/tokens 走 client 实例属性(client.model / client.total_prompt_tokens /
    client.total_completion_tokens),不是响应对象属性。"""

    def __init__(self, payload=None, raise_exc=None, model="deepseek-chat",
                 prompt_tokens=100, completion_tokens=50):
        self._payload = payload
        self._exc = raise_exc
        self.model = model
        # 镜像真契约:计数是"累计"属性,每次 chat 递增(extract_one 取前后快照差作单篇用量)
        self._per_call = (prompt_tokens, completion_tokens)
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if self._exc:
            raise self._exc
        self.total_prompt_tokens += self._per_call[0]
        self.total_completion_tokens += self._per_call[1]
        content = json.dumps(self._payload, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


def _doc():
    return {"doc_id": "d1", "title": "光芯片深度", "org": "某券商",
            "publish_ts": "2026-06-30", "doc_type": "industry_research"}


def test_extract_ok_and_validation():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one
    fw = load_framework()
    text = "报告指出 EML 缺口 25-30%,订单排至 2027 年。"
    payload = {
        "segments": [
            {"segment_id": "C2", "stance": "多", "strength": 3, "quote": "EML 缺口 25-30%"},
            {"segment_id": "ZZ9", "stance": "多", "strength": 1, "quote": "x"},
            {"segment_id": "C1", "stance": "多", "strength": 2, "quote": "编造的引句"},
        ],
        "catalysts": [{"type": "涨价", "desc": "EML涨价", "date_hint": "2026-06"}],
        "edges": [{"edge_id": "T4", "verdict": "支持", "evidence": "缺口涨价"},
                  {"edge_id": "T99", "verdict": "支持", "evidence": "x"}],
        "narratives": [{"narrative_id": "N4", "stance": "多"}],
        "global_updates": [{"segment_id": "C2", "field": "国产化率", "content": "良率接近海外"}],
        "stocks": [
            {"code": "688498.SH", "stance": "多", "logic": "量产爬坡"},
            {"code": "830799", "stance": "多", "logic": "x"},
            {"code": "912345", "stance": "多", "logic": "x"},
        ],
        "observations": [
            {"kind": "新环节", "note": "HBM 设备(键合机)研报中独立成投资主线,框架未覆盖", "suggest_id": "A3"},
            {"kind": "瞎编的kind", "note": "x" * 500, "suggest_id": None},
            {"kind": "新叙事", "note": ""},   # 空 note 丢弃
        ],
    }
    r = asyncio.run(extract_one(_doc(), text, fw, client=_FakeClient(payload)))
    assert r["ok"] is True
    ex = r["extraction"]
    segs = {s["segment_id"]: s for s in ex["segments"]}
    assert set(segs) == {"C2", "C1"}
    assert segs["C2"]["quote"] == "EML 缺口 25-30%" and segs["C2"]["quote_dropped"] is False
    assert segs["C1"]["quote"] is None and segs["C1"]["quote_dropped"] is True
    assert [e["edge_id"] for e in ex["edges"]] == ["T4"]
    assert {s["code"] for s in ex["stocks"]} == {"SH688498", "BJ830799"}  # 码式归一+拒绝9开头
    assert ex["doc_id"] == "d1" and ex["extracted_at"]
    assert r["model"] == "deepseek-chat"
    assert r["prompt_tokens"] == 100 and r["completion_tokens"] == 50   # 前后快照差=单篇增量
    # 框架外观察通道:kind 归 enum、note 截 300、空 note 丢;raw 原始响应落盘(框架v2免费重聚合)
    obs = ex["observations"]
    assert len(obs) == 2
    assert obs[0] == {"kind": "新环节", "note": obs[0]["note"], "suggest_id": "A3"} and "HBM" in obs[0]["note"]
    assert obs[1]["kind"] == "其他" and len(obs[1]["note"]) == 300
    assert ex["raw"]["observations"][0]["kind"] == "新环节"


def test_quote_whitespace_tolerant_but_no_fabrication():
    """PDF 解析正文句中有换行/空格 → 引句去空白后逐字连续一致即通过;
    改写/编造仍必须丢弃(2026-07-03 kimi 首批实测大量误丢后加)。"""
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import validate_extraction
    fw = load_framework()
    text = "全球AI大模型总调\n用量为36.1万亿Token,较此前一周增长13.5%,连续七\n周上涨。"
    raw = {"segments": [
        {"segment_id": "M1", "stance": "多", "strength": 2,
         "quote": "全球AI大模型总调用量为36.1万亿Token,较此前一周增长13.5%"},   # 同内容,原文断行
        {"segment_id": "C2", "stance": "多", "strength": 2,
         "quote": "全球AI大模型调用量为36万亿Token"},                          # 改写(丢字)→ 拒
    ]}
    out = validate_extraction(raw, fw, text)
    segs = {s["segment_id"]: s for s in out["segments"]}
    assert segs["M1"]["quote_dropped"] is False and segs["M1"]["quote"]
    assert segs["C2"]["quote_dropped"] is True and segs["C2"]["quote"] is None


def test_extract_llm_failure_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one
    fw = load_framework()
    r = asyncio.run(extract_one(_doc(), "文", fw, client=_FakeClient(raise_exc=RuntimeError("boom"))))
    assert r["ok"] is False and "boom" in r["reason"]


def test_extract_bad_json_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.llmx import extract_one

    class _BadClient(_FakeClient):
        async def chat(self, messages, **kw):
            return {"choices": [{"message": {"content": "这不是JSON"}}]}

    fw = load_framework()
    r = asyncio.run(extract_one(_doc(), "文", fw, client=_BadClient()))
    assert r["ok"] is False and "JSON" in r["reason"]
