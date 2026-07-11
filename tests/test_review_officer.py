# -*- coding: utf-8 -*-
"""盘后复盘官编排测试:五段+三职责+批判降级+报告落盘,全打桩零网络零 LLM。"""
import json

from guanlan_v2.autonomy import review_officer as RO, runtime as R


def _ctx(tmp_path, max_llm=12):
    return R.JobCtx(job_id="aj_t", dir=tmp_path, budget=R.Budget(max_llm),
                    progress=lambda **k: None, deadline_ts=9e18)


def _stub_all(monkeypatch, tmp_path, matured=False, sec_fail=None):
    monkeypatch.setattr(RO, "REPORTS_DIR", tmp_path / "reports")
    pairs = [{"run_id": "rs_1", "arms": {
        "data": {"ok": True, "n": 3, "matured_n": 3 if matured else 0},
        "rerank": {"ok": True, "n": 3, "matured_n": 3 if matured else 0}},
        "excess_diff": 0.01, "model": "deepseek/deepseek-reasoner"}]
    monkeypatch.setattr(RO, "_fetch_ab", lambda: {"ok": True, "pairs": pairs})
    monkeypatch.setattr(RO, "_refresh_market", lambda: {
        "ok": True, "as_of": "2026-07-12 15:00", "market_read": "偏多", "market_tilt": None})
    written = {}
    monkeypatch.setattr(RO, "_write_market", lambda *a: written.setdefault("args", a) or True)
    monkeypatch.setattr(RO, "_macro_snapshot", lambda: {"ok": True})

    def fake_section(**kw):
        nm = kw["name"]
        if sec_fail == nm:
            return {"ok": False, "name": nm, "text": "", "tool_calls": 0, "error": "boom"}
        return {"ok": True, "name": nm, "text": f"{nm} 小节", "tool_calls": 1}

    monkeypatch.setattr(RO, "run_section_agent", lambda **kw: fake_section(**kw))

    async def fake_llm(system, user, **kw):
        if "批判" in system:
            return {"ok": True, "data": {"pass": True, "issues": []}}
        if "教训" in system:
            return {"ok": True, "data": {"draft": "(行业·光芯片) 示例草稿"}}
        return {"ok": True, "data": {"morning_report_md": "# 晨报", "tomorrow_todos": ["t"]}}

    monkeypatch.setattr(RO, "_call_llm_json", fake_llm)
    return written


def _load_json(out):
    return json.loads((RO.REPORTS_DIR / (out["report"][:-3] + ".json")).read_text(encoding="utf-8"))


def test_full_run_writes_report(tmp_path, monkeypatch):
    _stub_all(monkeypatch, tmp_path)
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"] and out["report"]
    j = _load_json(out)
    assert set(j["sections"]) == {"ab", "seats", "data"}
    assert j["duties"]["market_refresh"]["ok"] and j["duties"]["macro_snapshot"]["ok"]
    assert j["duties"]["distill_draft"] is None            # 无 matured 对不起草
    md_path = RO.REPORTS_DIR / out["report"]
    assert md_path.exists() and "晨报" in md_path.read_text(encoding="utf-8")


def test_matured_pair_drafts_distill(tmp_path, monkeypatch):
    """② matured=True 才有 distill_draft,内容含「待人审」——只进报告,绝不入记忆。"""
    _stub_all(monkeypatch, tmp_path, matured=True)
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"]
    j = _load_json(out)
    draft = j["duties"]["distill_draft"]
    assert draft is not None
    assert "待人审" in json.dumps(draft, ensure_ascii=False)


def test_section_failure_degrades_only_that_section(tmp_path, monkeypatch):
    """③ sec_fail="seats" → 该段 degraded 且 job 仍 ok(诚实显形,不拖垮全局)。"""
    _stub_all(monkeypatch, tmp_path, sec_fail="seats")
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"] is True
    j = _load_json(out)
    assert j["sections"]["seats"]["degraded"] is True
    assert "boom" in (j["sections"]["seats"].get("error") or "")
    assert j["sections"]["ab"]["ok"] is True                 # 未牵连其它段


def test_budget_exhausted_degrades_subsequent_llm_steps(tmp_path, monkeypatch):
    """④ Budget(2) → 前两个 LLM 步(market_refresh/ab)吃满预算,后续 LLM 步全 degraded。"""
    _stub_all(monkeypatch, tmp_path)
    out = RO.run_review_officer(_ctx(tmp_path, max_llm=2))
    assert out["ok"] is True
    j = _load_json(out)
    assert j["budget_used"] <= 2
    assert j["sections"]["ab"]["ok"] is True                 # 前两步吃到预算
    assert j["sections"]["seats"]["degraded"] is True        # 预算耗尽后跳过
    assert j["sections"]["data"]["degraded"] is True
    assert j["duties"]["macro_snapshot"]["ok"] is True        # 零 LLM 职责不受预算影响
    assert j["critic"]["ok"] is False                         # 批判步也吃不到预算


def test_critic_reject_degrades_named_section(tmp_path, monkeypatch):
    """⑤ critic 桩返 pass:False+issues=[{"section":"ab","problem":"x"}]
    → json critic.issues 非空且 sections.ab.degraded。"""
    _stub_all(monkeypatch, tmp_path)

    async def fake_llm(system, user, **kw):
        if "批判" in system:
            return {"ok": True, "data": {"pass": False,
                    "issues": [{"section": "ab", "problem": "x"}]}}
        if "教训" in system:
            return {"ok": True, "data": {"draft": "d"}}
        return {"ok": True, "data": {"morning_report_md": "# 晨报", "tomorrow_todos": []}}

    monkeypatch.setattr(RO, "_call_llm_json", fake_llm)
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"] is True
    j = _load_json(out)
    assert j["critic"]["issues"]
    assert j["sections"]["ab"]["degraded"] is True


def test_write_market_skipped_when_as_of_none(tmp_path, monkeypatch):
    """⑥ _refresh_market 桩返 as_of=None → _write_market 不被调(written 无 args)。"""
    written = _stub_all(monkeypatch, tmp_path)
    monkeypatch.setattr(RO, "_refresh_market", lambda: {
        "ok": True, "as_of": None, "market_read": "偏多", "market_tilt": None})
    out = RO.run_review_officer(_ctx(tmp_path))
    assert out["ok"] is True
    assert "args" not in written
    j = _load_json(out)
    assert j["duties"]["market_refresh"]["ok"] is True
    assert j["duties"]["market_refresh"]["as_of"] is None


def test_read_report_latest_and_missing(tmp_path, monkeypatch):
    """read_report:空 date 取字典序最大的一份;无报告目录时诚实返回 ok:False。"""
    monkeypatch.setattr(RO, "REPORTS_DIR", tmp_path / "reports")
    assert RO.read_report("") == {"ok": False, "reason": "暂无日报"}
    RO.REPORTS_DIR.mkdir(parents=True)
    (RO.REPORTS_DIR / "2026-07-10.md").write_text("旧报", encoding="utf-8")
    (RO.REPORTS_DIR / "2026-07-11.md").write_text("新报", encoding="utf-8")
    (RO.REPORTS_DIR / "2026-07-11.json").write_text(
        json.dumps({"date": "2026-07-11"}, ensure_ascii=False), encoding="utf-8")
    r = RO.read_report()
    assert r["ok"] and r["date"] == "2026-07-11" and r["md"] == "新报"
    assert r["json"]["date"] == "2026-07-11"
    r2 = RO.read_report("2026-07-10")
    assert r2["ok"] and r2["md"] == "旧报" and r2["json"] is None   # 无同名 json 诚实返 None
