"""DecisionAgent — single pre-open trading-decision agent (mockable, cached).

Design (P2 §3):

* A plain class — NOT a ``SubAgent`` subclass. ``SubAgent`` pulls litellm via
  ``dream/__init__`` and binds a filesystem ``AgentMemory``; this subpackage
  stays zero-LLM-import in its default (mock-injected) path.
* ``LLMClient`` is imported at module scope so tests can
  ``patch('financial_analyst.backtest.decision.LLMClient.for_agent')`` and so a
  client can be injected directly.
* The decision cache key is the **sha256 of the actual ``build_messages`` text**
  (plus date/as_of/temperature), so every field that reaches the prompt — news
  body, event summary, candidates, holdings, rev20, the prompt skeleton itself —
  is automatically part of the key. Change the engine (matching/ledger) → prompt
  text unchanged → cache hit (no re-run); change any decision input → key changes
  → re-run. No hand-maintained ``PROMPT_VERSION``.
* ``n_llm_calls`` is self-maintained on the agent (incremented on every real
  ``chat``), independent of ``LLMClient.n_calls`` (which only ticks when the
  response carries a ``usage`` block) and of any mock having that attribute.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from financial_analyst.backtest.pit_reader import VisibleInfo
from financial_analyst.llm.client import LLMClient

_VALID_ACTIONS = {"buy", "add", "hold", "reduce", "sell"}

# prompt截断 budgets (§3.3 / §7)
_NEWS_MAX = 30
_NEWS_BODY_CHARS = 200
_EVENTS_MAX = 40
_EVENT_SUMMARY_CHARS = 120
_POLICY_MAX = 20
_POLICY_SUMMARY_CHARS = 150


# ==========================================================================
# IO dataclasses
# ==========================================================================
@dataclass
class IntradayCtx:
    """P3 intraday re-decision context (None → pre-open path, the P2 default).

    ``kind`` + ``bar_index`` are rendered into ``build_messages`` so they enter
    the cache key *structurally* — the key never depends on ``detail`` wording.
    """

    kind: str          # "stop_break" | "breakout_high" | "volume_surge"
    bar_index: int
    metric: float
    detail: str = ""   # human-readable only (display, not part of the key logic)


@dataclass
class DecisionInput:
    date: str
    as_of: str
    visible: VisibleInfo
    candidates: List[str]
    rev20_rank: Dict[str, float]
    holdings: Dict[str, dict]
    cash: float
    nav: float
    intraday: Optional["IntradayCtx"] = None   # P3: None = pre-open (P2-identical)


@dataclass
class DecisionLeg:
    code: str
    action: str
    target_price: float = 0.0
    stop_loss: float = 0.0
    weight_pct: float = 0.0
    reason: str = ""


@dataclass
class Decision:
    market_view: str
    decisions: List[DecisionLeg]
    warnings: List[str]
    raw: dict


# ==========================================================================
# prompt building (pure → unit-testable)
# ==========================================================================
def _fmt_holdings(holdings: Dict[str, dict]) -> str:
    if not holdings:
        return "(空仓)"
    lines = []
    for code, p in holdings.items():
        lines.append(
            f"{code}: qty={p.get('qty')}, avg_cost={p.get('avg_cost'):.2f}, "
            f"stop_loss={p.get('stop_loss', 0.0):.2f}"
            if isinstance(p.get("avg_cost"), (int, float))
            else f"{code}: {p}")
    return "\n".join(lines)


def _fmt_candidates(candidates: List[str], rev20_rank: Dict[str, float]) -> str:
    if not candidates:
        return "(无候选)"
    lines = []
    for c in candidates:
        pct = rev20_rank.get(c)
        pct_s = f"{pct:.2f}" if isinstance(pct, (int, float)) else "NA"
        lines.append(f"{c} | rev20_pct={pct_s}")
    return "\n".join(lines)


def _fmt_news(news) -> str:
    if not news:
        return "(无)"
    lines = []
    for n in news[:_NEWS_MAX]:
        body = (n.body or "")[:_NEWS_BODY_CHARS]
        lines.append(f"{n.ts} | {n.code or '大盘'} | {n.title} | {body}")
    return "\n".join(lines)


def _fmt_events(events) -> str:
    if not events:
        return "(无)"
    # ann_date desc, take the most recent K
    ev = sorted(events, key=lambda e: e.ann_date, reverse=True)[:_EVENTS_MAX]
    lines = []
    for e in ev:
        summ = (e.summary or "")[:_EVENT_SUMMARY_CHARS]
        lines.append(f"{e.ann_date} | {e.code} | {e.type} | {summ}")
    return "\n".join(lines)


def _fmt_policy(policy) -> str:
    if not policy:
        return "(无)"
    # gov 在前 (全市场影响) 再 macro; 同档按 pub_date desc
    ordered = sorted(policy, key=lambda p: (p.level != "gov", p.pub_date),
                     reverse=False)
    lines = []
    for p in ordered[:_POLICY_MAX]:
        scope = "全市场" if (p.level == "gov" or not p.code) else p.code
        summ = (p.summary or "")[:_POLICY_SUMMARY_CHARS]
        lines.append(f"{p.pub_date} | {p.level} | {scope} | {p.title} | {summ}")
    return "\n".join(lines)


_SYSTEM = (
    "你是 A 股盘前交易决策官。只依据下方“当日可见信息”(截至 {date} {as_of}),"
    "像真人交易员一样给出今日买卖决策。严守: T+1(今买明卖)、涨跌停不成交、"
    "反转逻辑(rev_20 越低越值得关注)。只在【候选池】内决策。"
    "政策研判口径: level=gov 或 scope=全市场 → 调整 market_view 与板块倾斜; "
    "带个股 code 的 macro 快讯 → 影响对应个股 leg。"
    "必须输出严格 JSON, 无 markdown, 无解释文字。"
)


def build_messages(inp: DecisionInput) -> List[Dict[str, str]]:
    """Render the (system, user) messages for ``inp`` (pure function).

    When ``inp.intraday is None`` (the P2 pre-open default) the output is
    byte-for-byte identical to the P2 prompt — so pre-open cache keys never
    change. When non-None, a structured intraday ctx line (kind + bar_index +
    metric) is prepended to ``user`` and one sentence is appended to ``system``.
    """
    meb = inp.visible.market_eod_prev or {}
    prev = meb.get("prev_trade_date")
    ctx_line = ""
    if inp.intraday is not None:
        ic = inp.intraday
        # NOTE: only the STRUCTURED fields (kind / bar_index / metric) are
        # rendered — never ``ic.detail``. The cache key is the sha256 of this
        # text, so anchoring on structured fields keeps the key stable across
        # detail wording changes (§3.4 / test 12b): change kind → key changes;
        # reword detail → key unchanged.
        ctx_line = (
            f"## 盘中关键点重判 (as_of={inp.as_of})\n"
            f"trigger_kind={ic.kind}; bar_index={ic.bar_index}; "
            f"metric={ic.metric:.4f}\n"
            "这是盘中实时重判, 只针对上述触发股, 决定是否加仓/减仓/止损离场。\n"
        )
    user = ctx_line + (
        "## 大盘(≤前一交易日收盘)\n"
        f"prev_trade_date={prev};宽度指标暂无(本版未接全市场 universe)\n"
        "## 当前账户\n"
        f"现金 {inp.cash:.0f} / 总资产 {inp.nav:.0f} / 持仓:\n"
        f"{_fmt_holdings(inp.holdings)}\n"
        "## 候选池(含 ≤T-1 反转分位, 分位低=近期跌多)\n"
        f"{_fmt_candidates(inp.candidates, inp.rev20_rank)}\n"
        f"## 当日可见新闻(≤{inp.as_of}, 共 {len(inp.visible.news)} 条)\n"
        f"{_fmt_news(inp.visible.news)}\n"
        f"## 当日可见事件(ann_date ≤ {inp.date}, 取前 {_EVENTS_MAX} 条)\n"
        f"{_fmt_events(inp.visible.events)}\n"
        f"## 当日可见政策/宏观(≤{inp.as_of}, 共 {len(inp.visible.policy)} 条)\n"
        f"{_fmt_policy(inp.visible.policy)}\n\n"
        '输出 JSON(严格匹配): {"market_view":"...", "decisions":[ '
        '{"code","action":"buy|add|hold|reduce|sell","target_price":num,'
        '"stop_loss":num,"weight_pct":num,"reason":"..."} ], "warnings":["..."] }'
    )
    sys = _SYSTEM.format(date=inp.date, as_of=inp.as_of)
    if inp.intraday is not None:
        sys = sys + " 本次为盘中重判, 已持仓的票优先考虑止损/止盈纪律。"
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


# ==========================================================================
# JSON parsing + conservative fallback (戊)
# ==========================================================================
def _coerce_num(x) -> float:
    if isinstance(x, bool):
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    return 0.0


def parse_decision(content: str, candidates: List[str]) -> Decision:
    """Parse LLM content into a ``Decision``; never raises (戊)."""
    cand_set = set(candidates)
    warnings: List[str] = []

    data = _try_load_json(content)
    if data is None:
        return Decision(
            market_view="(LLM 输出非法, 空仓不动)", decisions=[],
            warnings=["decision_parse_failed"],
            raw={"_error": "json", "_raw": str(content)[:500]})

    legs: List[DecisionLeg] = []
    raw_decs = data.get("decisions")
    if isinstance(raw_decs, list):
        for d in raw_decs:
            if not isinstance(d, dict):
                continue
            code = d.get("code")
            action = d.get("action")
            if not code or not action:
                warnings.append("leg_missing_code_or_action")
                continue
            if code not in cand_set:
                warnings.append(f"out_of_candidate:{code}")
                continue
            if action not in _VALID_ACTIONS:
                warnings.append(f"unknown_action:{action}->hold")
                action = "hold"
            legs.append(DecisionLeg(
                code=str(code), action=str(action),
                target_price=_coerce_num(d.get("target_price")),
                stop_loss=_coerce_num(d.get("stop_loss")),
                weight_pct=_coerce_num(d.get("weight_pct")),
                reason=str(d.get("reason", "")),
            ))

    out_warnings = list(data.get("warnings") or [])
    out_warnings.extend(warnings)
    return Decision(
        market_view=str(data.get("market_view", "")),
        decisions=legs, warnings=out_warnings, raw=data,
    )


def _try_load_json(content: str) -> Optional[dict]:
    if not isinstance(content, str):
        return None
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    # strip a ```json ... ``` fence and retry
    s = content.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: s.rfind("```")]
        s = s.strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


# ==========================================================================
# decision cache — sha256 over the actual messages text
# ==========================================================================
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


class DecisionCache:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def key(self, inp: DecisionInput, messages: list,
            temperature: Optional[float] = None) -> str:
        payload = _canonical_json({
            "date": inp.date, "as_of": inp.as_of,
            "messages": messages,
            "temperature": round(temperature, 3) if temperature is not None else None,
        })
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, raw: dict) -> None:
        path = self._dir / f"{key}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, default=str)


# ==========================================================================
# DecisionAgent
# ==========================================================================
class DecisionAgent:
    NAME = "backtest-agent"

    def __init__(self, client=None, cache: Optional[DecisionCache] = None,
                 temperature: float = 0.2) -> None:
        self._client = client
        self._cache = cache
        self._temperature = temperature
        self._llm_call_count = 0

    def _ensure_client(self):
        if self._client is None:
            self._client = LLMClient.for_agent(self.NAME)
        return self._client

    @property
    def n_calls(self) -> int:
        return self._llm_call_count

    async def decide(self, inp: DecisionInput) -> Decision:
        messages = build_messages(inp)
        key = None
        if self._cache is not None:
            key = self._cache.key(inp, messages, self._temperature)
            cached = self._cache.get(key)
            if cached is not None:
                return parse_decision(json.dumps(cached, default=str),
                                      inp.candidates)

        client = self._ensure_client()
        resp = await client.chat(
            messages, response_format={"type": "json_object"},
            temperature=self._temperature)
        self._llm_call_count += 1

        content = _extract_content(resp)
        decision = parse_decision(content, inp.candidates)

        if self._cache is not None and key is not None:
            self._cache.put(key, decision.raw)
        return decision


def _extract_content(resp: Any) -> str:
    """Pull ``choices[0].message.content`` from the model_dump dict shape."""
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        # tolerate object-style responses too
        try:
            return resp.choices[0].message.content  # type: ignore[union-attr]
        except Exception:
            return ""
