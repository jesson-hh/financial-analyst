# -*- coding: utf-8 -*-
"""后端定时盯盘 watcher(2026-07-11 落子改造 Task 1,spec §2)。

交易日盘中(09:30–11:30 / 13:00–15:00,引擎交易日历判日、失败回退周一~五)每 tick
(默认 5min)遍历盯盘集(``var/archive/strat_*.json`` 里 ``bind`` 非空的策略),
对每只绑定票:报价 fresh → 节流过(per-code 10min 硬地板 + 策略 ``clock.decisionFreq``
hourly≥1h / daily 当日一次)→ **进程内直调 decide 内核**(``api._decide_impl``,
严禁 HTTP 自调本服务,守协程红线),落盘 ``seats_decisions.jsonl`` 带 ``source:"watcher"``。

烧钱保险:日预算(默认 24 次/日)存 ``var/seats_watch.json``
``{"enabled": bool, "daily_budget": int, "counts": {"YYYY-MM-DD": n}}``;超限当日自停;
env 总闸 ``GUANLAN_SEATS_WATCH=1`` 才起 ``run_loop``(server lifespan),server 重启按
状态文件自恢复(enabled 持久化)。

协程红线:``run_loop`` 只 ``await asyncio.to_thread(tick)`` —— tick 内一切 LLM/取数/
读写盘全在工作线程同步执行,绝不堵事件循环;``_decide_production`` 在该工作线程内
``asyncio.run`` 一个独立事件循环跑 ``_decide_impl``(LLMClient 在同一循环内创建与使用,
不跨循环复用 httpx)。

全可注入(``tick(now, decide_fn, quote_fn, decisions_tail_fn)``):测试全桩零网络零 LLM。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger("guanlan.seats.watcher")

VAR = Path(__file__).resolve().parents[2] / "var"
ARCHIVE_DIR = VAR / "archive"
DECISIONS_LOG = VAR / "seats_decisions.jsonl"     # 与 api._DEC_LOG 同一文件(读侧)
STATE_PATH = VAR / "seats_watch.json"   # {"enabled": bool, "daily_budget": int, "counts": {"2026-07-10": 2}}

DEFAULT_BUDGET = 24                     # 日预算(次/日),烧钱保险
_FLOOR_SECONDS = 10 * 60                # per-code 10min 硬地板(与前端旧口径一致)
_SESSIONS = ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0)))
_COUNTS_KEEP_DAYS = 14                  # counts 只留最近 N 个日键(防状态文件无限长)


# ───────────────────────── 状态文件 ─────────────────────────

def load_state() -> dict:
    """读状态;缺文件/坏 json → 默认 ``{"enabled": False, "daily_budget": 24, "counts": {}}``。"""
    try:
        st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(st, dict):
            raise ValueError("state 非 dict")
    except Exception:  # noqa: BLE001 — 缺文件/坏文件一律回默认(诚实冷启动)
        return {"enabled": False, "daily_budget": DEFAULT_BUDGET, "counts": {}}
    st.setdefault("enabled", False)
    st.setdefault("daily_budget", DEFAULT_BUDGET)
    if not isinstance(st.get("counts"), dict):
        st["counts"] = {}
    return st


def save_state(st: dict) -> None:
    """落盘状态(计划 schema 原样)。失败抛出——预算护栏丢失必须显形,不静默。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def set_enabled(on: bool) -> dict:
    """改 enabled 落盘,返回 get_status()(/seats/watch/toggle 用)。"""
    st = load_state()
    st["enabled"] = bool(on)
    save_state(st)
    return get_status()


# ─────────────── 编排层协作缝(Phase 9 Task 4 加法·只增)───────────────
# 观澜编排回放/live 运行与本 watcher 共用同一个 24/日 LLM 预算池。两条缝均为纯加法:
# note_external_llm_use 把编排消耗记进 tick 同一份 counts(gating 见得到);
# orchestrated_codes 报出正被某编排运行接管的票,tick 对其记 skipped='orchestrated'
# 避免双判。非编排票的既有 tick 门序逐位不变。

_ORCHESTRATED_CODES: set[str] = set()


def orchestrated_codes() -> set[str]:
    """当前被某活跃编排运行接管的票集合(tick 对其跳过,记 skipped='orchestrated',
    避免与编排层双判同一只票)。默认空集;编排层在其活跃期登记/撤销 _ORCHESTRATED_CODES。"""
    return set(_ORCHESTRATED_CODES)


def note_external_llm_use(n: int, now: Optional[datetime] = None) -> None:
    """把 n 次外部(编排层)LLM 调用记进今日**同一份** counts —— tick 读的正是这份 —
    使 watcher gating 看得见编排消耗的 24/日池。加法缝:tick 自身计数逻辑不变;n<=0 空操作。
    落盘失败必抛(预算护栏丢失不静默,与 tick 同口径)。"""
    n = int(n)
    if n <= 0:
        return
    now = now or datetime.now()
    today = now.date().isoformat()
    st = load_state()
    counts = st.get("counts") or {}
    counts[today] = int(counts.get(today) or 0) + n
    for k in sorted(counts)[:-_COUNTS_KEEP_DAYS]:      # 只留最近 N 个日键(与 tick 同)
        counts.pop(k, None)
    st["counts"] = counts
    save_state(st)


# ────────── 编排接管登记(R17·注册半边,纯加法)──────────
# 上面 orchestrated_codes() 是这条缝的「读」半边;这里补上「写」半边 —— 在此之前
# 仓库里**没有任何代码**写过 _ORCHESTRATED_CODES,那条 tick 跳过分支是条死缝:编排真
# 跑起来后,同一只票会被 watcher 与编排各判一次(不是预算超支——编排消耗经
# note_external_llm_use 记进同一份 counts——而是同一笔真钱决策上的重复研判)。
#
# 语义(逐条有测试):
#   * **引用计数**。两个编排运行同时接管同一只票 → 计数 2;任一方撤销只减 1,另一方
#     仍然拥有。naive 的 set.discard 会替别人放手,那才是日后真会咬人的失败模式。
#   * **一次性凭证**。register 返回 OrchestratedRegistration,release 只撤销「这张凭证
#     登记过的那些键」且只生效一次(重复 release 是空操作,绝不误减别人的计数)。凭证
#     自身即上下文管理器,``with register_orchestrated_codes(...)`` 在正常/异常/提前
#     return 三条出口都归还——泄漏一次就等于在本进程里永久噤声那只票。
#   * **纯进程内,绝不落盘**。进程崩了登记随之消失、watcher 自愈(fail-open:恢复判,
#     绝不会卡死在不判)。代价必须说清:编排与 watcher 必须**同进程**(9999 server
#     lifespan)互斥才成立;跨进程的编排不在这条缝的射程内。
#   * **只挡「登记之后才开拍」的 tick,不是绝对互斥**。tick 在开头一次性取
#     ``orchestrated = orchestrated_codes()`` 快照,随后整拍时间都花在 quote_fn(网络)
#     与 decide_fn(LLM)里 —— 几秒到几十秒。已经在飞的那一拍手里是**过期空快照**,
#     编排在它飞行途中登记也拦不住它判同一只票。要根治得把读挪进 tick 的循环内,那就
#     动了 tick(破坏「非编排票门序逐位不变」的约束),所以这是**记录在案的边界**,
#     不是 bug。
#   * **线程安全**。写侧持锁改引用计数后**整体重绑**一个新 set;读侧 orchestrated_codes()
#     一字未改也永远安全(它拷贝的那个 set 对象此后不再被原地修改)。
#   * 别名:tick 比的是策略 bind 里的原样字符串,我们不控制它的写法,所以带交易所的
#     登记(``SZ300750`` / ``300750.SZ``)连带拥有裸六位与另一种写法。裸六位只拥有自己
#     ——本模块不猜交易所(号段推断是别处的事,不在这里复制一份会漂移的规则)。

_ORCHESTRATED_LOCK = threading.Lock()
#: 键 → 引用计数(拥有者数)。_ORCHESTRATED_CODES 恒等于本表的键集快照。
_ORCHESTRATED_REFS: dict[str, int] = {}

#: 一只票的三种完整写法(裸六位 / 交易所前缀 / 点后缀),纯语法、锚定匹配。
_ORCH_CODE_RE = re.compile(r"^(?:(?P<ex_pre>SH|SZ|BJ))?(?P<code>\d{6})(?:\.(?P<ex_suf>SH|SZ|BJ))?$")


def _orchestrated_aliases(code: str) -> tuple[str, ...]:
    """一只票的等价写法集合(纯语法,不猜交易所)。非 str/空串是调用方错误,响亮抛。"""
    if not isinstance(code, str):
        raise TypeError(f"orchestrated code 必须是 str,收到 {type(code).__name__}: {code!r}")
    raw = code.strip()
    if not raw:
        raise ValueError("orchestrated code 不能是空串/纯空白")
    out = {raw, raw.upper()}
    m = _ORCH_CODE_RE.fullmatch(raw.upper())
    if m:
        c = m.group("code")
        out.add(c)
        ex = m.group("ex_pre") or m.group("ex_suf")
        if ex:
            out.update({f"{ex}{c}", f"{c}.{ex}"})
    return tuple(sorted(out))


def _publish_orchestrated() -> None:
    """把引用计数表的键集**重绑**为新的 _ORCHESTRATED_CODES(须在持锁时调用)。

    重绑而非原地改:读侧 ``set(_ORCHESTRATED_CODES)`` 拿到的对象自此不再被改写,
    因此读侧无需加锁也不会撞上「拷贝时集合被并发修改」。"""
    global _ORCHESTRATED_CODES
    _ORCHESTRATED_CODES = set(_ORCHESTRATED_REFS)


class OrchestratedRegistration:
    """一次登记的一次性凭证;同时是上下文管理器(退出即归还,含异常路径)。"""

    __slots__ = ("_keys", "_released")

    def __init__(self, keys: tuple[str, ...]):
        self._keys = keys
        self._released = False

    @property
    def keys(self) -> tuple[str, ...]:
        """本次登记实际增计数的键(含别名);release 只撤销这些。"""
        return self._keys

    @property
    def released(self) -> bool:
        return self._released

    def __repr__(self) -> str:  # pragma: no cover — 诊断用
        return (f"OrchestratedRegistration(keys={list(self._keys)!r}, "
                f"released={self._released})")

    def __enter__(self) -> "OrchestratedRegistration":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        release_orchestrated_codes(self)
        return False       # 绝不吞异常


def register_orchestrated_codes(codes) -> OrchestratedRegistration:
    """登记一组被某活跃编排运行接管的票,返回一次性凭证(必须 release / with)。

    ``codes`` 是 str 的可迭代物。单个裸 str / bytes / None 一律 TypeError —— 直接迭代
    一个 str 会把 ``"300750"`` 拆成六个单字符「票」悄悄登记,那是最典型的坑。空可迭代
    物是合法的:得到一张空凭证,登记与归还都是空操作。同一次调用里的重复(含互为别名
    的写法)折叠成一次计数。任一元素非法则整体抛出,**绝不留下半份登记**。"""
    if codes is None or isinstance(codes, (str, bytes, bytearray)):
        raise TypeError(
            f"codes 必须是 str 的可迭代物(不是单个 str/bytes/None),收到 {codes!r}")
    try:
        items = list(codes)
    except TypeError as e:
        raise TypeError(f"codes 必须是 str 的可迭代物,收到 {type(codes).__name__}") from e
    keys: set = set()
    for c in items:                       # 校验全部先做完(非法 → 抛,零副作用)
        keys.update(_orchestrated_aliases(c))
    reg = OrchestratedRegistration(tuple(sorted(keys)))
    with _ORCHESTRATED_LOCK:
        for k in reg.keys:
            _ORCHESTRATED_REFS[k] = _ORCHESTRATED_REFS.get(k, 0) + 1
        _publish_orchestrated()
    return reg


def release_orchestrated_codes(registration: Optional[OrchestratedRegistration]) -> None:
    """归还一张登记凭证。``None`` 是空操作(调用方 finally 里可能什么都没拿到);
    重复归还同一张凭证也是空操作(一次性)——否则会误减别的运行的计数。
    传入非凭证对象是调用方错误,响亮抛 TypeError。"""
    if registration is None:
        return
    if not isinstance(registration, OrchestratedRegistration):
        raise TypeError(
            "release_orchestrated_codes 只接受 register_orchestrated_codes 返回的凭证,"
            f"收到 {type(registration).__name__}")
    with _ORCHESTRATED_LOCK:
        if registration._released:
            return
        registration._released = True
        for k in registration._keys:
            n = _ORCHESTRATED_REFS.get(k, 0) - 1
            if n > 0:
                _ORCHESTRATED_REFS[k] = n
            else:
                _ORCHESTRATED_REFS.pop(k, None)
        _publish_orchestrated()


# ───────────────────────── 盯盘集(策略 bind 派生)─────────────────────────

def watching_codes() -> list[dict]:
    """读 ``var/archive/strat_*.json``,``bind`` 非空的策略逐票展开(坏 json 跳过)。

    返回 ``[{code, strategy_id, name, clock, creed, w, pa, pa_method, refs}]``
    (per (策略, 票) 一行;同票多策略由 tick 内去重,绑定仍是盯盘集唯一真相,
    与 ww_seats_bind 同源语义)。"""
    out: list[dict] = []
    try:
        paths = sorted(ARCHIVE_DIR.glob("strat_*.json"))
    except Exception:  # noqa: BLE001 — 目录不可读 → 诚实空集
        return []
    for p in paths:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 单条坏 json 跳过
            continue
        if not isinstance(s, dict):
            continue
        binds = s.get("bind")
        if not isinstance(binds, list) or not binds:
            continue
        for code in binds:
            code = str(code or "").strip()
            if not code:
                continue
            out.append({
                "code": code,
                "strategy_id": str(s.get("id") or p.stem),
                "name": str(s.get("name") or ""),
                "clock": s.get("clock") if isinstance(s.get("clock"), dict) else {},
                "creed": str(s.get("creed") or ""),
                "w": s.get("w") or 0,
                "pa": bool(s.get("pa")),
                "pa_method": str(s.get("paMethod") or s.get("pa_method") or ""),
                "refs": s.get("refs") if isinstance(s.get("refs"), list) else [],
            })
    return out


def get_status() -> dict:
    """/seats/watch/status 载荷:{enabled, watching, today_count, daily_budget, last_tick, market_open}。"""
    st = load_state()
    now = datetime.now()
    codes: list[str] = []
    for cw in watching_codes():
        if cw["code"] not in codes:
            codes.append(cw["code"])
    return {
        "enabled": bool(st.get("enabled")),
        "watching": codes,
        "today_count": int((st.get("counts") or {}).get(now.date().isoformat()) or 0),
        "daily_budget": int(st.get("daily_budget") or DEFAULT_BUDGET),
        "last_tick": st.get("last_tick"),
        "market_open": _is_market_open(now),
    }


# ───────────────────────── 交易日盘中门 ─────────────────────────

_CAL_CACHE: dict = {"dates": None, "tried": False}


def _calendar_dates():
    """引擎全量交易日历 → set[date](一次性缓存);加载失败 → None(回退周一~五)。"""
    if not _CAL_CACHE["tried"]:
        _CAL_CACHE["tried"] = True
        try:
            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            cal = _lf.get_default_loader()._load_calendar("day")
            dates = {_pd.Timestamp(d).date() for d in cal}
            _CAL_CACHE["dates"] = dates or None
        except Exception:  # noqa: BLE001 — 无日历退 None,_is_market_open 走周一~五兜底
            _CAL_CACHE["dates"] = None
    return _CAL_CACHE["dates"]


def _is_market_open(now: datetime) -> bool:
    """交易日(引擎日历,日历不覆盖该日/加载失败回退周一~五)+ 09:30-11:30 / 13:00-15:00。"""
    d = now.date()
    try:
        cal = _calendar_dates()
    except Exception:  # noqa: BLE001
        cal = None
    if cal and d <= max(cal):
        if d not in cal:
            return False
    elif d.weekday() >= 5:       # 日历缺失/不覆盖 → 周一~五兜底
        return False
    t = now.time()
    return any(a <= t <= b for a, b in _SESSIONS)


def _throttle_ok(code: str, freq: str, last_ts, now: datetime) -> bool:
    """节流:10min 硬地板 + hourly≥1h + daily 当日一次;无记录/坏时间戳 → 放行。"""
    if not last_ts:
        return True
    try:
        last = datetime.fromisoformat(str(last_ts))
    except (TypeError, ValueError):
        return True
    delta = (now - last).total_seconds()
    if delta < _FLOOR_SECONDS:
        return False
    f = str(freq or "").strip().lower()
    if f == "daily":
        return last.date() != now.date()
    return delta >= 3600.0       # hourly / 未知频率保守按 hourly


# ───────────────────────── refs best-effort 服务端解析 ─────────────────────────

def _lookup_card(rid: str) -> Optional[dict]:
    """卡 id → dict(guanlan_v2.cards 后端 store,状态即目录);查不到/异常 → None。"""
    try:
        from guanlan_v2.cards.store import CardStore
        return CardStore().load(str(rid)).to_dict()
    except Exception:  # noqa: BLE001 — KeyError(无此卡)/store 故障一律 None(跳过)
        return None


def _lookup_factor(rid: str) -> Optional[dict]:
    """因子 id → dict(factorlib 注册表 base/+mined/,按 name 命中);查不到 → None。"""
    try:
        from guanlan_v2.factorlib.store import LibraryFactorStore
        for e in LibraryFactorStore().list_factors(validate=False):
            if str(e.get("name")) == str(rid):
                return e
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_refs(refs) -> tuple:
    """策略 refs(卡/因子 id)→ (cards, recipe_factors),按 decide payload 既有字段名
    (与前端 recipeForStrategy 同形):卡 ``{name,insight,verdict,conf,ic}``、因子
    ``{id,name,ic,expr}``。查不到(前端专有 GL 实体等)跳过,绝不编造。"""
    cards: list = []
    factors: list = []
    for rid in (refs or []):
        rid = str(rid or "").strip()
        if not rid:
            continue
        c = _lookup_card(rid)
        if c:
            cards.append({
                "name": c.get("title") or rid,
                "insight": c.get("insight") or c.get("verdict") or "",
                "verdict": c.get("verdict") or None,
                "conf": c.get("conf") if c.get("conf") is not None else None,
                "ic": c.get("ic") or None,
            })
            continue
        f = _lookup_factor(rid)
        if f:
            _ic = f.get("ic")
            factors.append({
                "id": f.get("name") or rid,
                "name": f.get("name") or rid,
                "ic": "" if _ic is None else str(_ic),
                "expr": str(f.get("expr") or ""),
            })
    return cards, factors


# ───────────────────────── 生产注入件(decide / quote / 尾巴 / 行业)─────────────────────────

def _norm_code(code: str) -> str:
    """引擎 normalize_code(``300750`` → ``SZ300750``);引擎不可用退大写原样。"""
    try:
        from financial_analyst.buddy.tools import normalize_code
        return normalize_code(code)
    except Exception:  # noqa: BLE001
        return str(code or "").strip().upper()


_IND_CACHE: dict = {"loader": None, "tried": False}


def _industry_for(code: str) -> str:
    """引擎股票行业元数据真值(IndustryLoader,申万门类,本地 parquet 只读);
    查不到/「未知」→ ''(诚实空,不硬编码假值)。"""
    try:
        if not _IND_CACHE["tried"]:
            _IND_CACHE["tried"] = True
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            if industry_map_path().exists():
                _IND_CACHE["loader"] = IndustryLoader()
        ld = _IND_CACHE["loader"]
        if ld is None:
            return ""
        ind = str(ld.get(_norm_code(code)) or "")
        return "" if ind == getattr(ld, "UNKNOWN_INDUSTRY", "未知") else ind
    except Exception:  # noqa: BLE001
        return ""


def _decide_production(payload: dict) -> dict:
    """生产 decide:进程内直调 api._decide_impl(严禁 HTTP 自调)。本函数在 tick 的
    to_thread 工作线程里跑 → 线程内无事件循环,asyncio.run 起独立循环安全。"""
    from guanlan_v2.seats import api as _api
    return asyncio.run(_api._decide_impl(payload))


def _quote_production(code: str) -> dict:
    """实时报价 fresh 门(腾讯,与 /seats/quote 同源采集器):盘中报价日==今日 → fresh。
    停牌/断流 → 报价日停在旧日 → fresh=False(tick 只在盘中跑,口径与端点等价)。"""
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
    c = _norm_code(code)
    data = TencentQuoteCollector().fetch([c])
    q = (data or {}).get(c) or next(iter((data or {}).values()), None)
    if not q:
        return {"fresh": False}
    asof_raw = str(q.get("asof") or "")
    asof_date = (f"{asof_raw[0:4]}-{asof_raw[4:6]}-{asof_raw[6:8]}"
                 if len(asof_raw) >= 8 and asof_raw[:8].isdigit() else None)
    fresh = bool(asof_date) and asof_date == datetime.now().date().isoformat()
    return {"fresh": fresh, "name": q.get("name"), "price": q.get("price"), "asof": asof_raw}


_CODE6_RE = re.compile(r"(\d{6})")


def _code6(code: str) -> str:
    m = _CODE6_RE.search(str(code or ""))
    return m.group(1) if m else str(code or "")


def _decisions_tail_production(code: str, max_lines: int = 4000) -> Optional[str]:
    """读 ``var/seats_decisions.jsonl`` 尾部,取该 code 最新一条 kind=decide 的 ts
    (6 位数字码等价比较:bind 存 ``300750``、落盘存 ``SZ300750``)。无 → None。"""
    try:
        if not DECISIONS_LOG.exists():
            return None
        lines = DECISIONS_LOG.read_text(encoding="utf-8").splitlines()[-max_lines:]
    except Exception:  # noqa: BLE001 — 读不到当无记录(节流退化为地板内多判一次,可接受)
        return None
    want = _code6(code)
    last: Optional[str] = None
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if r.get("kind") != "decide" or _code6(r.get("code")) != want:
            continue
        ts = str(r.get("ts") or "")
        if ts and (last is None or ts > last):    # ISO 字符串序=时间序
            last = ts
    return last


def _build_payload(cw: dict, now: datetime, quote: dict) -> dict:
    """decide payload(既有字段名,api.seats_decide 同口径消费):cards/recipe_factors
    由 refs 服务端 best-effort 解析;industry 为引擎元数据真值(取不到 '');
    source='watcher' 随 _persist_decision 落盘。"""
    cards, factors = _resolve_refs(cw.get("refs"))
    name = str((quote or {}).get("name") or "") or cw["code"]
    return {
        "code": cw["code"],
        "name": name,
        "date": now.date().isoformat(),
        "seat_cn": cw.get("name") or "盯盘",
        "creed": cw.get("creed") or "",
        "strategy_id": cw.get("strategy_id") or "",
        "strategy_name": cw.get("name") or "",
        "cards": cards,
        "recipe_factors": factors,
        "w": cw.get("w") or 0,
        "pa": bool(cw.get("pa")),
        "pa_method": cw.get("pa_method") or "",
        "industry": _industry_for(cw["code"]),
        "mode": "fast",          # 定时节拍走快档(deepseek-chat);深思留给手动「研判一次」
        "freq": "day",
        "source": "watcher",
    }


# ───────────────────────── tick / run_loop ─────────────────────────

def tick(now: Optional[datetime] = None,
         decide_fn: Optional[Callable[[dict], dict]] = None,
         quote_fn: Optional[Callable[[str], dict]] = None,
         decisions_tail_fn: Optional[Callable[[str], Any]] = None) -> dict:
    """一拍:enabled+盘中+预算余 → 逐 watching code(quote fresh → 节流过 → decide → 计数)。

    返回 ``{"judged": [codes], "skipped": {code: reason}}``。全可注入(测试用桩);
    生产缺省 = _decide_production / _quote_production / _decisions_tail_production。"""
    now = now or datetime.now()
    st = load_state()
    if not st.get("enabled"):
        return {"judged": [], "skipped": {"_": "disabled"}}
    if not _is_market_open(now):
        return {"judged": [], "skipped": {"_": "market_closed"}}
    today = now.date().isoformat()
    counts = st.get("counts") or {}
    used = int(counts.get(today) or 0)
    budget = int(st.get("daily_budget") or DEFAULT_BUDGET)
    if used >= budget:
        return {"judged": [], "skipped": {"_": "budget_exhausted"}}

    decide_fn = decide_fn or _decide_production
    quote_fn = quote_fn or _quote_production
    decisions_tail_fn = decisions_tail_fn or _decisions_tail_production

    judged: list = []
    skipped: dict = {}
    seen: set = set()
    orchestrated = orchestrated_codes()        # 编排接管的票:本拍跳过(加法缝,非编排票门序不变)
    for cw in watching_codes():
        code = cw["code"]
        if code in seen:                       # 同票多策略:一拍只判一次
            skipped.setdefault(code, "duplicate_bind")
            continue
        seen.add(code)
        if code in orchestrated:               # 被某编排运行接管 → 不双判
            skipped[code] = "orchestrated"
            continue
        if used + len(judged) >= budget:
            skipped[code] = "budget_exhausted"
            continue
        try:
            q = quote_fn(code) or {}
        except Exception as e:  # noqa: BLE001 — 单票报价故障不拖垮整拍
            skipped[code] = f"quote_error:{type(e).__name__}"
            continue
        if not q.get("fresh"):
            skipped[code] = "stale_quote"
            continue
        freq = str((cw.get("clock") or {}).get("decisionFreq") or "hourly")
        try:
            last_ts = decisions_tail_fn(code)
        except Exception:  # noqa: BLE001
            last_ts = None
        if not _throttle_ok(code, freq, last_ts, now):
            skipped[code] = "throttled"
            continue
        payload = _build_payload(cw, now, q)
        try:
            res = decide_fn(payload) or {}
        except Exception as e:  # noqa: BLE001 — 单票 decide 故障不拖垮整拍
            _log.warning("watcher decide 异常 code=%s: %s", code, e)
            skipped[code] = f"decide_error:{type(e).__name__}"
            continue
        if isinstance(res, dict) and res.get("ok") is False:
            skipped[code] = str(res.get("reason") or "decide_failed")[:120]
            continue
        judged.append(code)

    # 计数/last_tick 落盘(判 0 票也记 last_tick,状态端点可见「最近一拍」)
    counts[today] = used + len(judged)
    for k in sorted(counts)[:-_COUNTS_KEEP_DAYS]:      # 只留最近 N 个日键
        counts.pop(k, None)
    st["counts"] = counts
    st["last_tick"] = now.isoformat(timespec="seconds")
    try:
        save_state(st)
    except Exception as e:  # noqa: BLE001 — 预算护栏写失败必须留痕(下拍重读仍以盘上为准)
        _log.error("watcher 状态落盘失败(预算计数可能丢失): %s", e)
    return {"judged": judged, "skipped": skipped}


async def run_loop(interval_s: int = 300, *,
                   decide_fn: Optional[Callable[[dict], dict]] = None) -> None:
    """常驻循环(GUANLAN_SEATS_WATCH=1 时由 server lifespan 起):enabled 才干活;
    tick 全量进 to_thread(内含 LLM/取数,严禁堵事件循环);异常只记日志不退出。

    ``decide_fn``(Phase 10 Task 7 加法缝):server 在 ``GUANLAN_SEATS_DEEP=1`` 时
    注入编排深研判包装器,原样透传给 ``tick``;默认 ``None`` ⇒ tick 走生产缺省
    ``_decide_production``,行为逐位不变。"""
    _log.info("seats watcher run_loop 启动(interval=%ss,budget 默认 %s/日)", interval_s, DEFAULT_BUDGET)
    while True:
        try:
            if load_state().get("enabled"):
                out = await asyncio.to_thread(tick, None, decide_fn)
                if out.get("judged"):
                    _log.info("watcher tick judged=%s skipped=%s", out["judged"], out.get("skipped"))
        except asyncio.CancelledError:      # server 停机:干净退出
            raise
        except Exception as e:  # noqa: BLE001 — 单拍故障绝不杀循环
            _log.warning("watcher tick 异常: %s", e)
        await asyncio.sleep(max(30, int(interval_s or 300)))
