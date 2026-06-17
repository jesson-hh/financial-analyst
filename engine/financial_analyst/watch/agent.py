"""WatchAgent — single-stock intraday advisor (mockable, cached).

Mirrors ``backtest.decision.DecisionAgent`` (P2 §3) but for **one** stock:

* A plain class — NOT a ``SubAgent`` subclass (no litellm import on the default
  mock-injected path); ``LLMClient`` is imported at module scope so a client can
  be injected directly, and the default falls back to ``LLMClient.for_agent``.
* Input is a single ``WatchContext`` (trigger + realtime + 5min bars + EOD
  factors + today's news + the user's watch item) — **not** a portfolio
  (no candidates / holdings / cash / nav). Output is a ``WatchRec``.
* The cache key is the sha256 of the actual ``_build_messages`` text (plus
  code / now-truncated-to-minute / trigger kind / temperature) — same discipline
  as ``DecisionCache``: any prompt-reaching field automatically enters the key.
* ``trigger_kind`` and ``ts`` on the output come from the **context**, never the
  LLM payload, so they stay structurally pinned.
* Robust to bad output: malformed JSON, unknown action, or **any** exception
  (LLM raise included) → ``WatchRec(action="hold", error=...)``. Never raises.
* ``n_calls`` is self-maintained (incremented on every real ``chat``),
  independent of ``LLMClient.n_calls`` and of any mock's attributes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from financial_analyst.llm.client import LLMClient
from financial_analyst.watch.models import WatchContext, WatchRec

_VALID_ACTIONS = {"buy", "add", "hold", "reduce", "sell"}

# prompt 截断 budgets
_BARS_MAX = 24          # 近 N 根 5min (约 2 小时)
_NEWS_MAX = 10
_NEWS_CHARS = 140


# ==========================================================================
# prompt building (pure → unit-testable)
# ==========================================================================
def _fmt_bars(bars: List[Dict[str, Any]]) -> str:
    if not bars:
        return "(无)"
    lines = []
    for b in bars[-_BARS_MAX:]:
        lines.append(
            f"{b.get('datetime')} | O {_n(b.get('open'))} H {_n(b.get('high'))} "
            f"L {_n(b.get('low'))} C {_n(b.get('close'))} V {_n(b.get('vol'))}"
        )
    return "\n".join(lines)


def _fmt_factors(factors: Dict[str, Any]) -> str:
    if not factors:
        return "(无)"
    return "; ".join(f"{k}={v}" for k, v in factors.items())


def _fmt_news(news: List[str]) -> str:
    if not news:
        return "(无)"
    return "\n".join(f"- {str(h)[:_NEWS_CHARS]}" for h in news[:_NEWS_MAX])


def _fmt_realtime(rt: Dict[str, Any]) -> str:
    if not rt:
        return "(无)"
    return "; ".join(f"{k}={v}" for k, v in rt.items())


def _fmt_item(ctx: WatchContext) -> str:
    it = ctx.item
    if it is None:
        return "(用户未设关注成本/止损)"
    parts = []
    if it.avg_cost is not None:
        parts.append(f"持仓成本={it.avg_cost}")
    if it.stop_loss is not None:
        parts.append(f"用户止损={it.stop_loss}")
    return "; ".join(parts) if parts else "(用户未设关注成本/止损)"


def _n(x: Any) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    return f"{float(x):.2f}"


_SYSTEM = (
    "你是 A 股盘中实时盯盘助手。下面是**一只**被事件触发的票的实时上下文。"
    "请像盯盘交易员一样, 只针对这一只票, 给出明确的操作建议。"
    "严守: T+1(今买明卖)、涨跌停不成交、反转逻辑。"
    "你的**理由必须关联本次触发原因**(trigger_kind / detail)。"
    "必须输出严格 JSON, 无 markdown, 无解释文字, 结构如下: "
    '{"action":"buy|add|hold|reduce|sell", "target_price":num, '
    '"stop_loss":num, "reason":"...", "confidence":0~1}。'
)


def build_messages(ctx: WatchContext, knowledge: str = "",
                   hitrate_context: str = "", eod_prior_context: str = "") -> List[Dict[str, str]]:
    """Render the (system, user) messages for a single-stock ``ctx`` (pure).

    ``knowledge`` (optional) is the curated 盘中决策守则 (see :mod:`watch.knowledge`):
    when non-empty it is prepended to the system message so the advisor follows the
    project's validated rules (反转 / 量能 regime / 游资票失效 / 风控). Empty → the
    generic system prompt (byte-identical to the pre-knowledge behaviour).

    ``hitrate_context`` (optional, ① 复盘回灌) is the advisor's OWN historical
    track-record block (see :func:`watch.outcome.format_hitrate_context`): appended
    to system after ``knowledge`` so the advisor can calibrate confidence on its past
    results. Empty → no change (byte-identical). Both empty → exactly ``_SYSTEM``.
    """
    trig = ctx.trigger or {}
    kind = trig.get("kind", "")
    detail = trig.get("detail", "")
    metric = trig.get("metric", "")
    user = (
        f"## 标的\n{ctx.code} {ctx.name} | 现在 {ctx.now_ts}\n"
        f"## 本次触发\nkind={kind}; metric={metric}; detail={detail}\n"
        f"## 实时快照\n{_fmt_realtime(ctx.realtime)}\n"
        f"## 近 5min K 线(末尾 {_BARS_MAX} 根)\n{_fmt_bars(ctx.bars_5min)}\n"
        f"## 昨收因子/技术\n{_fmt_factors(ctx.factors_eod)}\n"
        f"## 当日新闻\n{_fmt_news(ctx.news_today)}\n"
        f"## 关注成本/止损\n{_fmt_item(ctx)}\n\n"
        '输出 JSON: {"action":"buy|add|hold|reduce|sell","target_price":num,'
        '"stop_loss":num,"reason":"(必须关联触发原因)","confidence":0~1}'
    )
    system = _SYSTEM
    if knowledge:
        system = (
            _SYSTEM
            + "\n\n## 必守策略知识 (项目验证; 与触发原因冲突时, 以风控/失效红线优先)\n"
            + knowledge.strip()
        )
    if hitrate_context:
        system = system + "\n\n" + hitrate_context.strip()
    if eod_prior_context:
        system = system + "\n\n" + eod_prior_context.strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ==========================================================================
# JSON parsing — tolerant (mirrors decision._try_load_json)
# ==========================================================================
def _coerce_num(x: Any) -> float:
    if isinstance(x, bool):
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return 0.0
    return 0.0


def _coerce_confidence(x: Any) -> float:
    c = _coerce_num(x)
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _try_load_json(content: Any) -> Optional[dict]:
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


def _extract_content(resp: Any) -> str:
    """Pull ``choices[0].message.content`` from the model_dump dict shape."""
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        try:
            return resp.choices[0].message.content  # type: ignore[union-attr]
        except Exception:
            return ""


# ==========================================================================
# WatchCache — sha256 over the actual messages text (mirrors DecisionCache)
# ==========================================================================
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


class WatchCache:
    """File-backed cache for single-stock recommendations.

    Stored value is the raw parsed LLM dict; the key is the sha256 of
    (code, now-to-minute, trigger kind, messages, temperature).
    """

    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def key(self, ctx: WatchContext, messages: list,
            temperature: Optional[float] = None) -> str:
        now_min = (ctx.now_ts or "")[:16]   # 截到分钟, 秒级抖动不破缓存
        payload = _canonical_json({
            "code": ctx.code,
            "now_min": now_min,
            "trigger_kind": (ctx.trigger or {}).get("kind", ""),
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
# WatchAgent
# ==========================================================================
class WatchAgent:
    NAME = "watch-agent"

    def __init__(self, client=None, cache: Optional[WatchCache] = None,
                 temperature: float = 0.2,
                 knowledge: Optional[str] = None,
                 hitrate: Optional[dict] = None,
                 signal_pack: Optional[Any] = None) -> None:
        self._client = client
        self._cache = cache
        self._temperature = temperature
        self._llm_call_count = 0
        # knowledge: None -> lazy-load the curated 盘中守则 from fa memory on first
        # use; "" -> explicitly disabled (tests / generic prompt). Cached on instance.
        self._knowledge = knowledge
        # hitrate (① 复盘回灌): None -> lazy-load the advisor's own track-record from
        # the outcome log on first use; a dict (incl {}) -> use as-is ({} = disabled).
        # Cached on instance (stable intraday; backfill is 盘后, a daily restart refreshes).
        self._hitrate = hitrate
        # signal_pack (P3 EOD 先验): None -> 懒加载 load_signal_pack() 最新日 pack;
        # 空 DataFrame -> 禁用 (测试/无 pack). 实例缓存 (盘中稳定, 次日重启刷新).
        self._signal_pack = signal_pack

    def _ensure_client(self):
        if self._client is None:
            self._client = LLMClient.for_agent(self.NAME)
        return self._client

    @property
    def n_calls(self) -> int:
        return self._llm_call_count

    def _get_knowledge(self) -> str:
        """Curated 盘中守则 text; lazy-loaded once when ``None`` (``""`` = disabled)."""
        if self._knowledge is None:
            try:
                from financial_analyst.watch.knowledge import load_watch_knowledge
                self._knowledge = load_watch_knowledge()
            except Exception:  # noqa: BLE001 — knowledge is best-effort, never fatal
                self._knowledge = ""
        return self._knowledge or ""

    def _get_hitrate(self) -> dict:
        """Advisor's own hit-rate dict; lazy-loaded once when ``None`` ({} = disabled)."""
        if self._hitrate is None:
            try:
                from financial_analyst.watch.outcome import compute_hitrate, load_outcomes
                self._hitrate = compute_hitrate(load_outcomes())
            except Exception:  # noqa: BLE001 — track record is best-effort, never fatal
                self._hitrate = {}
        return self._hitrate or {}

    def _get_signal_pack(self):
        """EOD 信号包 DataFrame; None 时懒加载一次 (空 df = 禁用)."""
        if self._signal_pack is None:
            try:
                from financial_analyst.watch.signal_pack import load_signal_pack
                self._signal_pack = load_signal_pack()
            except Exception:  # noqa: BLE001 — 先验 best-effort, 不致命
                import pandas as pd
                self._signal_pack = pd.DataFrame()
        return self._signal_pack

    def _build_messages(self, ctx: WatchContext) -> List[Dict[str, str]]:
        from financial_analyst.watch.outcome import format_hitrate_context
        from financial_analyst.watch.signal_pack import format_eod_prior_context, pack_prior_for
        kind = (ctx.trigger or {}).get("kind", "")
        hitrate_ctx = format_hitrate_context(self._get_hitrate(), kind)
        pack = self._get_signal_pack()
        prior = pack_prior_for(ctx.code, pack) if pack is not None and not pack.empty else {}
        eod_prior_ctx = format_eod_prior_context(prior)
        return build_messages(ctx, knowledge=self._get_knowledge(),
                              hitrate_context=hitrate_ctx, eod_prior_context=eod_prior_ctx)

    def _rec_from_payload(self, data: dict, ctx: WatchContext) -> WatchRec:
        """Build a WatchRec from a parsed LLM dict. ``trigger_kind``/``ts``
        come from ``ctx`` (structurally pinned); unknown action → hold."""
        action = data.get("action")
        if action not in _VALID_ACTIONS:
            action = "hold"
        return WatchRec(
            code=ctx.code,
            action=str(action),
            reason=str(data.get("reason", "")),
            trigger_kind=str((ctx.trigger or {}).get("kind", "")),
            ts=ctx.now_ts,
            target_price=_coerce_num(data.get("target_price")),
            stop_loss=_coerce_num(data.get("stop_loss")),
            confidence=_coerce_confidence(data.get("confidence")),
        )

    def _hold(self, ctx: WatchContext, error: str) -> WatchRec:
        return WatchRec(
            code=ctx.code,
            action="hold",
            reason="",
            trigger_kind=str((ctx.trigger or {}).get("kind", "")),
            ts=ctx.now_ts,
            error=error,
        )

    async def decide_one(self, ctx: WatchContext) -> WatchRec:
        """Advise on a single triggered stock. Never raises — any failure
        (LLM error / bad JSON) collapses to ``WatchRec(action='hold', error=...)``."""
        try:
            messages = self._build_messages(ctx)
            key = None
            if self._cache is not None:
                key = self._cache.key(ctx, messages, self._temperature)
                cached = self._cache.get(key)
                if cached is not None:
                    return self._rec_from_payload(cached, ctx)

            client = self._ensure_client()
            resp = await client.chat(
                messages, response_format={"type": "json_object"},
                temperature=self._temperature)
            self._llm_call_count += 1

            content = _extract_content(resp)
            data = _try_load_json(content)
            if data is None:
                return self._hold(ctx, f"json_parse_failed: {str(content)[:200]}")

            if self._cache is not None and key is not None:
                self._cache.put(key, data)
            return self._rec_from_payload(data, ctx)
        except Exception as e:  # noqa: BLE001 — advisor must never crash the loop
            return self._hold(ctx, str(e))
