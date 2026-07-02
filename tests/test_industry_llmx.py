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
        self.total_prompt_tokens = prompt_tokens
        self.total_completion_tokens = completion_tokens
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        if self._exc:
            raise self._exc
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
        "stocks": [{"code": "688498.SH", "stance": "多", "logic": "量产爬坡"}],
    }
    r = asyncio.run(extract_one(_doc(), text, fw, client=_FakeClient(payload)))
    assert r["ok"] is True
    ex = r["extraction"]
    segs = {s["segment_id"]: s for s in ex["segments"]}
    assert set(segs) == {"C2", "C1"}
    assert segs["C2"]["quote"] == "EML 缺口 25-30%" and segs["C2"]["quote_dropped"] is False
    assert segs["C1"]["quote"] is None and segs["C1"]["quote_dropped"] is True
    assert [e["edge_id"] for e in ex["edges"]] == ["T4"]
    assert ex["stocks"][0]["code"] == "SH688498"          # 码式归一
    assert ex["doc_id"] == "d1" and ex["extracted_at"]
    assert r["model"] == "deepseek-chat"
    assert r["prompt_tokens"] == 100 and r["completion_tokens"] == 50


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
