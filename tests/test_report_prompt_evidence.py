"""Task 5:证据块+数字锚七处注入 + report-writer 徽章/持仓视角 + bear F4 修——回归锁。

桩 LLM 捕 messages(照 tests/test_seats_decide_pa.py 的 _CapLLM 同款)。零网络零真 LLM:
LLMClient.for_agent 全桩;quote/f10/timeline 走本地文件读,异常均被各 _execute 自身
try/except 吞掉,不触网、不需要真实上游产物。

覆盖:
1. 七消费节点(fundamental/technical/whale/bull/bear/risk/report-writer):
   (a) 带 evidence-loader inputs → prompt 含「平台证据」标记 + 对应消费矩阵 section 键
       (裁剪证据——不该消费的 section 不出现)。
   (b) 无 evidence-loader → prompt 不含「平台证据」标记,且旧 SYSTEM_PROMPT 的既有
       distinctive 文本仍在(对照法:不做逐字节 diff——改前逻辑不可复现——而是断言
       "新标记缺席 + 旧标记仍在"两条,同任务书 6 的验收口径)。
2. bear F4 幻觉市值断根:quote-fetcher 真实 mv_yi/pe/ret_60d 满足三条件→"可援引 F4"+
   真实数字;不满足→"不得援引 F4"+真实数字(不是模板"Sub-200亿"字面硬编码)。数字锚
   (quote_summary)对 bull/bear/risk 三者均是"quote-fetcher 存在即注入"、独立于 ev。
3. company-research-evidence@2 的十五 section、schema_version/coverage 与
   report-writer 徽章:每个大段落(中文数字编号标题 + 新闻情绪研判)尾部注证据 as_of
   (按消费矩阵启发式映射到对应 section);ev 全缺 → 每段统一诚实降级徽章。
4. report-writer 持仓视角:evidence.sections.holding.held=True → prompt 含「持仓视角」
   确定性块(成本/数量/浮盈亏真实数字)+ SYSTEM_PROMPT 追加"结论段须给持有者视角建议"
   一句;未持有/无台账 → 两者皆不出现。
5. yaml 双本(config/swarm 与 engine/_resources)wiring 断言:七节点 input_keys/soft_deps
   含 evidence-loader;bull/bear/risk 的 input_keys 含 quote-fetcher 但 deps 不含
   (不改波次,quote 是全体 tier2 硬依赖必然已 done)。
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENGINE = _REPO_ROOT / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.agent.tier2 import fundamental_analyst as fa_mod  # noqa: E402
from financial_analyst.agent.tier2 import technical_analyst as ta_mod  # noqa: E402
from financial_analyst.agent.tier2 import whale_analyst as wa_mod  # noqa: E402
from financial_analyst.agent.tier3 import bull_advocate as bull_mod  # noqa: E402
from financial_analyst.agent.tier3 import bear_advocate as bear_mod  # noqa: E402
from financial_analyst.agent.tier3 import risk_officer as risk_mod  # noqa: E402
from financial_analyst.agent.tier3 import report_writer as rw_mod  # noqa: E402

_MEMORIES = _REPO_ROOT / "memories"

_CAP: Dict[str, Any] = {}


class _CapLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        _CAP["system"] = messages[0]["content"]
        # Inspect the actual initial evidence prompt even when the bounded
        # writer review adds a corrective turn after it.
        _CAP["user"] = messages[1]["content"]
        _CAP["last_user"] = messages[-1]["content"]
        _CAP["messages"] = list(messages)
        return {"choices": [{"message": {"content": _CAP["_response"]}}]}


def _run(monkeypatch, mod, agent_cls, inputs: dict, response: dict) -> Dict[str, Any]:
    _CAP.clear()
    _CAP["_response"] = json.dumps(response, ensure_ascii=False)
    monkeypatch.setattr(mod, "LLMClient", _CapLLM)
    agent = agent_cls(memory_root=_MEMORIES)
    _CAP["output"] = asyncio.run(agent._execute(inputs))
    return _CAP


def _extract_evidence_json(text: str) -> dict:
    marker = "# 平台证据 (确定性 — 引用须带出处, 数字禁编造)\n"
    idx = text.index(marker) + len(marker)
    line = text[idx:].split("\n", 1)[0]
    return json.loads(line)


def _full_evidence() -> dict:
    sections = {
            "quote_live": {"as_of": "2026-07-12T09:29:50", "price": 12.3, "pct": 1.1},
            "company_facts": {
                "as_of": "2026-06-30", "snapshot_date": "2026-07-10",
                "valuation": {"report_period": "2026-06-30"}, "events": [],
            },
            "announcements": {
                "as_of": "2026-07-10", "source": "cninfo_announcements",
                "scope": "metadata_only", "items": [],
            },
            "expectations": {
                "as_of": "2026-07-09", "source": "ths_eps_forecast",
                "trust": "external_consensus", "items": [],
            },
            "valuation": {
                "as_of": "2026-07-08", "pe_ttm": {"current": 18.4, "percentile": 32.1},
            },
            "research_views": {
                "as_of": "2026-07-07", "trust": "external_untrusted",
                "items": [], "evidence": [], "coverage": {},
            },
            "fundflow": {"as_of": "2026-07-12", "stock_main_net": -530000.0, "sector": 1.2e8},
            "board_eco": {"as_of": "2026-07-12T15:00:00", "zt_count": 47, "north_net": -3.98e10},
            "sentiment": {"as_of": "2026-07-12", "tag": "偏多", "read": "情绪回暖"},
            "kuaixun": {"as_of": "2026-07-12T10:00:00", "items": [{"time": "09:31", "title": "快讯A"}]},
            "chain": {"seg": "液冷", "quadrant": "Q1", "therm": "hot", "industry_views": []},
            "quant": {"v4_rank": 12, "v4_pct": 0.95,
                      "dl": {"lgb": None, "fc": 0.02, "lstm": 0.01, "gat": 0.03},
                      "rerank": {"rank_before": 40, "rank_after": 19, "stance": "up",
                                 "run_id": "r1", "ts": "2026-07-12"}},
            "mainline": {"as_of": "2026-07-11", "top": [], "stock_hit": None},
            "macro": {"as_of": "2026-07-12T08:00:00", "astock_temp": "warm", "global_temp": "neutral"},
            "holding": None,
        }
    return {
        "ok": True,
        "schema_version": "company-research-evidence@2",
        "generated_at": "2026-07-12T09:30:00",
        "coverage": {
            "present": [name for name, value in sections.items() if value is not None],
            "missing": ["holding"],
            "company_core": {
                "required": ["company_facts", "announcements", "expectations", "valuation"],
                "missing": [], "status": "complete",
            },
        },
        "sections": sections,
        "errors": {},
    }


def _quote(mv_yi=150.0, pe=180.0, ret_60d=0.62, price=23.4) -> dict:
    return {"code": "SH603986", "asof_date": "2026-07-12", "price": price,
            "pe": pe, "mv_yi": mv_yi, "ret_60d": ret_60d, "name": "兆易创新"}


# ---------------------------------------------------------------------------
# 1. fundamental-analyst:公司核心事实 + 外部观点 + chain/mainline/quant
# ---------------------------------------------------------------------------

def test_full_evidence_fixture_matches_v2_contract():
    ev = _full_evidence()
    expected = {
        "quote_live", "company_facts", "announcements", "expectations",
        "valuation", "research_views", "fundflow", "board_eco", "sentiment",
        "kuaixun", "chain", "quant", "mainline", "macro", "holding",
    }
    assert ev["schema_version"] == "company-research-evidence@2"
    assert len(ev["sections"]) == 15 and set(ev["sections"]) == expected
    assert ev["coverage"]["missing"] == ["holding"]
    assert ev["coverage"]["company_core"]["status"] == "complete"

def test_fundamental_with_ev_includes_evidence_block(monkeypatch):
    cap = _run(monkeypatch, fa_mod, fa_mod.FundamentalAnalyst,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"valuation_score": 1, "mv_tier": "mid"})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {
        "company_facts", "announcements", "expectations", "valuation",
        "research_views", "chain", "mainline", "quant",
    }
    assert "平台证据使用规则" in cap["system"]


def test_fundamental_no_ev_omits_marker_keeps_old_text(monkeypatch):
    cap = _run(monkeypatch, fa_mod, fa_mod.FundamentalAnalyst,
               {"quote-fetcher": _quote()},
               {"valuation_score": 1, "mv_tier": "mid"})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are a fundamental equity analyst" in cap["system"]


# ---------------------------------------------------------------------------
# 2. technical-analyst: quote_live/fundflow/board_eco
# ---------------------------------------------------------------------------

def test_technical_with_ev_includes_evidence_block(monkeypatch):
    cap = _run(monkeypatch, ta_mod, ta_mod.TechnicalAnalyst,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"technical_score": 1})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {"quote_live", "fundflow", "board_eco"}


def test_technical_no_ev_omits_marker_keeps_old_text(monkeypatch):
    cap = _run(monkeypatch, ta_mod, ta_mod.TechnicalAnalyst,
               {"quote-fetcher": _quote()},
               {"technical_score": 1})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are a technical analyst" in cap["system"]


# ---------------------------------------------------------------------------
# 3. whale-analyst: fundflow/board_eco
# ---------------------------------------------------------------------------

def test_whale_with_ev_includes_evidence_block(monkeypatch):
    cap = _run(monkeypatch, wa_mod, wa_mod.WhaleAnalyst,
               {"evidence-loader": _full_evidence()},
               {"whale_score": 1, "sentiment_label": "bounce"})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {"fundflow", "board_eco"}


def test_whale_no_ev_omits_marker_keeps_old_text(monkeypatch):
    cap = _run(monkeypatch, wa_mod, wa_mod.WhaleAnalyst,
               {},
               {"whale_score": 1, "sentiment_label": "bounce"})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are a whale-behavior" in cap["system"]


# ---------------------------------------------------------------------------
# 4. bull-advocate: quote_summary(数字锚,独立于 ev) + sentiment/fundflow/board_eco
# ---------------------------------------------------------------------------

def test_bull_with_ev_includes_evidence_block_and_quote_summary(monkeypatch):
    cap = _run(monkeypatch, bull_mod, bull_mod.BullAdvocate,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"thesis_bullets": ["[V1] a", "[V2] b"], "v_anchors": ["V1", "V2"],
                "target_price_high": 30.0, "target_price_base": 25.0})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {"sentiment", "fundflow", "board_eco"}
    assert '"mv_yi": 150.0' in cap["user"]


def test_bull_no_ev_omits_marker_keeps_quote_summary(monkeypatch):
    cap = _run(monkeypatch, bull_mod, bull_mod.BullAdvocate,
               {"quote-fetcher": _quote()},
               {"thesis_bullets": ["[V1] a", "[V2] b"], "v_anchors": ["V1", "V2"],
                "target_price_high": 30.0, "target_price_base": 25.0})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are a buy-side Bull Advocate" in cap["system"]
    # 数字锚不依赖 ev——quote-fetcher 一到就注入,独立特性
    assert '"mv_yi": 150.0' in cap["user"]


# ---------------------------------------------------------------------------
# 5. bear-advocate: F4 幻觉市值断根 + sentiment/fundflow/board_eco
# ---------------------------------------------------------------------------

def test_bear_f4_cites_real_numbers_when_conditions_met(monkeypatch):
    """mv=150亿<200, pe=180>100, ret60=62%>50% → 三条件成立,可援引 F4,真实数字入 prompt。"""
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote(mv_yi=150.0, pe=180.0, ret_60d=0.62)},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "{F4_CLAUSE}" not in cap["system"], "占位符必须被真实数值替换"
    assert "150.00亿" in cap["system"] and "180.00" in cap["system"] and "62.0%" in cap["system"]
    assert "可援引 F4" in cap["system"]
    assert "不得援引 F4" not in cap["system"]


def test_bear_f4_forbids_citation_when_conditions_not_met(monkeypatch):
    """大盘股 mv=1200亿,不满足 mv<200 → 不得援引 F4(不是硬编码"Sub-200亿"字面模板)。"""
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote(mv_yi=1200.0, pe=25.0, ret_60d=0.05)},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "1200.00亿" in cap["system"]
    assert "不得援引 F4" in cap["system"]
    assert "可援引 F4" not in cap["system"]


def test_bear_f4_honest_when_quote_missing(monkeypatch):
    """quote-fetcher 全缺 → 数字位写「证据未及」,不崩、不编造。"""
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "证据未及" in cap["system"]
    assert "不得援引 F4" in cap["system"]


def test_bear_with_ev_includes_evidence_block(monkeypatch):
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {"sentiment", "fundflow", "board_eco"}


def test_bear_no_ev_omits_marker_keeps_old_text(monkeypatch):
    cap = _run(monkeypatch, bear_mod, bear_mod.BearAdvocate,
               {"quote-fetcher": _quote()},
               {"thesis_bullets": ["[F2] a", "[F8] b"], "f_anchors": ["F2", "F8"]})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are a buy-side Bear Advocate" in cap["system"]


# ---------------------------------------------------------------------------
# 6. risk-officer: quote_summary + sentiment/fundflow/board_eco
# ---------------------------------------------------------------------------

def test_risk_officer_with_ev_includes_evidence_block_and_quote_summary(monkeypatch):
    cap = _run(monkeypatch, risk_mod, risk_mod.RiskOfficer,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"risk_score": -1, "position_sizing_advice": "1-3%"})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {
        "sentiment", "fundflow", "board_eco", "announcements", "kuaixun",
    }
    assert '"mv_yi": 150.0' in cap["user"]


def test_risk_officer_no_ev_omits_marker_keeps_old_text(monkeypatch):
    cap = _run(monkeypatch, risk_mod, risk_mod.RiskOfficer,
               {"quote-fetcher": _quote()},
               {"risk_score": -1, "position_sizing_advice": "1-3%"})
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "You are an independent Chief Risk Officer" in cap["system"]


# ---------------------------------------------------------------------------
# 7. report-writer:全部 sections + 徽章 + 持仓视角
# ---------------------------------------------------------------------------

_MD_BODY_TEMPLATE = (
    "# 兆易创新 (SH603986) — Deep-Dive Research\n\n"
    "**Date:** 2026-07-12 | **Price:** ¥23.40\n\n"
    "## 一、综合评级\n**总评 5/10** | 操作: 持有\n\n"
    "## 二、市场环境(大盘 · 主线 · 早盘)\n### A. 大盘\n内容\n\n"
    "## 三、基本面 (FundamentalAnalyst)\n内容\n\n"
    "## 七、操作建议\n- 目标价: 30\n"
)


def _rw_response(markdown_body: str) -> dict:
    return {
        "markdown_body": markdown_body,
        "rating_overall": 5, "rating_dimensions": {"fundamental": 1, "technical": 1, "whale": 1, "risk": -1},
        "action": "hold", "target_price": 30.0, "stop_loss": 20.0, "position_pct": 0.03,
        "summary_json": {},
    }


def test_report_writer_with_ev_includes_all_sections(monkeypatch, tmp_path):
    today = date.today().isoformat()
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": today, "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == set(_full_evidence()["sections"].keys())


def test_report_writer_retries_one_truncated_json_response(monkeypatch, tmp_path):
    class _RetryLLM:
        provider = "deepseek"
        model = "deepseek-chat"
        calls = 0
        seen_messages = []

        @classmethod
        def for_agent(cls, name):
            return cls()

        def with_overrides(self, **kw):
            return self

        async def chat(self, messages, **kw):
            type(self).calls += 1
            type(self).seen_messages.append(list(messages))
            if type(self).calls == 1:
                content = '{"markdown_body": "unterminated'
            else:
                content = json.dumps(
                    _rw_response(_MD_BODY_TEMPLATE), ensure_ascii=False,
                )
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(rw_mod, "LLMClient", _RetryLLM)
    agent = rw_mod.ReportWriter(memory_root=_MEMORIES)
    result = asyncio.run(agent._execute({
        "code": "SH603986", "asof_date": "2026-07-12",
        "out_dir": str(tmp_path), "quote-fetcher": _quote(),
    }))

    assert _RetryLLM.calls == 2
    assert "previous response was not a complete valid JSON" in (
        _RetryLLM.seen_messages[1][-1]["content"]
    )
    assert result["output_md_path"].endswith("SH603986_2026-07-12.md")


def test_report_writer_stops_after_second_invalid_json(monkeypatch, tmp_path):
    class _AlwaysInvalidLLM:
        provider = "deepseek"
        model = "deepseek-chat"
        calls = 0

        @classmethod
        def for_agent(cls, name):
            return cls()

        def with_overrides(self, **kw):
            return self

        async def chat(self, messages, **kw):
            type(self).calls += 1
            return {"choices": [{"message": {"content": "{"}}]}

    monkeypatch.setattr(rw_mod, "LLMClient", _AlwaysInvalidLLM)
    agent = rw_mod.ReportWriter(memory_root=_MEMORIES)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(agent._execute({
            "code": "SH603986", "asof_date": "2026-07-12",
            "out_dir": str(tmp_path), "quote-fetcher": _quote(),
        }))
    assert _AlwaysInvalidLLM.calls == 2


def test_report_writer_no_ev_omits_marker_keeps_old_text(monkeypatch, tmp_path):
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote()},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "持仓视角" not in cap["system"] and "持仓视角" not in cap["user"]
    assert "You are the chief analyst writing the final research report" in cap["system"]
    assert "company_risk_input" not in cap["system"]
    # 既有确定性段落(券商/股东)不受影响
    assert "券商评级与目标价" in cap["user"]
    assert "股东与主力" in cap["user"]
    legacy_sidecar = json.loads(
        (tmp_path / "SH603986_2026-07-12.json").read_text(encoding="utf-8")
    )
    assert legacy_sidecar["action"] == "hold"
    assert legacy_sidecar["target_price"] == 30.0
    assert legacy_sidecar["stop_loss"] == 20.0
    legacy_output = rw_mod.ReportOutput(**cap["output"])
    assert legacy_output.report_kind == "investment_report"
    assert legacy_output.rating_status == "rated"
    assert legacy_output.rating_overall == 2
    assert legacy_output.action == "hold"


def test_report_writer_badges_cite_section_as_of(monkeypatch, tmp_path):
    today = date.today().isoformat()
    res = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": today, "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               _rw_response(_MD_BODY_TEMPLATE))
    md_path = None
    # locate written md via out_dir glob (deterministic name {code}_{asof}.md)
    md_path = tmp_path / f"SH603986_{today}.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "〔证据 as_of: 2026-06-30〕" in text     # 三、基本面 → company_facts
    assert "〔证据 as_of: 2026-07-11〕" in text     # 一、综合评级 → quant(无as_of)/mainline
    assert "〔证据 as_of: 2026-07-12T08:00:00〕" in text  # 二、市场环境 → macro; 七、操作建议 → quant(无as_of)/macro
    assert "平台证据缺失" not in text


def test_report_writer_badges_honest_fallback_when_ev_missing(monkeypatch, tmp_path):
    today = date.today().isoformat()
    res = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": today, "out_dir": str(tmp_path),
                "quote-fetcher": _quote()},
               _rw_response(_MD_BODY_TEMPLATE))
    md_path = tmp_path / f"SH603986_{today}.md"
    text = md_path.read_text(encoding="utf-8")
    assert text.count("〔平台证据缺失,本段基于引擎自采数据〕") == 4  # 一/二/三/七 四个大段落
    assert "〔证据 as_of" not in text


def test_report_writer_holding_section_when_held(monkeypatch, tmp_path):
    today = date.today().isoformat()
    ev = _full_evidence()
    ev["sections"]["holding"] = {"held": True, "avg_cost": 18.5, "qty": 300, "upl": 1234.56}
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": today, "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": ev},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "持仓视角" in cap["user"]
    assert "18.50" in cap["user"] and "300" in cap["user"] and "1234.56" in cap["user"]
    assert "持有者视角" in cap["system"]


def test_report_writer_holding_section_absent_when_not_held(monkeypatch, tmp_path):
    ev = _full_evidence()  # holding=None by default (未持有)
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": ev},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "持仓视角" not in cap["user"]
    assert "持有者视角" not in cap["system"]


def test_company_drafting_index_separates_lifecycle_without_hiding_real_gaps(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    issuer = {"evidence_id": "CE-ISSUER", "type": "issuer", "usable": True,
              "status": "ok", "future": False, "stale": False}
    ev["sections"]["company_research_manifest"] = {
        "status": "incomplete", "pre_report_source_status": "complete",
        "lifecycle_pending_stages": ["risk", "report"],
        "source_register": [issuer, issuer | {"evidence_id": "CE-CONTEXT", "type": "context"},
                            issuer | {"evidence_id": "CE-FUTURE", "future": True},
                            issuer | {"evidence_id": "CE-FAILED", "usable": False}],
        "gaps": [
            {"stage": "risk", "code": "strong_claim_inventory_missing"},
            {"stage": "risk", "code": "source_failed", "input_key": "pack.chain"},
            {"stage": "profile", "code": "source_failed", "input_key": "pack.chain"},
        ],
    }
    ev["sections"]["company_financials_contract"] = {"calculations": [
        {"calculation_id": "CAL-FIN-YTD", "status": "complete", "metric": "revenue_ytd_yoy",
         "result": {"value": -0.1488, "unit": "ratio", "period": "2026-06-30"}},
        {"calculation_id": "CAL-MISSING", "status": "incomplete", "result": {"value": None}},
    ]}
    before = json.loads(json.dumps(ev))
    cap = _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": date.today().isoformat(), "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev}, _rw_response("研究证据不足"),
    )
    marker = "# 公司研究写作索引（导航，非新证据）\n"
    index = json.loads(cap["user"].split(marker)[1].splitlines()[0])
    assert [r["evidence_id"] for r in index["issuer_sources"]] == ["CE-ISSUER"]
    assert [r["evidence_id"] for r in index["context_only_sources"]] == ["CE-CONTEXT"]
    assert "registered_calculations" not in index
    assert index["calculation_locations"] == [
        {"ref": "CAL-FIN-YTD", "path": "company_financials_contract.calculations[0]"},
        {"ref": "CAL-MISSING", "path": "company_financials_contract.calculations[1]"},
    ]
    assert [g["code"] for g in index["lifecycle_baseline_gaps"]] == ["strong_claim_inventory_missing"]
    assert index["data_gaps"][0]["affected_stages"] == ["risk", "profile"]
    assert index["data_gaps"][0]["input_key"] == "pack.chain"
    assert ev == before
    assert json.loads(cap["user"].split("# 平台证据 (确定性 — 引用须带出处, 数字禁编造)\n")[1]
                      .splitlines()[0]) == before["sections"]


def test_company_drafting_index_never_projects_absent_verified_sections():
    assert rw_mod._company_drafting_index({}) == {}
    assert rw_mod._company_drafting_index({"financials": {"rows": []}}) == {}


def test_company_calculation_navigation_is_exact_locations_not_certification():
    sections = {
        "company_research_manifest": {"source_register": []},
        "company_valuation_dag": {"status": "incomplete", "nodes": [
            {"calc_id": "valuation.pb", "result": 4.43, "status": "complete"},
            {"calc_id": "valuation.enterprise_value", "result": None, "status": "incomplete"},
            {"result": 999}, None,
        ]},
        "company_vibe_valuation": {"calculations": [
            {"calculation_id": "calc-real", "calc_id": "different-alias", "status": "failed"},
        ]},
        "untrusted_extra": {"calculations": [{"calculation_id": "do-not-index"}]},
    }
    before = json.loads(json.dumps(sections))
    locations = rw_mod._company_drafting_index(sections)["calculation_locations"]
    assert locations == [
        {"ref": "valuation.pb", "path": "company_valuation_dag.nodes[0]"},
        {"ref": "valuation.enterprise_value", "path": "company_valuation_dag.nodes[1]"},
        {"ref": "calc-real", "path": "company_vibe_valuation.calculations[0]"},
    ]
    assert all(set(row) == {"ref", "path"} for row in locations)
    assert sections == before
    assert "valuation.pb" in rw_mod.COMPANY_RISK_INPUT_PROMPT
    assert "标记只是结构编号" in rw_mod.COMPANY_RISK_INPUT_PROMPT


def test_report_writer_company_mode_uses_vibe_sections_and_suppresses_legacy_blocks(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["company_research_manifest"] = {
        "as_of": "2026-07-12", "status": "incomplete",
        "completion_status": "incomplete", "freshness_status": "fresh",
    }
    body = """<!-- company-research-status: incomplete -->
# 兆易创新公司研究

本报告仅供研究参考，不构成投资建议，非个性化，未考虑任何读者的财务状况、风险承受能力或投资目标。

## [section:company_profile] 结论摘要
证据链仍有缺口。
## [section:financials] 事实
待证据引用。
## [section:expectations] 推断
待证据引用。
## [section:valuation] 估值
待计算引用。
## [section:risk_catalysts] 风险与反证
待结构化反证。
## [section:scenarios_verdict] 裁决点
待三个可证伪裁决点。
## [section:sources_gaps] 数据缺口
风险合同未完成。
"""
    cap = _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev},
        _rw_response(body),
    )

    assert "公司研究归档模式" in cap["system"]
    for title in ("结论摘要", "事实", "推断", "估值", "风险与反证", "裁决点", "数据缺口"):
        assert title in cap["system"]
    assert "目标价、隐含价格或其他价格锚" in cap["system"]
    assert "company_risk_input" in cap["system"]
    assert "strong_claims" in cap["system"]
    assert "counter_evidence" in cap["system"]
    assert '"relative_direction": "disconfirming"' in cap["system"]
    assert "decision_points 至少 3 个" in cap["system"]
    assert "不能作为整行数字的笼统背书" in cap["system"]
    assert "prompt-facing local payload" in cap["system"]
    assert "不得自行四舍五入" in cap["system"]
    assert "[assumption:ASSUMPTION_ID]" in cap["system"]
    assert "不得给自选阈值硬贴 evidence 引用" in cap["system"]
    assert "report-writer 运行前的上游清单" in cap["system"]
    assert "risk/report" in cap["system"]
    assert "不能仅据此返回 null" in cap["system"]
    assert "pre_report_source_status" in cap["system"]
    assert "lifecycle_pending_stages" in cap["system"]
    assert "确定性 risk/report Gate 裁决" in cap["system"]
    assert "incomplete/failed 时不得声明 complete" in cap["system"]
    assert "只要 source_register 仍有可用 issuer 来源" in cap["system"]
    assert "company_research_manifest.source_register[].evidence_id" in cap["system"]
    assert "type=context" in cap["system"]
    assert "[claim:CLAIM_ID]" in cap["system"]
    assert "[decision:POINT_ID]" in cap["system"]
    assert "price target" in cap["system"]
    assert "implied price" in cap["system"]
    assert "summary_json 必须省略" in cap["system"]
    assert "target_price_high/base" not in cap["system"]
    assert "position_sizing_advice" not in cap["system"]
    assert "rating_status=not_rated" in cap["system"]
    assert "券商评级与目标价 (确定性" not in cap["user"]
    assert "股东与主力 (确定性" not in cap["user"]
    written = (tmp_path / "SH603986_2026-07-12.md").read_text(encoding="utf-8")
    assert "Post-write sanity overrides" not in written
    company_output = rw_mod.ReportOutput(**cap["output"])
    assert company_output.report_kind == "company_research"
    assert company_output.rating_status == "not_rated"
    assert company_output.rating_overall is None
    assert company_output.rating_dimensions == {}
    assert company_output.action is None
    assert company_output.target_price is None
    assert company_output.stop_loss is None
    assert company_output.position_pct is None


def test_report_writer_company_mode_preserves_top_level_risk_input_in_sidecar(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["company_research_manifest"] = {
        "as_of": "2026-07-12", "status": "complete",
        "completion_status": "complete", "freshness_status": "fresh",
        "source_register": [{
            "source_id": "pack.announcements", "evidence_id": "EV-ANN",
            "type": "issuer", "status": "ok", "stale": False,
        }],
    }
    risk_input = {
        "strong_claims": [{
            "kind": "strong_claim", "claim_id": "claim-growth",
            "claim": "盈利修复可以延续。", "strong_claim": True,
            "source_refs": ["EV-ANN"],
        }],
        "counter_evidence": [{
            "kind": "counter_evidence", "claim_id": "claim-growth",
            "relative_direction": "disconfirming",
            "counter_evidence": "公告显示客户验收延后。",
            "source_refs": ["EV-ANN"],
        }],
        "decision_points": [{
            "kind": "decision_point", "point_id": f"dp-{index}",
            "question": f"第 {index} 个待裁决问题",
            "metric": "公告进度", "event": None,
            "threshold": "按期完成", "resolution_condition": None,
            "deadline": deadline, "status": "pending",
            "source_refs": ["EV-ANN"], "impact": "改变盈利兑现判断",
        } for index, deadline in enumerate(
            ("2026-08-31", "2026-09-30", "2026-10-31"), start=1
        )],
        "catalysts": [],
    }
    response = _rw_response(_MD_BODY_TEMPLATE)
    response["summary_json"] = {
        "existing_summary_field": "preserve-me",
        "company_risk_input": {"nested_stale_copy": True},
        "rating_overall": 9,
        "rating_dimensions": {"fundamental": 2},
        "action": "buy", "target_price": 99, "stop_loss": 88,
        "stop": 77, "implied_price": 66, "position_pct": 0.1,
        "nested": {
            "report_kind": "investment_report", "rating_status": "rated",
            "rating_overall": 8, "price_anchor": 55, "safe": "keep",
        },
    }
    response["company_risk_input"] = risk_input

    _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev},
        response,
    )

    sidecar = json.loads(
        (tmp_path / "SH603986_2026-07-12.json").read_text(encoding="utf-8")
    )
    assert sidecar["existing_summary_field"] == "preserve-me"
    assert sidecar["company_risk_input"] == risk_input
    assert "nested_stale_copy" not in sidecar["company_risk_input"]
    assert sidecar["report_kind"] == "company_research"
    assert sidecar["rating_status"] == "not_rated"
    assert "rating_overall" not in sidecar
    assert "rating_dimensions" not in sidecar
    assert sidecar["nested"] == {"safe": "keep"}
    forbidden_keys = {
        "rating_overall", "rating_dimensions",
        "action", "target_price", "price_target", "stop", "stop_loss",
        "implied_price", "price_anchor", "position_pct",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert not (keys(sidecar) & forbidden_keys)


def test_report_writer_company_mode_missing_risk_input_is_explicit_null(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["company_research_manifest"] = {
        "as_of": "2026-07-12", "status": "incomplete",
        "completion_status": "incomplete", "freshness_status": "fresh",
        "source_register": [],
    }
    response = _rw_response(_MD_BODY_TEMPLATE)
    response["summary_json"] = {"existing_summary_field": "preserve-me"}

    _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev},
        response,
    )

    sidecar = json.loads(
        (tmp_path / "SH603986_2026-07-12.json").read_text(encoding="utf-8")
    )
    assert "company_risk_input" in sidecar
    assert sidecar["company_risk_input"] is None
    assert sidecar["existing_summary_field"] == "preserve-me"


def test_report_writer_company_mode_accepts_explicit_not_rated_without_fake_score(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["company_research_manifest"] = {
        "as_of": "2026-07-12", "status": "incomplete",
        "completion_status": "incomplete", "freshness_status": "fresh",
        "source_register": [],
    }
    response = _rw_response(_MD_BODY_TEMPLATE)
    response["rating_overall"] = "not_rated"
    response["rating_dimensions"] = {
        "fundamental": "not_rated", "technical": "not_rated",
    }
    response["summary_json"] = {"research_scope": "company"}

    cap = _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev},
        response,
    )

    sidecar = json.loads(
        (tmp_path / "SH603986_2026-07-12.json").read_text(encoding="utf-8")
    )
    assert sidecar["rating_status"] == "not_rated"
    assert "rating_overall" not in sidecar
    assert "rating_dimensions" not in sidecar
    assert sidecar["research_scope"] == "company"
    output = rw_mod.ReportOutput(**cap["output"])
    assert output.report_kind == "company_research"
    assert output.rating_status == "not_rated"
    assert output.rating_overall is None
    assert output.rating_dimensions == {}
    assert output.action is None
    assert output.target_price is None
    assert output.stop_loss is None
    assert output.position_pct is None


def test_report_output_rejects_company_research_with_legacy_trade_values():
    with pytest.raises(ValueError, match="company_research 不得携带投资评分"):
        rw_mod.ReportOutput(
            output_md_path="report.md",
            output_json_path="report.json",
            report_kind="company_research",
            rating_status="not_rated",
            rating_overall=0,
            rating_dimensions={},
            action=None,
            target_price=None,
            stop_loss=None,
            position_pct=None,
        )


def test_report_writer_legacy_mode_still_rejects_not_rated_sentinel(
        monkeypatch, tmp_path):
    response = _rw_response(_MD_BODY_TEMPLATE)
    response["rating_overall"] = "not_rated"
    with pytest.raises(ValueError, match="invalid literal for int"):
        _run(
            monkeypatch, rw_mod, rw_mod.ReportWriter,
            {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
             "quote-fetcher": _quote()},
            response,
        )


def test_report_writer_company_mode_without_summary_json_keeps_risk_input(
        monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["company_research_manifest"] = {
        "as_of": "2026-07-12", "status": "complete",
        "completion_status": "complete", "freshness_status": "fresh",
        "source_register": [{
            "source_id": "pack.issuer", "evidence_id": "EV-ISSUER",
            "type": "issuer", "status": "ok", "stale": False,
        }],
    }
    response = _rw_response(_MD_BODY_TEMPLATE)
    response.pop("summary_json")
    response["company_risk_input"] = {
        "strong_claims": [],
        "counter_evidence": [],
        "decision_points": [],
        "catalysts": [],
    }

    _run(
        monkeypatch, rw_mod, rw_mod.ReportWriter,
        {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
         "quote-fetcher": _quote(), "evidence-loader": ev},
        response,
    )

    sidecar = json.loads(
        (tmp_path / "SH603986_2026-07-12.json").read_text(encoding="utf-8")
    )
    assert sidecar["company_risk_input"] == response["company_risk_input"]
    assert sidecar["report_kind"] == "company_research"
    assert sidecar["rating_status"] == "not_rated"
    assert "rating_overall" not in sidecar
    assert "rating_dimensions" not in sidecar
    assert "action" not in sidecar
    assert "target_price" not in sidecar
    assert "stop_loss" not in sidecar
    assert "position_pct" not in sidecar


# ---------------------------------------------------------------------------
# 8. yaml wiring:两份 yaml 一致 + 七节点 evidence-loader 接线 + quote-fetcher deps 不加
# ---------------------------------------------------------------------------

_REPO_YAML = _REPO_ROOT / "config" / "swarm" / "stock-deep-dive.yaml"
_BUNDLED_YAML = _REPO_ROOT / "engine" / "financial_analyst" / "_resources" / "config" / "swarm" / "stock-deep-dive.yaml"

_SEVEN = ["fundamental-analyst", "technical-analyst", "whale-analyst",
          "bull-advocate", "bear-advocate", "risk-officer", "report-writer"]


def test_yaml_repo_and_bundled_still_identical():
    assert _REPO_YAML.read_text(encoding="utf-8") == _BUNDLED_YAML.read_text(encoding="utf-8")


def test_yaml_seven_nodes_wire_evidence_loader():
    import yaml
    cfg = yaml.safe_load(_REPO_YAML.read_text(encoding="utf-8"))
    by_name = {a["name"]: a for a in cfg["agents"]}
    for name in _SEVEN:
        node = by_name[name]
        assert "evidence-loader" in node.get("deps", []), f"{name} 缺 deps evidence-loader(deps 是唯一生效字段)"
        assert "evidence-loader" in node.get("input_keys", []), f"{name} 缺 input_keys evidence-loader"
        assert "evidence-loader" in node.get("soft_deps", []), f"{name} 缺 soft_deps evidence-loader"


def test_yaml_bull_bear_risk_quote_fetcher_input_key_only():
    import yaml
    cfg = yaml.safe_load(_REPO_YAML.read_text(encoding="utf-8"))
    by_name = {a["name"]: a for a in cfg["agents"]}
    for name in ("bull-advocate", "bear-advocate", "risk-officer"):
        node = by_name[name]
        assert "quote-fetcher" in node["input_keys"], f"{name} 缺 input_keys quote-fetcher"
        assert "quote-fetcher" not in node["deps"], f"{name} 的 deps 不该加 quote-fetcher(不改波次)"
