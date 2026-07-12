"""Task 7:introspector 数字溯源门 + sanity 回写 json + 依赖卫生/死规则清理。

零网络零真 LLM:LLMClient.for_agent 全桩(照 tests/test_report_prompt_evidence.py 的
_CapLLM 同款)。覆盖:

1. `_check_provenance` 纯函数用例矩阵(不经 LLM,不经 agent):正常/目标价异常/止损异常/
   比值边界不算异常/veto 冲突/position_pct=0 时无冲突/市值偏差/市值在阈值内不算偏差/
   字段缺失跳过(绝不猜字段名/绝不崩)/全空。
2. introspector `_execute` 全链(桩 LLM):
   (a) ctx 含 evidence/quote 键(且真实数字确实塞进去,不是占位)。
   (b) violations 非空 → 对已落盘 md 追加「## ⚠ 未溯源数字」尾节(append-only,原内容
       仍在最前);violations 空 → md 文件字节不变(不碰)。
   (c) 确定性 violations 与 LLM 列举的 violations 合并去重(同一条不重复出现在 md 里)。
   (d) position_pct>0 但 risk-officer.veto_flags 非空 → 经 _execute 里的 merge 逻辑被
       _check_provenance 抓到(veto 真值来自 risk-officer 自己的输出,不是猜的)。
3. report_writer sanity 回写 json(⑥根修):LLM 返回内部不自洽的 rating_overall/action/
   position_pct + 一份"过期"的 summary_json,sanity 修正后落盘的 .json 必须反映修正后的
   数字(而不是 LLM 原始值);LLM 自己在 summary_json 里塞的自由字段原样保留(merge 不是
   replace);.md 的 "Post-write sanity overrides" 附注逻辑不受影响。
4. 依赖卫生 yaml 断言:两份 yaml 字节一致 + risk-officer 的 news-reader/f10-reader 保留在
   deps 同时新增进 soft_deps + introspector 的 evidence-loader/quote-fetcher 三处(deps/
   soft_deps/input_keys)全部就位。
5. risk-officer 死规则清理:SYSTEM_PROMPT 不再提 SUPER_DISTR/BROKEN BOARD(factor-computer
   退役连带的两条死规则已删),留一行退役说明注释;quote 相关规则改写为引用真值字段
   quote_summary(而非含糊的 "if quote shows ...")。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENGINE = _REPO_ROOT / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.agent.tier3 import introspector as intro_mod  # noqa: E402
from financial_analyst.agent.tier3 import report_writer as rw_mod  # noqa: E402
from financial_analyst.agent.tier3 import risk_officer as risk_mod  # noqa: E402

_MEMORIES = _REPO_ROOT / "memories"
_REPO_YAML = _REPO_ROOT / "config" / "swarm" / "stock-deep-dive.yaml"
_BUNDLED_YAML = (
    _REPO_ROOT / "engine" / "financial_analyst" / "_resources" / "config"
    / "swarm" / "stock-deep-dive.yaml"
)

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


def _quote(price=23.4, mv_yi=150.0, pe=18.0, ret_60d=0.05) -> dict:
    return {"code": "SH600000", "asof_date": "2026-07-12", "price": price,
            "pe": pe, "mv_yi": mv_yi, "ret_60d": ret_60d, "name": "浦发银行"}


def _full_evidence() -> dict:
    return {
        "ok": True,
        "generated_at": "2026-07-12T09:30:00",
        "sections": {
            "quote_live": {"as_of": "2026-07-12T09:29:50", "price": 23.4, "pct": 1.1},
            "sentiment": {"as_of": "2026-07-12", "tag": "偏多", "read": "情绪回暖"},
        },
        "errors": {},
    }


def _empty_llm_response(**overrides) -> dict:
    out = {"quality_flags": [], "proposals": [], "summary": "", "provenance_violations": []}
    out.update(overrides)
    return out


# ---------------------------------------------------------------------------
# 1. _check_provenance 纯函数用例矩阵
# ---------------------------------------------------------------------------

def test_check_provenance_normal_no_violations():
    v = intro_mod._check_provenance(
        {"target_price": 25.0, "stop_loss": 20.0, "position_pct": 0.03},
        {"price": 23.4, "mv_yi": 150.0},
        {},
    )
    assert v == []


def test_check_provenance_target_price_ratio_violation():
    v = intro_mod._check_provenance(
        {"target_price": 200.0, "stop_loss": 20.0},
        {"price": 23.4},
        {},
    )
    assert len(v) == 1
    assert "目标价/止损相对现价异常" in v[0]
    assert "目标价" in v[0]


def test_check_provenance_stop_loss_ratio_violation():
    v = intro_mod._check_provenance(
        {"target_price": 25.0, "stop_loss": 1.0},   # 1.0 / 23.4 ≈ 0.043 < 0.2
        {"price": 23.4},
        {},
    )
    assert len(v) == 1
    assert "止损" in v[0]


def test_check_provenance_ratio_boundary_is_not_violation():
    # 精确落在闭区间 [0.2, 5] 边界上不算异常
    v = intro_mod._check_provenance(
        {"target_price": 23.4 * 5, "stop_loss": 23.4 * 0.2},
        {"price": 23.4},
        {},
    )
    assert v == []


def test_check_provenance_veto_conflict_violation():
    v = intro_mod._check_provenance(
        {"position_pct": 0.05, "veto_flags": ["game_capital_speculation"]},
        {"price": 23.4},
        {},
    )
    assert len(v) == 1
    assert "veto" in v[0]


def test_check_provenance_no_conflict_when_position_zero_despite_veto():
    v = intro_mod._check_provenance(
        {"position_pct": 0.0, "veto_flags": ["game_capital_speculation"]},
        {"price": 23.4},
        {},
    )
    assert v == []


def test_check_provenance_mv_deviation_violation():
    v = intro_mod._check_provenance(
        {"mv_yi": 500.0},
        {"price": 23.4, "mv_yi": 150.0},   # 偏差 (500-150)/150 ≈ 233% >> 30%
        {},
    )
    assert len(v) == 1
    assert "市值" in v[0]


def test_check_provenance_mv_within_threshold_not_violation():
    v = intro_mod._check_provenance(
        {"mv_yi": 180.0},
        {"price": 23.4, "mv_yi": 150.0},   # 偏差 20% < 30%
        {},
    )
    assert v == []


def test_check_provenance_missing_fields_skipped_honestly():
    # quote 无 price/mv_yi、summary 无 target_price/stop_loss/position_pct/mv 类字段
    # → 三条检查全跳过,绝不崩、绝不猜字段名。
    v = intro_mod._check_provenance(
        {"action": "hold"},
        {},
        {"sections": {"quote_live": {}}},
    )
    assert v == []


def test_check_provenance_all_empty_returns_empty_list():
    assert intro_mod._check_provenance({}, {}, {}) == []
    assert intro_mod._check_provenance(None, None, None) == []


# ---------------------------------------------------------------------------
# 2. introspector._execute 全链(桩 LLM)
# ---------------------------------------------------------------------------

def test_introspector_ctx_includes_evidence_and_quote_keys(monkeypatch):
    cap = _run(monkeypatch, intro_mod, intro_mod.Introspector,
               {"report-writer": {"output_md_path": "", "rating_overall": 5, "action": "hold",
                                   "target_price": 25.0, "stop_loss": 20.0, "position_pct": 0.03},
                "quote-fetcher": _quote(),
                "evidence-loader": _full_evidence()},
               _empty_llm_response())
    user = cap["user"]
    assert '"evidence"' in user
    assert '"quote"' in user
    assert '"mv_yi": 150.0' in user       # quote 的真实数字确实塞进去了,不是占位
    assert "generated_at" in user          # evidence pack 内容确实塞进去了


def test_introspector_appends_unsourced_section_when_violations_present(monkeypatch, tmp_path):
    md_file = tmp_path / "SH600001_2026-07-12.md"
    md_file.write_text("# 研报正文\n内容...\n", encoding="utf-8")
    inputs = {
        "report-writer": {"output_md_path": str(md_file), "rating_overall": 5, "action": "hold",
                           "target_price": 999.0,  # 相对现价比值超 5 → 触发确定性 violation
                           "stop_loss": 20.0, "position_pct": 0.03},
        "quote-fetcher": _quote(price=23.4),
        "evidence-loader": {},
    }
    _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, _empty_llm_response())
    text = md_file.read_text(encoding="utf-8")
    assert text.startswith("# 研报正文\n内容...\n"), "append-only——原内容仍在最前"
    assert "## ⚠ 未溯源数字(introspector 校验)" in text
    assert "目标价" in text


def test_introspector_does_not_touch_md_when_no_violations(monkeypatch, tmp_path):
    md_file = tmp_path / "SH600002_2026-07-12.md"
    original = "# 研报正文\n干净内容\n"
    md_file.write_text(original, encoding="utf-8")
    inputs = {
        "report-writer": {"output_md_path": str(md_file), "rating_overall": 5, "action": "hold",
                           "target_price": 25.0, "stop_loss": 20.0, "position_pct": 0.03},
        "quote-fetcher": _quote(price=23.4),
    }
    _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, _empty_llm_response())
    assert md_file.read_text(encoding="utf-8") == original, "无 violation 时绝不碰已落盘 md"


def test_introspector_merges_llm_and_deterministic_violations(monkeypatch, tmp_path):
    md_file = tmp_path / "SH600003_2026-07-12.md"
    md_file.write_text("# body\n", encoding="utf-8")
    inputs = {
        "report-writer": {"output_md_path": str(md_file), "rating_overall": 5, "action": "hold",
                           "target_price": 999.0, "stop_loss": 20.0, "position_pct": 0.03},
        "quote-fetcher": _quote(price=23.4),
    }
    llm_resp = _empty_llm_response(
        provenance_violations=["市值50亿(§三 基本面)——证据与现价均未提供来源"]
    )
    _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, llm_resp)
    text = md_file.read_text(encoding="utf-8")
    assert text.count("目标价/止损相对现价异常") == 1   # 确定性那条
    assert "市值50亿" in text                              # LLM 列举那条


def test_introspector_dedups_identical_violation_from_llm_and_deterministic(monkeypatch, tmp_path):
    md_file = tmp_path / "SH600004_2026-07-12.md"
    md_file.write_text("# body\n", encoding="utf-8")
    inputs = {
        "report-writer": {"output_md_path": str(md_file), "rating_overall": 5, "action": "hold",
                           "target_price": 999.0, "stop_loss": 20.0, "position_pct": 0.03},
        "quote-fetcher": _quote(price=23.4),
    }
    # 先独立算出确定性 violation 的确切字符串,避免两处硬编码同一份格式化文本
    det = intro_mod._check_provenance({"target_price": 999.0, "stop_loss": 20.0}, {"price": 23.4}, {})
    assert det, "sanity: 该输入应触发至少一条确定性 violation"
    llm_resp = _empty_llm_response(provenance_violations=[det[0]])  # LLM 复述了同一条
    _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, llm_resp)
    text = md_file.read_text(encoding="utf-8")
    assert text.count(det[0]) == 1, "去重后不该重复出现"


def test_introspector_veto_conflict_detected_via_risk_officer_merge(monkeypatch, tmp_path):
    """summary_json(writer_out)本身不带 veto_flags——_execute 须从 risk-officer 自己的
    输出补真值进去,_check_provenance 才能抓到 position_pct>0 与 veto 的冲突。"""
    md_file = tmp_path / "SH600005_2026-07-12.md"
    md_file.write_text("# body\n", encoding="utf-8")
    inputs = {
        "report-writer": {"output_md_path": str(md_file), "rating_overall": -5, "action": "avoid",
                           "target_price": 20.0, "stop_loss": 18.0, "position_pct": 0.03},
        "risk-officer": {"veto_flags": ["recent_severe_negative_event"]},
        "quote-fetcher": _quote(price=23.4),
    }
    _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, _empty_llm_response())
    text = md_file.read_text(encoding="utf-8")
    assert "risk-officer veto 生效" in text


def test_introspector_missing_md_path_skips_append_without_crashing(monkeypatch):
    # report-writer 输出没给 output_md_path(极端降级场景)——即使 det violation 触发,
    # 也不该抛异常;只是无处可追加。
    inputs = {
        "report-writer": {"rating_overall": 5, "action": "hold",
                           "target_price": 999.0, "stop_loss": 20.0, "position_pct": 0.03},
        "quote-fetcher": _quote(price=23.4),
    }
    cap = _run(monkeypatch, intro_mod, intro_mod.Introspector, inputs, _empty_llm_response())
    assert cap  # 跑完没崩(asyncio.run 没抛)


# ---------------------------------------------------------------------------
# 3. report_writer sanity 回写 json(⑥根修)
# ---------------------------------------------------------------------------

def test_report_writer_json_reflects_sanity_corrected_values(monkeypatch, tmp_path):
    response = {
        "markdown_body": "# stub report\n",
        "rating_overall": 9,     # 与 dims 之和(=2)严重不符,须被 sanity 覆盖
        "rating_dimensions": {"fundamental": 1, "technical": 1, "whale": 1, "risk": -1},
        "action": "buy",
        "target_price": 30.0,
        "stop_loss": 20.0,
        "position_pct": 0.05,    # veto 生效应被强制清零
        "summary_json": {
            "rating_overall": 9, "action": "buy",
            "target_price": 999.0, "stop_loss": 1.0, "position_pct": 0.05,
            "note": "llm free field",   # LLM 自己塞的自由字段,须原样保留
        },
    }
    _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
         {"code": "SH600000", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
          "quote-fetcher": _quote(), "risk-officer": {"veto_flags": ["recent_severe_negative_event"]}},
         response)

    json_path = tmp_path / "SH600000_2026-07-12.json"
    saved = json.loads(json_path.read_text(encoding="utf-8"))

    assert saved["rating_overall"] == 2, "rating_overall 须被 sum(dimensions) 覆盖,不是 LLM 原始的 9"
    assert saved["action"] == "hold", "veto 生效强制 0 仓位后,action 须按修正后 position_pct/rating 重新派生"
    assert saved["target_price"] == 30.0
    assert saved["stop_loss"] == 20.0
    assert saved["position_pct"] == 0.0, "veto 生效 → position_pct 须强制归零,且这个归零后的值须落盘"
    assert saved["note"] == "llm free field", "LLM 自己的自由字段须原样保留(merge 不是 replace)"
    assert saved["rating_dimensions"] == {"fundamental": 1, "technical": 1, "whale": 1, "risk": -1}

    md_text = (tmp_path / "SH600000_2026-07-12.md").read_text(encoding="utf-8")
    assert "Post-write sanity overrides" in md_text, ".md 的附注逻辑不受这次 json 回写改动影响"


def test_report_writer_json_rewrite_handles_missing_summary_json_key(monkeypatch, tmp_path):
    """LLM 完全没给 summary_json 键(旧行为的兜底路径:json.dumps(parsed) 整份落盘)——
    加了 sanity 回写后这条路径依然不能崩,且修正后的六个字段仍须出现在落盘内容里。"""
    response = {
        "markdown_body": "# stub\n",
        "rating_overall": -20,     # 会被 clamp 到 -10
        "rating_dimensions": {},
        "action": "hold",
        "target_price": 15.0,
        "stop_loss": 10.0,
        "position_pct": 0.02,
    }
    _run(monkeypatch, rw_mod, rw_mod.ReportWriter,
         {"code": "SH600006", "asof_date": "2026-07-12", "out_dir": str(tmp_path),
          "quote-fetcher": _quote()},
         response)
    json_path = tmp_path / "SH600006_2026-07-12.json"
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["rating_overall"] == -10
    # rating_overall clamp 到 -10 (<=0,无 veto) → sanity 同时把 position_pct 强制归零;
    # 这条修正也须体现在落盘的 json 里(不是 LLM 原始的 0.02)。
    assert saved["position_pct"] == 0.0


# ---------------------------------------------------------------------------
# 4. 依赖卫生 yaml 断言(两份 yaml 字节一致 + soft 化到位)
# ---------------------------------------------------------------------------

def test_yaml_repo_and_bundled_identical_t7():
    assert _REPO_YAML.read_text(encoding="utf-8") == _BUNDLED_YAML.read_text(encoding="utf-8")


def test_yaml_risk_officer_readers_kept_in_deps_and_added_to_soft():
    for path in (_REPO_YAML, _BUNDLED_YAML):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_name = {a["name"]: a for a in cfg["agents"]}
        ro = by_name["risk-officer"]
        for dep in ("news-reader", "f10-reader"):
            assert dep in ro["deps"], f"{path}: risk-officer.deps 须仍含 {dep}(不是移除,是降级)"
            assert dep in ro["soft_deps"], f"{path}: risk-officer.soft_deps 须含 {dep}(依赖卫生)"


def test_yaml_introspector_wires_evidence_and_quote_as_soft():
    for path in (_REPO_YAML, _BUNDLED_YAML):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_name = {a["name"]: a for a in cfg["agents"]}
        intro = by_name["introspector"]
        for dep in ("evidence-loader", "quote-fetcher"):
            assert dep in intro["deps"], f"{path}: introspector.deps 须含 {dep}"
            assert dep in intro["input_keys"], f"{path}: introspector.input_keys 须含 {dep}"
            assert dep in intro.get("soft_deps", []), f"{path}: introspector.soft_deps 须含 {dep}(soft)"


# ---------------------------------------------------------------------------
# 5. risk-officer 死规则清理(SYSTEM_PROMPT 断言)
# ---------------------------------------------------------------------------

def test_risk_officer_prompt_drops_factor_computer_hard_rules():
    prompt = risk_mod.SYSTEM_PROMPT
    # 死规则本体(编号规则 + 触发动作)须被删除——SUPER_DISTR/BROKEN BOARD 的名字仍允许
    # 出现在退役说明注释里(下一个测试断言),这里断言的是"规则条款"不再存在。
    assert "SUPER_DISTR REDUCTION:" not in prompt, "SUPER_DISTR REDUCTION 规则条款须删除"
    assert "BROKEN BOARD:" not in prompt, "BROKEN BOARD 规则条款须删除"
    assert 'veto_flags += ["super_distribution_active"]' not in prompt
    assert 'veto_flags += ["broken_board"]' not in prompt
    assert "factor-computer.vol_regime" not in prompt
    assert "factor-computer.board_score" not in prompt


def test_risk_officer_prompt_notes_retirement_and_rewrites_quote_rule():
    prompt = risk_mod.SYSTEM_PROMPT
    assert "退役" in prompt and "factor-computer" in prompt, "须留一行注释说明规则依赖的节点已退役"
    # 退役说明注释里允许(且应该)点名是哪两条规则被移除——这是"注明"要求本身,不是遗留死代码。
    assert "SUPER_DISTR" in prompt and "BROKEN BOARD" in prompt
    assert "GAME-CAPITAL VETO" in prompt and "NEGATIVE EVENT VETO" in prompt, "两条真值规则须保留"
    assert "quote_summary" in prompt, "quote 规则须改写为引用真值字段 quote_summary(而非含糊的 'if quote shows')"
