"""帷幄/buddy 轮内上下文预算 — 回归锁(优化 ②③)。

② schema 8626 字符(~2.6K tok)是**固定前缀**(永远紧跟 system),DeepSeek 自动前缀缓存
   会把它按 cached_tokens 复用 → 不是「无缓存的泄漏」。本次只做两件诚实改进:
   (a) 每轮只建一次 schema(原本循环里 15× 重建,token 中性,纯 CPU/稳定性);
   (b) LLMClient 捕获 DeepSeek 的 cached_tokens 让缓存可观测(证明前缀已被缓存)。

③ 工具结果每轮重进上下文是**真泄漏**:2-4KB 的 stock_brief 在 3..15 轮被反复重发。
   修法 = `_messages_for_llm` 把**早前**(上一条 assistant 之前)的大工具结果折叠成
   头片 + 「重跑取全量」指针;**最新**一批工具结果(上一条 assistant 之后)保持全量,
   让 LLM 当轮拿到精确原文推理。红线:只丢尾巴不改数字,全量原文已经过 SSE 进 UI。

锁:① 早前大结果被折叠且带可重跑指针 ② 最新结果不折叠 ③ 小结果不折叠
   ④ 存储里的原文不被改写(折叠只发生在喂 LLM 的那份拷贝)⑤ 缓存命中被计入。
"""
import json

from financial_analyst.buddy.agent import BuddyAgent, Message
from financial_analyst.llm.client import LLMClient


def _asst(text="", tool_calls=None):
    raw = {"role": "assistant", "content": text}
    if tool_calls:
        raw["tool_calls"] = tool_calls
    return Message(role="assistant", content=text, raw=raw)


def _tool(content, call_id="c1"):
    raw = {"role": "tool", "tool_call_id": call_id, "content": content}
    return Message(role="tool", content=content, raw=raw)


def _build_agent():
    # for_agent 只读 llm.yaml,不触网;_messages_for_llm 是纯函数,不调 LLM。
    return BuddyAgent()


def test_stale_large_tool_result_is_folded():
    a = _build_agent()
    big = "价 100.5 PE 20.3 " + ("x" * 4000)   # > _STALE_TOOL_MIN
    a.messages = [
        Message(role="user", content="看看茅台"),
        _asst("", [{"id": "c1", "type": "function",
                    "function": {"name": "stock_brief", "arguments": "{}"}}]),
        _tool(big, "c1"),                        # 早前结果(下面还有一条 assistant)
        _asst("茅台现价 100.5。"),                # 最近的 assistant
    ]
    out = a._messages_for_llm()
    folded = out[2]["content"]
    assert len(folded) < len(big), "早前大结果应被折叠变短"
    assert "重新调用" in folded and "可重跑" in folded, "折叠须留可重跑指针"
    assert folded.startswith("价 100.5 PE 20.3"), "保留的头片不得改写数字"
    # 存储原文不被改写(折叠只在喂 LLM 的拷贝里)
    assert a.messages[2].content == big, "in-memory 原文必须保持全量"


def test_fresh_tool_result_kept_full():
    a = _build_agent()
    big = "y" * 5000
    a.messages = [
        Message(role="user", content="看看茅台"),
        _asst("", [{"id": "c1", "type": "function",
                    "function": {"name": "stock_brief", "arguments": "{}"}}]),
        _tool(big, "c1"),                        # 最新一批结果(其后没有 assistant)
    ]
    out = a._messages_for_llm()
    assert out[2]["content"] == big, "最新工具结果必须全量喂给 LLM(当轮要精确推理)"


def test_small_stale_tool_result_untouched():
    a = _build_agent()
    small = "茅台 100.5 PE 20"   # < _STALE_TOOL_MIN
    a.messages = [
        Message(role="user", content="看看茅台"),
        _asst("", [{"id": "c1", "type": "function",
                    "function": {"name": "quote_lookup", "arguments": "{}"}}]),
        _tool(small, "c1"),
        _asst("好的。"),
    ]
    out = a._messages_for_llm()
    assert out[2]["content"] == small, "小结果不应被折叠"


def test_schema_build_is_stable():
    """schema 须可重复且字节稳定 —— 这是 DeepSeek 前缀缓存命中的前提。"""
    a = _build_agent()
    s1 = json.dumps(a._tool_schemas(None), ensure_ascii=False)
    s2 = json.dumps(a._tool_schemas(None), ensure_ascii=False)
    assert s1 == s2, "同一组工具的 schema 序列化必须字节一致(否则破坏前缀缓存)"


def test_llm_client_accumulates_cache_hits():
    c = LLMClient(provider="deepseek", model="deepseek-chat", config={})
    # DeepSeek/OpenAI 标准:usage.prompt_tokens_details.cached_tokens
    c._accumulate_usage({"usage": {
        "prompt_tokens": 3000, "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 2600},
    }})
    assert c.total_prompt_tokens == 3000
    assert c.total_cached_tokens == 2600
    # DeepSeek 原始顶层字段 prompt_cache_hit_tokens(若 details 缺失则回退到它)
    c2 = LLMClient(provider="deepseek", model="deepseek-chat", config={})
    c2._accumulate_usage({"usage": {
        "prompt_tokens": 3000, "completion_tokens": 50,
        "prompt_cache_hit_tokens": 2600, "prompt_cache_miss_tokens": 400,
    }})
    assert c2.total_cached_tokens == 2600
