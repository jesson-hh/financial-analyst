# -*- coding: utf-8 -*-
"""盘后自主复盘官 playbook:五段(A/B 成绩单→落子复盘→数据巡检→综合晨报→批判)
+ 三职责(大盘判读日更/蒸馏草稿/macro 快照搭车),日报 md+json 落盘。

红线(全单适用,勿动):
- 只读 + 写报告:全程绝不写 picks/信号/blend/seats/记忆;蒸馏草稿只进报告,
  标"待人审·未入记忆",入记忆仍走现有 ww_rerank_distill confirm 门。
- 诚实显形:任何段/职责失败或超时/预算耗尽 → 该步标 degraded + 原因,绝不编造;
  批判不过 → 对应段标降级。报告永远落盘,**报告落盘失败才算 job 失败**。
- 外部依赖全收敛为模块级薄函数(`_fetch_ab/_refresh_market/_write_market/
  _macro_snapshot/_call_llm_json/run_section_agent`),便于测试打桩、零网络零 LLM。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from guanlan_v2.autonomy.runtime import JobCtx
from guanlan_v2.autonomy.subagent import run_section_agent  # noqa: F401 — 打桩靶点(模块级引入)
from guanlan_v2.screen.llm import _call_llm_json             # noqa: F401 — 打桩靶点

REPORTS_DIR = Path(__file__).resolve().parents[2] / "var" / "reports" / "daily"

_SECTION_SEAT = "review_section"      # fast 档(config/llm.yaml)
_OFFICER_SEAT = "review_officer"      # deep 档

# 段 agent 系统提示词共用骨架(brief 写死红线,勿改措辞)
_SECTION_SYSTEM_TMPL = (
    "你是观澜盘后复盘官的「{sec}」段分析师。只用给定工具取数,只汇报工具返回的事实,"
    "每个数字给出处(工具名);查不到就写「该项无数据」,严禁编造;输出 ≤400 字中文小节。"
)

_SEATS_BRIEF = "复盘落子近况:请用 ww_ledger_state 和 ww_picks_perf 核实台账状态与决策/后果对照,写成小节。"
_DATA_BRIEF = "数据+调度巡检:请用 ww_data_health 和 ww_model_health 核实数据新鲜度与模型体检状态,写成小节。"

_DISTILL_SYSTEM = (
    "你是观澜盘后复盘官的「教训蒸馏」起草人。基于给定 A/B 重排成熟对照数据,起草一条可入长期记忆的"
    "「行业·」教训草稿(仅起草,不写入记忆,人审后另行经 ww_rerank_distill 确认);"
    "只用给定数字,严禁编造;输出 JSON:{\"draft\": <中文教训文本>}。"
)

_REPORT_SYSTEM = (
    "你是观澜盘后复盘官的「综合晨报」撰写人。基于给定三段产物(A/B成绩单/落子复盘/数据巡检)与"
    "职责结果摘要,写一份晨报;只汇报已知事实,每个数字标注来源段,严禁编造;"
    "输出 JSON:{\"morning_report_md\": <markdown 正文>, \"tomorrow_todos\": [<字符串>...]}。"
)

_CRITIC_SYSTEM = (
    "你是观澜盘后复盘官的「批判」把关人。检查给定晨报与各段原文:每个数字是否有出处、有无编造、"
    "失败段是否如实标注;不合格的段落在 issues 里指出对应 section 名(ab/seats/data);"
    "输出 JSON:{\"pass\": <bool>, \"issues\": [{\"section\": <段名>, \"problem\": <问题>}...]}。"
)


# ── 外部依赖薄封装(模块级,便于打桩)──────────────────────────────────

def _fetch_ab() -> Dict[str, Any]:
    from guanlan_v2.console.tools import _rerank_perf_fetch
    return _rerank_perf_fetch(limit=10)


def _refresh_market() -> Dict[str, Any]:
    from guanlan_v2.screen.news import news_sentiment
    return asyncio.run(news_sentiment([]))


def _write_market(day: str, read: Optional[str], tilt: Optional[str],
                  as_of: Optional[str]) -> bool:
    from guanlan_v2.datafeed.sentiment import write_market
    return write_market(day, read, tilt, as_of, source="review_officer")


def _macro_snapshot() -> Dict[str, Any]:
    from guanlan_v2.macro.pulse import build_pulse
    return build_pulse(refresh=True)


# ── matured 判定(与 console/tools.py rerank_distill_impl 同款短路)───────

def _pair_matured(p: Dict[str, Any]) -> bool:
    arms = p.get("arms") or {}
    return all(bool(a.get("ok")) and isinstance(a.get("n"), int) and a.get("n") > 0
               and a.get("matured_n") == a.get("n")
               for a in ((arms.get("data") or {}), (arms.get("rerank") or {})))


# ── 步前门:超时/预算 ───────────────────────────────────────────────────

def _gate(ctx: JobCtx, *, need_budget: bool) -> Optional[str]:
    if ctx.over_deadline():
        return "job 已超软墙钟(deadline),该步跳过"
    if need_budget and not ctx.budget.charge():
        return "预算耗尽(LLM 调用上限已达),该步跳过"
    return None


def _call_deep(ctx: JobCtx, system: str, user: str) -> Dict[str, Any]:
    """deep 座席一次性 json 调用,统一门禁 + 异常归一。"""
    reason = _gate(ctx, need_budget=True)
    if reason:
        return {"ok": False, "reason": reason, "data": {}}
    try:
        out = asyncio.run(_call_llm_json(system, user, agent=_OFFICER_SEAT))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "data": {}}
    if not out.get("ok"):
        return {"ok": False, "reason": out.get("reason") or "LLM 调用失败", "data": {}}
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    return {"ok": True, "reason": None, "data": data}


# ── 段 agent 派工(fast 档)──────────────────────────────────────────────

def _run_section(ctx: JobCtx, *, name: str, cn: str, tools: Set[str],
                 brief: str) -> Tuple[Dict[str, Any], str]:
    reason = _gate(ctx, need_budget=True)
    if reason:
        return {"ok": False, "degraded": True, "error": reason, "tool_calls": 0}, ""
    out_path = (Path(ctx.dir) / f"sec_{name}.md") if ctx.dir else Path(f"sec_{name}.md")
    system_prompt = _SECTION_SYSTEM_TMPL.format(sec=cn)
    try:
        r = run_section_agent(name=name, system_prompt=system_prompt, brief_text=brief,
                              allowed_tools=set(tools), out_path=out_path,
                              seat=_SECTION_SEAT)
    except Exception as exc:  # noqa: BLE001
        r = {"ok": False, "name": name, "text": "", "tool_calls": 0,
             "error": f"{type(exc).__name__}: {exc}"}
    ok = bool(r.get("ok"))
    rec: Dict[str, Any] = {"ok": ok, "degraded": not ok,
                           "tool_calls": int(r.get("tool_calls") or 0)}
    if not ok:
        rec["error"] = r.get("error") or "未知失败"
    return rec, (r.get("text") or "")


def _ab_brief(ab_raw: Dict[str, Any]) -> str:
    if not ab_raw.get("ok"):
        return (f"A/B 档案读取失败:{ab_raw.get('reason') or '未知原因'}。"
                "请用 ww_rerank_perf 工具核实并如实汇报「该项无数据」。")
    pairs = ab_raw.get("pairs") or []
    return (f"以下是最近 A/B 重排对照档案摘要(共 {len(pairs)} 对),"
            "请用 ww_rerank_perf 工具核实细节并写成小节:\n"
            + json.dumps(pairs, ensure_ascii=False)[:2000])


def _distill_user(pairs: List[Dict[str, Any]]) -> str:
    matured = [p for p in pairs if _pair_matured(p)]
    return "成熟 A/B 对(两臂 ok 且 matured_n==n>0):\n" + json.dumps(matured, ensure_ascii=False)[:2000]


def _report_user(section_texts: Dict[str, str], duties: Dict[str, Any]) -> str:
    cn_map = {"ab": "A/B 成绩单", "seats": "落子复盘", "data": "数据巡检"}
    parts = [f"【{cn_map.get(k, k)}】\n{v or '(该段无数据)'}" for k, v in section_texts.items()]
    parts.append("职责结果摘要:" + json.dumps(duties, ensure_ascii=False))
    return "\n\n".join(parts)


def _critic_user(morning_md: str, section_texts: Dict[str, str]) -> str:
    cn_map = {"ab": "A/B 成绩单", "seats": "落子复盘", "data": "数据巡检"}
    parts = [f"晨报:\n{morning_md}"]
    for k, v in section_texts.items():
        parts.append(f"【{cn_map.get(k, k)}】原文:\n{v or '(该段无数据)'}")
    return "\n\n".join(parts)


def _badge(name_cn: str, rec: Dict[str, Any]) -> str:
    if rec.get("ok"):
        return f"{name_cn} ✓"
    err = rec.get("error") or rec.get("reason") or "未知原因"
    return f"{name_cn} degraded({err})"


def _assemble_md(today: str, sections: Dict[str, Dict[str, Any]], duties: Dict[str, Any],
                 critic_out: Dict[str, Any], morning_md: str, todos: List[str],
                 section_texts: Dict[str, str], distill_note: str) -> str:
    badges = [
        _badge("A/B成绩单", sections.get("ab", {})),
        _badge("落子复盘", sections.get("seats", {})),
        _badge("数据巡检", sections.get("data", {})),
        _badge("大盘判读", duties.get("market_refresh") or {}),
        _badge("macro快照", duties.get("macro_snapshot") or {}),
        _badge("批判", critic_out),
    ]
    lines = [f"# 观澜盘后复盘官日报 · {today}", "",
             "段状态(数字出处=各段工具调用):" + " · ".join(badges), "",
             morning_md or "(综合晨报缺失)", "", "---", "", "## 附录:各段原文", ""]
    cn_map = {"ab": "A/B 成绩单", "seats": "落子复盘", "data": "数据巡检"}
    for k, cn in cn_map.items():
        lines.append(f"### {cn}")
        lines.append(section_texts.get(k) or f"(该段无数据:{sections.get(k, {}).get('error', '')})")
        lines.append("")
    lines.append("### 蒸馏草稿(待人审·未入记忆)")
    dd = duties.get("distill_draft")
    lines.append(dd["draft"] if dd else distill_note)
    lines.append("")
    lines.append("## 明日待办")
    if todos:
        lines.extend(f"- {t}" for t in todos)
    else:
        lines.append("- (无)")
    return "\n".join(lines)


# ── 主编排 ──────────────────────────────────────────────────────────────

def run_review_officer(ctx: JobCtx) -> Dict[str, Any]:
    today = dt.date.today().isoformat()
    sections: Dict[str, Dict[str, Any]] = {}
    section_texts: Dict[str, str] = {}
    duties: Dict[str, Any] = {"market_refresh": None, "macro_snapshot": None,
                              "distill_draft": None}

    # ── 职责1:大盘判读日更(确定性 + 1 LLM,经 news_sentiment 内部)──────
    reason = _gate(ctx, need_budget=True)
    if reason:
        duties["market_refresh"] = {"ok": False, "as_of": None, "reason": reason}
    else:
        try:
            mk = _refresh_market() or {}
        except Exception as exc:  # noqa: BLE001
            mk = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        as_of = mk.get("as_of")
        if mk.get("ok") and as_of:
            try:
                _write_market(today, mk.get("market_read"), mk.get("market_tilt"), as_of)
            except Exception:  # noqa: BLE001 — 落盘失败不拖垮职责本身的诚实记录
                pass
        duties["market_refresh"] = {"ok": bool(mk.get("ok")), "as_of": as_of,
                                    "reason": None if mk.get("ok") else mk.get("reason")}

    # ── 段A:A/B 成绩单(确定性预取 + 段 agent fast 档)───────────────
    deadline_reason = _gate(ctx, need_budget=False)
    if deadline_reason:
        ab_raw: Dict[str, Any] = {"ok": False, "reason": deadline_reason, "pairs": []}
        sections["ab"] = {"ok": False, "degraded": True, "error": deadline_reason, "tool_calls": 0}
        section_texts["ab"] = ""
    else:
        try:
            ab_raw = _fetch_ab() or {"ok": False, "pairs": []}
        except Exception as exc:  # noqa: BLE001
            ab_raw = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "pairs": []}
        sections["ab"], section_texts["ab"] = _run_section(
            ctx, name="ab", cn="A/B 成绩单", tools={"ww_rerank_perf"}, brief=_ab_brief(ab_raw))

    # ── 职责2:蒸馏草稿(仅当预取里存在 matured 对)──────────────────
    pairs = ab_raw.get("pairs") or []
    distill_note = "(本次无成熟 A/B 对,未起草)"
    if any(_pair_matured(p) for p in pairs):
        r = _call_deep(ctx, _DISTILL_SYSTEM, _distill_user(pairs))
        draft = (r.get("data") or {}).get("draft") if r.get("ok") else None
        if draft:
            duties["distill_draft"] = {"draft": draft, "note": "待人审·未入记忆"}
        else:
            duties["distill_draft"] = None
            distill_note = f"(matured 对存在但起草失败:{r.get('reason') or '未知原因'})"
    else:
        duties["distill_draft"] = None

    # ── 段B:落子复盘 ────────────────────────────────────────────────
    sections["seats"], section_texts["seats"] = _run_section(
        ctx, name="seats", cn="落子复盘", tools={"ww_ledger_state", "ww_picks_perf"},
        brief=_SEATS_BRIEF)

    # ── 段C:数据+调度巡检 ───────────────────────────────────────────
    sections["data"], section_texts["data"] = _run_section(
        ctx, name="data", cn="数据+调度巡检", tools={"ww_data_health", "ww_model_health"},
        brief=_DATA_BRIEF)

    # ── 职责3:macro 快照搭车(零 LLM)────────────────────────────────
    reason = _gate(ctx, need_budget=False)
    if reason:
        duties["macro_snapshot"] = {"ok": False, "reason": reason}
    else:
        try:
            mp = _macro_snapshot() or {}
            duties["macro_snapshot"] = {"ok": bool(mp.get("ok", True)), "reason": None}
        except Exception as exc:  # noqa: BLE001
            duties["macro_snapshot"] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    # ── 段D:综合晨报(deep 一次性)────────────────────────────────────
    rep = _call_deep(ctx, _REPORT_SYSTEM, _report_user(section_texts, duties))
    if rep.get("ok") and (rep.get("data") or {}).get("morning_report_md"):
        morning_md = str(rep["data"]["morning_report_md"])
        todos_raw = rep["data"].get("tomorrow_todos")
        todos = [str(t) for t in todos_raw] if isinstance(todos_raw, list) else []
    else:
        morning_md = f"(综合晨报生成失败:{rep.get('reason') or '未知原因'})"
        todos = []

    # ── 段E:批判(deep 一次性;不过→对应段标降级)───────────────────
    crit = _call_deep(ctx, _CRITIC_SYSTEM, _critic_user(morning_md, section_texts))
    if crit.get("ok"):
        cpass = bool((crit.get("data") or {}).get("pass"))
        issues_raw = (crit.get("data") or {}).get("issues")
        issues = issues_raw if isinstance(issues_raw, list) else []
        critic_out: Dict[str, Any] = {"ok": True, "pass": cpass, "issues": issues}
        if not cpass:
            for iss in issues:
                sec_name = iss.get("section") if isinstance(iss, dict) else None
                if sec_name in sections:
                    problem = iss.get("problem") if isinstance(iss, dict) else None
                    sections[sec_name]["ok"] = False
                    sections[sec_name]["degraded"] = True
                    sections[sec_name]["error"] = f"批判未过:{problem or '未说明'}"
    else:
        critic_out = {"ok": False, "pass": None, "issues": [], "reason": crit.get("reason")}

    # ── 落盘:报告永远落盘,落盘失败才算 job 失败 ─────────────────────
    payload = {
        "date": today, "job_id": ctx.job_id, "sections": sections, "duties": duties,
        "critic": critic_out, "budget_used": ctx.budget.used,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    md_text = _assemble_md(today, sections, duties, critic_out, morning_md, todos,
                           section_texts, distill_note)
    md_name, json_name = f"{today}.md", f"{today}.json"
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (REPORTS_DIR / md_name).write_text(md_text, encoding="utf-8")
        (REPORTS_DIR / json_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — 唯一真失败:报告落盘失败
        return {"ok": False, "error": f"报告落盘失败:{type(exc).__name__}: {exc}"}
    return {"ok": True, "report": md_name}


def read_report(date: str = "") -> Dict[str, Any]:
    """读日报(date 空=REPORTS_DIR 里字典序最大的 .md);md 和同名 .json 都读。"""
    d = (date or "").strip()
    if not d:
        try:
            names = sorted(p.name[:-3] for p in REPORTS_DIR.glob("*.md"))
        except Exception:  # noqa: BLE001
            names = []
        if not names:
            return {"ok": False, "reason": "暂无日报"}
        d = names[-1]
    md_path = REPORTS_DIR / f"{d}.md"
    if not md_path.exists():
        return {"ok": False, "reason": f"日报不存在:{d}"}
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"读取失败:{type(exc).__name__}: {exc}"}
    json_data = None
    json_path = REPORTS_DIR / f"{d}.json"
    if json_path.exists():
        try:
            json_data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            json_data = None
    return {"ok": True, "date": d, "md": md_text, "json": json_data}
