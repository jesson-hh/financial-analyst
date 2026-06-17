# guanlan_v2.cards.refine · 炼(对话精炼经验卡)接引擎大模型
# 测纯函数(基础 prompt 组装 / JSON 容错解析)+ 注入假 client 的 refine_card;
# 真 LLM 调用不在单测(留控制端 live 验证)。async 用 asyncio.run 跑,免 pytest-asyncio。
import asyncio

from guanlan_v2.cards.refine import (
    SYSTEM_PROMPT,
    build_refine_messages,
    parse_refine_output,
    refine_card,
)


class _FakeClient:
    def __init__(self, content):
        self._content = content
        self.calls = []

    async def chat(self, messages, response_format=None, temperature=0.2):
        self.calls.append({"messages": messages, "response_format": response_format,
                           "temperature": temperature})
        return {"choices": [{"message": {"content": self._content}}]}


_CARD = {"name": "缩量企稳反转", "insight": "超跌缩量企稳后反转概率抬升。",
         "conds": [["量比", "<", "0.7"]], "scenes": ["周频"], "expr": "rank(rev)"}


def test_system_prompt_sets_role_and_json_contract():
    assert "经验卡" in SYSTEM_PROMPT
    assert "觀瀾" in SYSTEM_PROMPT or "观澜" in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT.upper()
    assert "reply" in SYSTEM_PROMPT          # 输出契约里点名 reply 字段


def test_system_prompt_includes_factor_dsl_vocab():
    # 因子 DSL 白名单注入 system prompt:模型只能用引擎真实语法写 expr
    for tok in ("returns", "ts_sum", "rank", "turnover_rate",
                "correlation", "indneutralize", "lambda"):
        assert tok in SYSTEM_PROMPT, f"SYSTEM_PROMPT 缺少关键 token: {tok}"


def test_system_prompt_includes_concept_dsl_kb():
    # §一 通用 alpha 范例 + §三 组合规则 仍在
    assert "cross(ts_mean(close,5), ts_mean(close,20))" in SYSTEM_PROMPT  # 均线金叉范例
    assert "truth value" in SYSTEM_PROMPT                                 # 组合规则(别用 and/or)
    # §二 已修正:技术指标大多可重建;只有 OBV/CCI/SAR 是真缺口、expr 留空
    assert "OBV" in SYSTEM_PROMPT and "CCI" in SYSTEM_PROMPT and "SAR" in SYSTEM_PROMPT
    assert "留空" in SYSTEM_PROMPT


def test_build_messages_has_system_then_user_with_card_and_instruction():
    msgs = build_refine_messages(_CARD, [], "把量比阈值放宽到 0.8")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_PROMPT
    assert msgs[-1]["role"] == "user"
    u = msgs[-1]["content"]
    assert "缩量企稳反转" in u                 # 当前卡名进 user
    assert "0.8" in u                          # 指令进 user
    assert "量比" in u                          # 触发条件进 user


def test_build_messages_includes_recent_chat_history():
    chat = [{"role": "user", "text": "先改成日频"}, {"role": "asst", "text": "好"}]
    u = build_refine_messages(_CARD, chat, "再放宽阈值")[-1]["content"]
    assert "先改成日频" in u


def test_parse_clean_json():
    out = parse_refine_output('{"reply":"已放宽","insight":"新洞察","scenes":["日频"]}')
    assert out["reply"] == "已放宽"
    assert out["patch"]["insight"] == "新洞察"
    assert out["patch"]["scenes"] == ["日频"]


def test_parse_strips_markdown_fences():
    out = parse_refine_output('```json\n{"reply":"r","expr":"rank(x)"}\n```')
    assert out["reply"] == "r"
    assert out["patch"]["expr"] == "rank(x)"


def test_parse_extracts_from_surrounding_prose():
    out = parse_refine_output('好的,这是结果:{"reply":"r","name":"新名"} 以上。')
    assert out["patch"]["name"] == "新名"


def test_parse_only_keeps_known_patch_fields():
    out = parse_refine_output('{"reply":"r","insight":"i","junk":"x"}')
    assert "junk" not in out["patch"]
    assert set(out["patch"]).issubset({"name", "insight", "conds", "scenes", "expr"})


def test_refine_card_uses_injected_client_and_parses():
    fake = _FakeClient('{"reply":"已放宽阈值","insight":"放宽后的洞察","scenes":["日频"]}')
    res = asyncio.run(refine_card(_CARD, [], "放宽到0.8", client=fake))
    assert res["reply"] == "已放宽阈值"
    assert res["patch"]["insight"] == "放宽后的洞察"
    assert res["patch"]["scenes"] == ["日频"]
    # 走 JSON 模式 + system prompt 在首条
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert fake.calls[0]["messages"][0]["role"] == "system"


# ── 端点接线(monkeypatch 假 refine_card,不触真 LLM) ──
def _refine_client(tmp_path, fake_refine, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guanlan_v2.cards.store import CardStore
    import guanlan_v2.cards.api as api
    monkeypatch.setattr(api, "refine_card", fake_refine)
    app = FastAPI()
    app.include_router(api.build_cards_router(CardStore(root=tmp_path)))
    return TestClient(app)


def test_refine_endpoint_returns_patch(tmp_path, monkeypatch):
    async def fake_refine(card, chat, instruction):
        return {"reply": "已改", "patch": {"insight": "x"}}
    c = _refine_client(tmp_path, fake_refine, monkeypatch)
    r = c.post("/cards/refine", json={"draft": {"name": "X"}, "chat": [], "instruction": "改"})
    assert r.status_code == 200
    assert r.json()["patch"]["insight"] == "x"


def test_refine_endpoint_502_on_llm_error(tmp_path, monkeypatch):
    async def boom(card, chat, instruction):
        raise RuntimeError("no api key / proxy down")
    c = _refine_client(tmp_path, boom, monkeypatch)
    r = c.post("/cards/refine", json={"draft": {}, "instruction": "改"})
    assert r.status_code == 502


def test_system_prompt_includes_ta_indicator_examples():
    # 已验证 TA 指标库范例已注入 prompt:模型能照着写 MACD/RSI/KDJ 的可编译 expr
    assert "TA 指标范例" in SYSTEM_PROMPT
    assert "sma(close,13,2)" in SYSTEM_PROMPT          # MACD DIF 范例(EMA12)
