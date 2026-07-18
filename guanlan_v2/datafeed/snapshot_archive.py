# -*- coding: utf-8 -*-
"""EOD 盘口/资金快照归档器(datafeed 中台 · 小 phase 甲)。

现状:market_tape 与 fundflow 两个 SWR 中台都只落单份 var/live/*.json、每次原子覆写、
零历史。本模块给它们补**每日收盘后 append-only 归档**,并交付冻结签名读取器
read_archive 作为三方契约(甲落盘 → Phase 5 market.factor 电池 → Phase 9 replay manifest)。

红线:
- **绝不触网**:只读现有缓存文件,绝不调 market_tape._refresh/_trigger_refresh、
  fundflow.pulse._refresh_live/build_live;SWR 热路径(read_tape/read_live)零改。
- **诚实**:缺源/ok:false/warming 当日该 kind 诚实跳过并记 note,绝不落半假行;
  不补零、不回填、不伪造新鲜;first_date 显形;降级必标注。
- **append-only + 幂等**:同 (trade_date, kind) 已有则跳过,重跑不改已有行。
- **月轮转 + 尾读红线**:写入恒当月主文件 snapshots.jsonl,发现非本月行搬
  snapshots-YYYYMM.jsonl;最近数据永远在主文件尾,读近期不扫归档文件。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_DIR_DEFAULT = _REPO_ROOT / "var" / "live"
_ARCHIVE_DIR_DEFAULT = _REPO_ROOT / "var" / "archive"

# 归档三 kind 与其对应的现有 SWR 缓存文件(只读)。
KINDS: Tuple[str, ...] = ("market_tape", "fundflow_concept", "fundflow_industry")
_FUNDFLOW_CACHE_FILE = {
    "fundflow_concept": "fundflow_live_concept.json",
    "fundflow_industry": "fundflow_live_industry.json",
}
_MAIN_FILE = "snapshots.jsonl"


# ── 路径解析(与 market_tape / fundflow.pulse 写侧口径一致)──────────────────────
def _live_dir(cache_dir=None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(os.environ.get("GUANLAN_FUNDFLOW_LIVE_DIR") or _LIVE_DIR_DEFAULT)


def _market_tape_cache_path(cache_dir=None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir) / "market_tape.json"
    return Path(os.environ.get(
        "GUANLAN_MARKET_TAPE_PATH", str(_LIVE_DIR_DEFAULT / "market_tape.json")))


def _archive_dir(archive_dir=None) -> Path:
    if archive_dir is not None:
        return Path(archive_dir)
    return Path(os.environ.get("GUANLAN_SNAPSHOT_ARCHIVE_DIR") or _ARCHIVE_DIR_DEFAULT)


# ── 小工具 ──────────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _to_yyyymmdd(s: Any) -> Optional[str]:
    """'2026-07-17'/'2026-07-17T15:03:00'/'20260717' → '20260717';无法解析→None。"""
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _month_of(trade_date: Any) -> Optional[str]:
    d = _to_yyyymmdd(trade_date)
    return d[:6] if d else None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """逐行读归档 jsonl,脏行跳过(照 macro _read_snapshots 惯例:非 dict / 缺 trade_date 跳过)。"""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — 脏行跳过
            continue
        if isinstance(row, dict) and row.get("trade_date"):
            out.append(row)
    return out


def _rotated_path(kind_dir: Path, month: str) -> Path:
    return kind_dir / f"snapshots-{month}.jsonl"


def _dump_line(row: Dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False)


# ── payload 提取:诚实三态跳过 + trade_date 定日 ────────────────────────────────
def _extract_market_tape(cache_dir) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """返回 (payload, trade_date, skip_reason)。诚实三态:
    缓存缺失/不可解析 → skip;warming 预热占位 → skip;ok:false → skip;
    无任何盘口源(sources 空)→ skip(非交易日无数据,绝不落半假行)。"""
    payload = _load_json(_market_tape_cache_path(cache_dir))
    if payload is None:
        return None, None, "market_tape 缓存缺失或不可解析"
    if payload.get("warming"):
        return None, None, "market_tape 预热中(warming),无定格快照"
    if payload.get("ok") is False:
        return None, None, "market_tape 缓存标记 ok:false"
    if not (payload.get("sources") or {}):
        return None, None, "market_tape 无盘口源(sources 空)"
    # trade_date:优先 _backfill_board_pools 已定日 board_date(YYYYMMDD);正常交易日
    # board_date=None(未回溯)→ 取 pulled_at 之日。
    trade_date = _to_yyyymmdd(payload.get("board_date")) or _to_yyyymmdd(payload.get("pulled_at"))
    if not trade_date:
        return None, None, "market_tape 无法定 trade_date(board_date/pulled_at 皆缺)"
    return payload, trade_date, None


def _extract_fundflow(kind: str, cache_dir) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """返回 (payload, trade_date, skip_reason)。诚实三态同 market_tape:
    缓存缺失/warming/ok:false 三态 → 该 kind 诚实跳过,归档文件零新行。"""
    fname = _FUNDFLOW_CACHE_FILE[kind]
    payload = _load_json(_live_dir(cache_dir) / fname)
    if payload is None:
        return None, None, f"{kind} 缓存缺失或不可解析"
    if payload.get("warming"):
        return None, None, f"{kind} 预热中(warming)"
    if payload.get("ok") is False:
        reason = "; ".join(payload.get("notes") or []) or "空"
        return None, None, f"{kind} 缓存 ok:false({reason})"
    # trade_date:payload 自带日期(market.date)优先 → pulled_at 之日兜底。
    trade_date = _to_yyyymmdd((payload.get("market") or {}).get("date")) \
        or _to_yyyymmdd(payload.get("pulled_at"))
    if not trade_date:
        return None, None, f"{kind} 无法定 trade_date"
    return payload, trade_date, None


# ── 月轮转 append(尾读红线:主文件恒当月;非本月行搬 snapshots-YYYYMM.jsonl)─────
def _has_trade_date(kind_dir: Path, trade_date: str) -> bool:
    """当月主文件是否已有该 trade_date(幂等判重)。近期数据必在主文件,不扫归档文件。"""
    return any(r.get("trade_date") == trade_date for r in _read_jsonl(kind_dir / _MAIN_FILE))


def _rotate_and_append(kind_dir: Path, new_row: Dict[str, Any], cur_month: str) -> None:
    """把主文件里非本月(cur_month)的行搬入对应 snapshots-YYYYMM.jsonl,主文件重写为
    本月行 + 追加 new_row。原子重写主文件;月度归档文件 append-only。"""
    kind_dir.mkdir(parents=True, exist_ok=True)
    main = kind_dir / _MAIN_FILE
    existing = _read_jsonl(main)
    keep: List[Dict[str, Any]] = []
    moved: Dict[str, List[Dict[str, Any]]] = {}
    for r in existing:
        m = _month_of(r.get("trade_date"))
        if m and m != cur_month:
            moved.setdefault(m, []).append(r)
        else:
            keep.append(r)                 # 本月行 / 无法定月的行(不冒然搬移)保留主文件
    keep.append(new_row)
    # 先落月度归档(append-only),再原子重写主文件——搬移中断不丢数据(月度先落地)。
    for m, rows in moved.items():
        with open(_rotated_path(kind_dir, m), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(_dump_line(r) + "\n")
    tmp = main.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(_dump_line(r) + "\n" for r in keep), encoding="utf-8")
    os.replace(tmp, main)


# ── 主入口 ──────────────────────────────────────────────────────────────────────
def archive_eod(now: Optional[datetime] = None, cache_dir=None, archive_dir=None) -> Dict[str, Any]:
    """读三 kind 现有 SWR 缓存,各 append 一行到当月主文件(月轮转)。绝不触网。

    幂等:同 (trade_date, kind) 已有则跳过(文件字节不变)。诚实:缺源/ok:false/warming
    三态跳过并记 skipped,绝不落半假行。返回 {"archived": [kind…], "skipped": [{kind, reason}]}。
    """
    now = now or datetime.now()
    archived_at = now.isoformat(timespec="seconds")
    cur_month = now.strftime("%Y%m")
    root = _archive_dir(archive_dir)

    extracted = [("market_tape", *_extract_market_tape(cache_dir))]
    for k in ("fundflow_concept", "fundflow_industry"):
        extracted.append((k, *_extract_fundflow(k, cache_dir)))

    report: Dict[str, Any] = {"archived": [], "skipped": []}
    for kind, payload, trade_date, reason in extracted:
        if reason is not None:
            report["skipped"].append({"kind": kind, "reason": reason})
            continue
        kind_dir = root / kind
        if _has_trade_date(kind_dir, trade_date):          # 幂等:已归档 → 跳过,零改字节
            report["skipped"].append({"kind": kind, "reason": f"已归档 {trade_date}(幂等跳过)"})
            continue
        row = {"trade_date": trade_date, "archived_at": archived_at,
               "kind": kind, "payload": payload}
        _rotate_and_append(kind_dir, row, cur_month)
        report["archived"].append(kind)
    return report


def _parse_iso(s: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _month_from_rotated_name(path: Path) -> Optional[str]:
    """'snapshots-202606.jsonl' → '202606';不匹配→None。"""
    stem = path.name
    if stem.startswith("snapshots-") and stem.endswith(".jsonl"):
        m = stem[len("snapshots-"):-len(".jsonl")]
        return m if (len(m) == 6 and m.isdigit()) else None
    return None


def read_archive(kind: str, start: Optional[str] = None, end: Optional[str] = None,
                 asof: Optional[str] = None, archive_dir=None) -> Dict[str, Any]:
    """**冻结签名(§9-4 三方契约):read_archive(kind, start=None, end=None, asof=None)**。
    返回 {kind, rows, first_date}:rows 按 trade_date 升序的归档行
    {trade_date, archived_at, kind, payload};first_date=归档真实起点(本次读取所触及范围内的
    最早 trade_date,asof-PIT 一致)显形,空则 None。

    - start/end:trade_date 闭区间过滤(YYYYMMDD 或含分隔符,内部归一)。
    - asof:PIT 过滤——只见 archived_at<=asof 的行;晚于 asof 落盘的行不可见,first_date 同步收缩。
    - **尾读红线**:区间落在当月(start 月 >= 主文件当月)→ 只读主文件,绝不打开月度归档文件;
      跨月/start=None → 按月枚举 snapshots-YYYYMM.jsonl 与主文件合并。
    - 脏行跳过(照 macro _read_snapshots 惯例);空归档诚实 rows=[]/first_date=None,不 raise。

    契约稳定性:签名/返回键改动须甲/Phase 5/Phase 9 三 plan 同批 reconcile,不得单方漂移。
    (archive_dir 为测试/运维注入参数,不属冻结契约的 4 参;下游默认读 var/archive。)
    """
    kind_dir = _archive_dir(archive_dir) / kind
    s_date = _to_yyyymmdd(start) if start is not None else None
    e_date = _to_yyyymmdd(end) if end is not None else None
    s_month = s_date[:6] if s_date else None

    main_rows = _read_jsonl(kind_dir / _MAIN_FILE)
    main_month = main_rows[0]["trade_date"][:6] if main_rows else None

    scanned: List[Dict[str, Any]] = list(main_rows)
    # 是否需下探月度归档:start=None(全量,Phase 9 coverage floor)、主文件空、或 start 早于当月。
    need_rotated = (s_month is None) or (main_month is None) or (s_month < main_month)
    if need_rotated:
        for p in sorted(kind_dir.glob("snapshots-*.jsonl")) if kind_dir.exists() else []:
            m = _month_from_rotated_name(p)
            if m is None:
                continue
            if s_month is not None and m < s_month:
                continue
            if e_date is not None and m > e_date[:6]:
                continue
            scanned.extend(_read_jsonl(p))

    asof_dt = _parse_iso(asof) if asof is not None else None

    def _pit_ok(row: Dict[str, Any]) -> bool:
        if asof_dt is None:
            return True
        aa = _parse_iso(row.get("archived_at"))
        return aa is not None and aa <= asof_dt      # 无法定位时刻的行 PIT 下不可见(诚实)

    pit_rows = [r for r in scanned if _pit_ok(r)]
    first_date = min((r["trade_date"] for r in pit_rows), default=None)

    def _window_ok(row: Dict[str, Any]) -> bool:
        td = _to_yyyymmdd(row.get("trade_date"))
        if td is None:
            return False
        if s_date is not None and td < s_date:
            return False
        if e_date is not None and td > e_date:
            return False
        return True

    rows = sorted((r for r in pit_rows if _window_ok(r)), key=lambda r: r["trade_date"])
    return {"kind": kind, "rows": rows, "first_date": first_date}


_ARCHIVE_ENV = "GUANLAN_SNAPSHOT_ARCHIVE"
_CLOSE_HHMM = (15, 5)     # 收盘后门:>=15:05(收盘 15:00 + 数据定格余量)才归档


def maybe_archive_eod(now: Optional[datetime] = None, cache_dir=None, archive_dir=None) -> bool:
    """三门 EOD 归档钩子(挂宿主日跑调度 tick 的单行加法调用)。照
    autonomy.runtime.maybe_enqueue_daily_review 三门模式,三门全过才真归档:
    ①env 总闸 GUANLAN_SNAPSHOT_ARCHIVE=="1"(默认关);②收盘后 now>=15:05(盘中不落,
    数据未定格);③当日 dedup —— archive_eod 自身按当月主文件的 (trade_date, kind) 幂等,
    当日已落则全跳过、无新增。**自吞异常返 False,绝不拖垮宿主 tick**(非交易日 board 池空由
    _backfill_board_pools 徽章语义兜,按 board_date 判重不重复落)。返回 True 仅当本次至少
    归档了一个 kind。"""
    try:
        if os.environ.get(_ARCHIVE_ENV) != "1":
            return False
        now = now or datetime.now()
        if (now.hour, now.minute) < _CLOSE_HHMM:          # 未到 15:05:盘中不归档
            return False
        report = archive_eod(now=now, cache_dir=cache_dir, archive_dir=archive_dir)
        return bool(report.get("archived"))
    except Exception:  # noqa: BLE001 — 归档失败绝不拖垮宿主调度 tick
        return False


__all__ = ["archive_eod", "maybe_archive_eod", "read_archive", "KINDS"]
