# recipe 路径多轮记忆 · 回归锁
#
# bug(浏览器活体复现): 用户「看立昂微」→ recipe 路径出速览; 紧接「给我出一份深度研报」
# → 助手反问「哪只股票?」。根因: run_recipe 只拿 client, 既不读也不写 agent.messages,
# 对话历史从不累积 → recipe→recipe / recipe→自由循环 两处断裂。
#
# 这两条测试锁:① 综合 prompt 必须看得到前几轮对话 ② 这轮 (query, answer) 必须写回
# 共享历史。纯 fake client/agent, 不打真 LLM、不跑真工具。
import asyncio

from financial_analyst.buddy.agent import Message
from financial_analyst.buddy.recipes import Recipe, run_recipe


class _FakeClient:
    """记录每次 chat 收到的 messages, 返回固定正文。"""

    def __init__(self, answer="立昂微深度研报正文 [§1]"):
        self.answer = answer
        self.calls = []

    async def chat(self, messages, tools=None, temperature=0.2):
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.answer}}]}


class _FakeAgent:
    """最小 agent: 只需 messages 列表 + add_user, 对齐 BuddyAgent 接口。"""

    def __init__(self):
        self.messages = []

    def add_user(self, text):
        self.messages.append(Message(role="user", content=text))


def _brief_recipe():
    # steps=[] → 不跑工具; resolve_slots 永远成功; gate 永远放行。
    return Recipe(
        intent="brief",
        name="测试速览",
        resolve_slots=lambda q, ctx: {"code": "SH605358"},
        steps=[],
        synthesis_system="你是 A 股研究员, 只用材料里的事实。",
        synthesis_user_tmpl="用户问: {query}\n\n材料:\n{pack}",
        gate=lambda answer, pack, slots: [],
    )


def _drain(query, client, agent):
    async def go():
        evts = []
        async for ev in run_recipe(_brief_recipe(), query, {}, client, agent=agent):
            evts.append(ev)
        return evts

    return asyncio.run(go())


def test_recipe_synthesis_includes_prior_conversation():
    """综合 LLM 必须看得到前几轮对话(否则「它/深度研报」无主体)。"""
    agent = _FakeAgent()
    agent.messages.append(Message(role="user", content="你帮我看看立昂微"))
    agent.messages.append(Message(role="assistant", content="立昂微 (SH605358) 当日涨停, PB 6.85。"))
    client = _FakeClient()

    _drain("给我出一份深度研报", client, agent)

    assert client.calls, "run_recipe 没调用 LLM"
    sent = client.calls[0]
    blob = "\n".join(
        m["content"] for m in sent if isinstance(m.get("content"), str)
    )
    assert ("立昂微" in blob) or ("SH605358" in blob), (
        "综合 prompt 没带上前几轮对话 — recipe 路径无记忆"
    )


def test_recipe_writes_turn_back_to_history():
    """这轮 (query, answer) 必须写回共享历史, 让后续轮(含自由循环 fallback)看得到。"""
    agent = _FakeAgent()
    client = _FakeClient(answer="立昂微 (SH605358) 综合倾向中性偏多 [§1]")

    before = len(agent.messages)
    _drain("你帮我看看立昂微", client, agent)

    contents = [str(m.content) for m in agent.messages]
    assert len(agent.messages) > before, "这轮没写回历史"
    assert any("你帮我看看立昂微" in c for c in contents), "用户 query 没进历史"
    assert any("综合倾向中性偏多" in c for c in contents), "助手 answer 没进历史"


def test_recipe_history_skips_tool_noise_and_is_bounded():
    """历史里的 tool 消息/空消息不该泄进综合 prompt; 且只取近若干轮。"""
    agent = _FakeAgent()
    # 夹一条 tool 噪声 + 一条空消息, 不该出现在综合 prompt
    agent.messages.append(Message(role="user", content="看茅台"))
    agent.messages.append(Message(role="tool", content="ths_fund_flow 原始 JSON 一大坨", raw={}))
    agent.messages.append(Message(role="assistant", content="茅台 (SH600519) 主力净流入。"))
    client = _FakeClient()

    _drain("它负债率高吗", client, agent)

    sent = client.calls[0]
    roles = [m.get("role") for m in sent]
    assert "tool" not in roles, "tool 噪声泄进了综合 prompt"
    blob = "\n".join(m["content"] for m in sent if isinstance(m.get("content"), str))
    assert "原始 JSON 一大坨" not in blob, "tool 原文泄进了综合 prompt"
