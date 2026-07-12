"""Tier-4 post-mortem agent (lives in tier3 dir to avoid creating tier4/).

Runs AFTER ``report-writer``: reads the whole report payload (all upstream agent
outputs + the writer's final ratings/action), does a self-review for immediate
quality issues, and proposes new rules to consider adding to per-agent memory.

**Does NOT auto-patch memory files** — proposals are written to
``memories/_pending_introspections/<date>_<code>.json`` for human review. This
avoids the foot-gun of an LLM silently corrupting the agent rule-books.

Plays the role described in ``memories/introspector/introspector_rules.md``:
Wrong > Partial > Correct, target risk-officer when safer, confidence bucketed
by case count (2=low / 3-5=med / 6+=high).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent
from financial_analyst.llm.client import LLMClient


class IntrospectionProposal(BaseModel):
    target_agent: str           # "risk-officer" / "bear-advocate" / "_shared" 等
    pattern: str                # observed feature intersection / failure mode
    proposed_rule: str          # 一句话规则, 后续人工 review 后塞进 memory
    confidence: str             # "low" | "med" | "high"
    rationale: str              # 为什么这条 rule 值得加


class IntrospectionOutput(BaseModel):
    quality_flags: List[str] = []           # 本次研报立即可见的问题
    proposals: List[IntrospectionProposal] = []  # 跨案例归纳的规则提议 (待 review)
    summary: str = ""
    written_to: Optional[str] = None        # 提议 JSON 落盘路径 (None=没有 proposals)
    provenance_violations: List[str] = []   # 数字溯源门:确定性检查 + LLM 列举 合并去重


SYSTEM_PROMPT = """You are the Introspector — a post-mortem meta-analyst for the
A-share research swarm. The other agents just finished a single-stock report.
Your job: do a self-review + propose rules for human review. Output strict JSON.

# Self-review rules (from introspector_rules.md memory)
- Wrong > Partial > Correct: anomalies carry more signal than agreement.
- Confidence: 2 cases = low, 3-5 = med, 6+ = high (single-stock报告本身就是 1 case,
  所以大多数 proposals 应该是 low / med, 除非与 memory 中已有 pattern 强契合).
- Look for FEATURE INTERSECTIONS that distinguish hits from misses
  (mv_tier, vol_regime, board_total_score, rating sign, conviction_level, anchors).
- Anti-patterns (DON'T propose):
  · "Need more data" (无用; 没 pattern 就返回空 proposals)
  · "Bear was too bearish" without specific trigger (必须指明 when / why)
  · 矛盾既有 memory 而不引用既有规则
- 倾向把规则挂到 risk-officer (CRO 有 veto, 加 CRO 规则比削弱任何 analyst 安全)

# Quality flags — IMMEDIATE issues in THIS report (放在 quality_flags 数组里):
- bull bullets missing V# anchors (bull.thesis_bullets 没 "[V#]" 前缀)
- bear bullets missing F1-F14 anchors (bear.thesis_bullets 没 "[F#]" 前缀)
- rating_overall ≠ sum(rating_dimensions) (writer 内部不自洽)
- bull 和 bear 强烈不一致但 rating 没体现不确定性
- risk-officer 有 veto 但 action != "avoid"
- target_price 离 bull/bear price 都太远 (写手编了价位)

# Proposals — RULES for memory (放在 proposals 数组里):
- 每条 proposal 必含 target_agent + pattern + proposed_rule + confidence + rationale
- 优先盯 risk-officer (加防线), 其次 bear-advocate (加 failure mode)
- 不要给 bull-advocate 加规则 (会过度乐观抑制)
- 空 proposals 列表 = 诚实 (你这次没找到值得规则化的模式)

# 数字出处校验(Provenance check)— 放入 provenance_violations 数组(独立于上面两项)
- 逐条核对报告正文(ctx.report_markdown,本次研报落盘的 md,可能因长度被截断)里出现的
  关键数字断言(目标价/止损/市值/涨跌幅/仓位等)。
- 若该数字能在【平台证据】(ctx.evidence.sections)或现价锚(ctx.quote)中查到出处
  (即使经过合理换算),不列入。
- 查无出处(证据/现价均未提供该数字来源)才列入 provenance_violations,每条一句话,
  格式:"<数字>(<所在段落/上下文>)——<为何查无出处>"。
- ctx.evidence/ctx.quote 若整体缺失(空 dict,平台证据本就没跑起来)→ 不能反过来把
  报告里每个数字都当"查无出处"扣上违规帽子(不是写手编造,是证据环节没跑通),此时
  provenance_violations 应保持空,除非有其他明确矛盾佐证。
- 空 provenance_violations = 诚实(没发现未溯源数字)。

# Output
严格 IntrospectionOutput JSON. proposals / provenance_violations 均是数组 (可空), 不写 free text."""


def _check_provenance(
    summary_json: Dict[str, Any], quote: Dict[str, Any], ev: Dict[str, Any]
) -> List[str]:
    """确定性(非 LLM)数字溯源检查——纯函数,字段缺失一律跳过绝不猜测字段名。

    ``summary_json`` 是 report-writer 最终结构化输出(rating_overall/action/
    target_price/stop_loss/position_pct,即落盘 ``out/*.json`` 的同款字段——见
    ``report_writer.py`` 的 sanity 回写),外加调用方(``_execute``)从 risk-officer
    补进的 ``veto_flags`` 真值(summary_json 自身不携带 veto 信息,veto 权威在
    risk-officer)。三条检查:

    1. target_price / stop_loss 相对现价(``quote.price``)比值须落在 [0.2, 5],
       否则怀疑写手编了脱离现价的价位。
    2. position_pct > 0 但 veto_flags 非空 —— 与 report_writer.py sanity 语义矛盾
       (veto 生效理应强制 0 仓位),这里是独立复核,抓回归。
    3. summary_json 若带市值类数字字段(``mv_yi``/``mv``/``market_cap_yi`` 之一,
       以实际能找到的字段为准),与 ``quote.mv_yi`` 相对偏差 > 30% → 怀疑写手编了
       脱离真实市值的数字。查无该字段 → 跳过,绝不硬猜字段名。

    ``ev``(evidence-loader 证据包)保留在签名里作为未来扩展位(例如对照 quant/chain
    section 反查目标价合理性)——当前三条检查均不依赖它。
    """
    violations: List[str] = []
    summary_json = summary_json or {}
    quote = quote or {}

    def _f(v):
        try:
            if v is None:
                return None
            fv = float(v)
            if fv != fv or fv in (float("inf"), float("-inf")):
                return None
            return fv
        except (TypeError, ValueError):
            return None

    # 1. target_price / stop_loss 相对现价倍数
    current_price = _f(quote.get("price"))
    if current_price is not None and current_price > 0:
        for field_name, label in (("target_price", "目标价"), ("stop_loss", "止损")):
            v = _f(summary_json.get(field_name))
            if v is None or v <= 0:
                continue
            ratio = v / current_price
            if ratio < 0.2 or ratio > 5:
                violations.append(
                    f"目标价/止损相对现价异常:{label}={v:.2f} 现价={current_price:.2f} "
                    f"比值={ratio:.2f}(超出[0.2, 5])"
                )

    # 2. position_pct 与 veto 一致(沿用 report_writer sanity 语义的独立复核)
    position_pct = _f(summary_json.get("position_pct"))
    veto_flags = summary_json.get("veto_flags")
    if position_pct is not None and position_pct > 0 and veto_flags:
        violations.append(
            f"position_pct={position_pct:.3f} > 0 但 risk-officer veto 生效"
            f"({veto_flags})——应强制 0 仓位"
        )

    # 3. mv 类数字(若 summary_json 提供)与 quote.mv_yi 相对偏差
    quote_mv = _f(quote.get("mv_yi"))
    if quote_mv is not None and quote_mv > 0:
        mv_val = None
        for key in ("mv_yi", "mv", "market_cap_yi"):
            if key in summary_json and summary_json.get(key) is not None:
                mv_val = _f(summary_json.get(key))
                break
        if mv_val is not None:
            dev = abs(mv_val - quote_mv) / quote_mv
            if dev > 0.30:
                violations.append(
                    f"市值数字与现价市值偏差过大:summary={mv_val:.2f}亿 "
                    f"quote.mv_yi={quote_mv:.2f}亿 偏差={dev:.1%}(超30%)"
                )

    return violations


class Introspector(SubAgent[IntrospectionOutput]):
    NAME = "introspector"
    OUTPUT_SCHEMA = IntrospectionOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        client = LLMClient.for_agent(self.NAME)

        writer_out = inputs.get("report-writer") or {}
        risk_out = inputs.get("risk-officer") or {}
        quote = inputs.get("quote-fetcher") or {}
        ev = inputs.get("evidence-loader") or {}

        # md 路径 = report-writer 已落盘的路径(output_md_path),同一份文件——
        # 复用它既能抽 code(供 proposals 落盘命名),也能读正文供 LLM 数字出处校验、
        # 追加"未溯源数字"尾节。
        md_path = (writer_out.get("output_md_path") or "").strip()
        code = ""
        if md_path:
            stem = Path(md_path).stem
            code = stem.split("_")[0] if "_" in stem else stem

        report_markdown = ""
        if md_path:
            try:
                report_markdown = Path(md_path).read_text(encoding="utf-8")
            except Exception:
                report_markdown = ""

        # 1. 把整份报告塞进 ctx(evidence/quote 供数字出处校验;report_markdown 单独
        # 截断,避免一份长报告把 ctx 预算全占掉)
        ctx = {
            "fundamental": inputs.get("fundamental-analyst", {}),
            "technical": inputs.get("technical-analyst", {}),
            "whale": inputs.get("whale-analyst", {}),
            "quant": inputs.get("quant-analyst", {}),
            "bull": inputs.get("bull-advocate", {}),
            "bear": inputs.get("bear-advocate", {}),
            "risk_officer": risk_out,
            "writer": writer_out,
            "evidence": ev,
            "quote": quote,
            "report_markdown": report_markdown[:6000],
        }
        ctx_json = json.dumps(ctx, ensure_ascii=False, default=str)[:16000]

        # 2. 读自己的 memory (introspector_rules.md)
        memory_text = self.memory.load_all()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n# My memory rules\n" + memory_text},
            {"role": "user", "content": f"Full report payload (JSON):\n{ctx_json}\n\n"
                                         f"Return IntrospectionOutput JSON per schema."},
        ]
        response = await client.chat(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = json.loads(response["choices"][0]["message"]["content"])

        # 3. 数字溯源门:确定性检查(纯函数)+ LLM 列举结果 合并去重
        check_payload = dict(writer_out)
        check_payload.setdefault("veto_flags", risk_out.get("veto_flags"))
        det_violations = _check_provenance(check_payload, quote, ev)
        llm_violations = raw.get("provenance_violations") or []
        if not isinstance(llm_violations, list):
            llm_violations = []
        merged_violations = list(det_violations)
        for v in llm_violations:
            if isinstance(v, str) and v not in merged_violations:
                merged_violations.append(v)
        raw["provenance_violations"] = merged_violations

        # 4. violations 非空 → 对已落盘 md 追加尾节(append-only,绝不重写已有内容);
        # 追加失败(文件缺失/权限等)诚实吞掉——检查结果仍在 Output.provenance_violations
        # 里,不让 introspector 的复盘校验反过来拖垮报告主产物(波4/波6本就不阻塞)。
        if merged_violations and md_path:
            try:
                with open(md_path, "a", encoding="utf-8") as f:
                    f.write("\n\n## ⚠ 未溯源数字(introspector 校验)\n")
                    for v in merged_violations:
                        f.write(f"- {v}\n")
            except Exception:
                pass

        # 5. 非空 proposals 落盘待人工 review (NOT 自动 patch memory!)
        if raw.get("proposals"):
            try:
                from financial_analyst.memory_paths import default_memory_root
                pending_dir = default_memory_root() / "_pending_introspections"
                pending_dir.mkdir(parents=True, exist_ok=True)

                today = date.today().isoformat()
                out_file = pending_dir / f"{today}_{code or 'unknown'}.json"
                out_file.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                raw["written_to"] = str(out_file)
            except Exception as e:
                raw.setdefault("quality_flags", []).append(
                    f"introspector: failed to persist proposals: {type(e).__name__}: {e}"
                )

        return raw
