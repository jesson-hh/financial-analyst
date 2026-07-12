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
3. report-writer 徽章:每个大段落(中文数字编号标题 + 新闻情绪研判)尾部注证据 as_of
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
from pathlib import Path
from typing import Any, Dict

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
        _CAP["user"] = messages[-1]["content"]
        _CAP["messages"] = list(messages)
        return {"choices": [{"message": {"content": _CAP["_response"]}}]}


def _run(monkeypatch, mod, agent_cls, inputs: dict, response: dict) -> Dict[str, Any]:
    _CAP.clear()
    _CAP["_response"] = json.dumps(response, ensure_ascii=False)
    monkeypatch.setattr(mod, "LLMClient", _CapLLM)
    agent = agent_cls(memory_root=_MEMORIES)
    asyncio.run(agent._execute(inputs))
    return _CAP


def _extract_evidence_json(text: str) -> dict:
    marker = "# 平台证据 (确定性 — 引用须带出处, 数字禁编造)\n"
    idx = text.index(marker) + len(marker)
    line = text[idx:].split("\n", 1)[0]
    return json.loads(line)


def _full_evidence() -> dict:
    return {
        "ok": True,
        "generated_at": "2026-07-12T09:30:00",
        "sections": {
            "quote_live": {"as_of": "2026-07-12T09:29:50", "price": 12.3, "pct": 1.1},
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
        },
        "errors": {},
    }


def _quote(mv_yi=150.0, pe=180.0, ret_60d=0.62, price=23.4) -> dict:
    return {"code": "SH603986", "asof_date": "2026-07-12", "price": price,
            "pe": pe, "mv_yi": mv_yi, "ret_60d": ret_60d, "name": "兆易创新"}


# ---------------------------------------------------------------------------
# 1. fundamental-analyst: chain/mainline/quant
# ---------------------------------------------------------------------------

def test_fundamental_with_ev_includes_evidence_block(monkeypatch):
    cap = _run(monkeypatch, fa_mod, fa_mod.FundamentalAnalyst,
               {"quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               {"valuation_score": 1, "mv_tier": "mid"})
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == {"chain", "mainline", "quant"}
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
    assert set(picked.keys()) == {"sentiment", "fundflow", "board_eco"}
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
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "平台证据" in cap["user"]
    picked = _extract_evidence_json(cap["user"])
    assert set(picked.keys()) == set(_full_evidence()["sections"].keys())


def test_report_writer_no_ev_omits_marker_keeps_old_text(monkeypatch, tmp_path):
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote()},
               _rw_response(_MD_BODY_TEMPLATE))
    assert "平台证据" not in cap["system"] and "平台证据" not in cap["user"]
    assert "持仓视角" not in cap["system"] and "持仓视角" not in cap["user"]
    assert "You are the chief analyst writing the final research report" in cap["system"]
    # 既有确定性段落(券商/股东)不受影响
    assert "券商评级与目标价" in cap["user"]
    assert "股东与主力" in cap["user"]


def test_report_writer_badges_cite_section_as_of(monkeypatch, tmp_path):
    res = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote(), "evidence-loader": _full_evidence()},
               _rw_response(_MD_BODY_TEMPLATE))
    md_path = None
    # locate written md via out_dir glob (deterministic name {code}_{asof}.md)
    md_path = tmp_path / "SH603986_2026-07-12.md"
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "〔证据 as_of: 2026-07-11〕" in text     # 三、基本面 → chain(无as_of)/mainline
    assert "〔证据 as_of: 2026-07-12T08:00:00〕" in text  # 二、市场环境 → macro; 七、操作建议 → quant(无as_of)/macro
    assert "平台证据缺失" not in text


def test_report_writer_badges_honest_fallback_when_ev_missing(monkeypatch, tmp_path):
    res = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
                "quote-fetcher": _quote()},
               _rw_response(_MD_BODY_TEMPLATE))
    md_path = tmp_path / "SH603986_2026-07-12.md"
    text = md_path.read_text(encoding="utf-8")
    assert text.count("〔平台证据缺失,本段基于引擎自采数据〕") == 4  # 一/二/三/七 四个大段落
    assert "〔证据 as_of" not in text


def test_report_writer_holding_section_when_held(monkeypatch, tmp_path):
    ev = _full_evidence()
    ev["sections"]["holding"] = {"held": True, "avg_cost": 18.5, "qty": 300, "upl": 1234.56}
    cap = _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
               {"code": "SH603986", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
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
