"""统一情绪 store —— 观澜情绪判读的单一事实源(数据中台件②)。

收编 T3/T4:此前 rescore/news_search/rerank 各拉各的 LLM、各存各的缓存(三家三 schema、
口径分裂、rescore 全命中时大盘 read/tilt 丢失致 rerank 空转)。本模块统一为两文件:
  var/sentiment/judgments-YYYYMM.jsonl  每行一条 (date,code) 个股情绪判读(同键取最后一条)
  var/sentiment/market-YYYYMM.jsonl     每行一次大盘消息面判读(latest wins)
全 append-only + 月轮转(文件名带 YYYYMM)防无界膨胀。展示型:只存判读口径,绝不碰信号通路。
"""
from __future__ import annotations

import json
import threading
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2] / "var" / "sentiment"
_LOCK = threading.Lock()

_TAG_SCORE = {"利好": 1.0, "中性": 0.0, "利空": -1.0}


def _today() -> str:
    return _date.today().isoformat()


def _norm_day(day: Optional[str]) -> str:
    """入参日期归一到 ISO(YYYY-MM-DD),与存储行的 date 字段同形。
    容 20260707(无连字符,LLM 常见变体)/2026-07-07;脏输入回落今天(评审 Minor:
    dashless 查询曾因逐行串比较失配而假报『今日未判读』)。"""
    if not day:
        return _today()
    s = str(day).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return s if len(s) == 10 and s[4] == "-" else _today()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _month(day: str) -> str:
    """YYYY-MM-DD → YYYYMM(轮转键;脏输入回落当月)。"""
    s = (day or "").replace("-", "")
    return s[:6] if len(s) >= 6 else _date.today().strftime("%Y%m")


def _file(prefix: str, day: str) -> Path:
    return _ROOT / f"{prefix}-{_month(day)}.jsonl"


def _append(path: Path, row: Dict[str, Any]) -> bool:
    """append 一行 jsonl;失败静默 False(缓存写失败不挡上层,代价=下次重判)。"""
    try:
        with _LOCK:
            _ROOT.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    """逐行读 jsonl,脏行跳过(append-only 惯例);缺文件→[]。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ── 个股判读 ─────────────────────────────────────────────────────────────

def read_judgments(codes: Optional[List[str]] = None, day: Optional[str] = None
                   ) -> Dict[str, Optional[dict]]:
    """当日各 code 的最新判读:{code: {tag,read,score}|None}。
    只含当日出现过的 code;值 None=当日判过但无相关新闻(区别于"从未判"=不在 dict)。
    codes 给定则只返其中出现过的;codes=None 返当日全部。"""
    day = _norm_day(day)
    want = {str(c) for c in codes} if codes is not None else None
    out: Dict[str, Optional[dict]] = {}
    for r in _read_lines(_file("judgments", day)):   # 顺序 append,后写覆盖前写
        if r.get("date") != day:
            continue
        c = str(r.get("code") or "")
        if not c or (want is not None and c not in want):
            continue
        tag = r.get("tag")
        out[c] = ({"tag": tag, "read": r.get("read"),
                   "score": _TAG_SCORE.get(tag, r.get("score", 0.0))}
                  if tag in _TAG_SCORE else None)
    return out


def read_judgment(code: str, day: Optional[str] = None) -> Optional[dict]:
    """单票当日最新判读;未判→None(注意:与"判过无新闻"同为 None,用 read_judgments 的 in 判区分)。"""
    return read_judgments([str(code)], day).get(str(code))


def write_judgments(day: str, rows: Dict[str, Optional[dict]], *,
                    as_of: Optional[str] = None, source: str = "") -> int:
    """批量落判读。rows 值={tag,read,score} 或 None(判过无新闻)。返回成功写入行数。"""
    day = _norm_day(day)
    ts = _now()
    path = _file("judgments", day)
    n = 0
    for code, v in (rows or {}).items():
        row: Dict[str, Any] = {"ts": ts, "date": day, "code": str(code),
                               "as_of": as_of, "source": source}
        if isinstance(v, dict) and v.get("tag") in _TAG_SCORE:
            row.update(tag=v["tag"], read=v.get("read"),
                       score=_TAG_SCORE[v["tag"]])
        else:
            row.update(tag=None, read=None, score=None)  # 判过无新闻:显式 null 行
        if _append(path, row):
            n += 1
    return n


# ── 大盘消息面 ───────────────────────────────────────────────────────────

def latest_market(day: Optional[str] = None) -> Dict[str, Any]:
    """当日最新大盘判读:{market_read, market_tilt, as_of, ts};无→全 None。"""
    day = _norm_day(day)
    last = None
    for r in _read_lines(_file("market", day)):
        if r.get("date") == day:
            last = r
    if not last:
        return {"market_read": None, "market_tilt": None, "as_of": None, "ts": None}
    return {"market_read": last.get("market_read"), "market_tilt": last.get("market_tilt"),
            "as_of": last.get("as_of"), "ts": last.get("ts")}


def write_market(day: str, market_read: Optional[str], market_tilt: Optional[str],
                 as_of: Optional[str] = None, source: str = "") -> bool:
    """落一次大盘判读(仅在有 read 或 tilt 时写,避免空行污染)。"""
    if not market_read and not market_tilt:
        return False
    day = _norm_day(day)
    return _append(_file("market", day),
                   {"ts": _now(), "date": day, "market_read": market_read,
                    "market_tilt": market_tilt, "as_of": as_of, "source": source})


# ── 统一读接口(ww_sentiment / 其它消费方一站式)──────────────────────────

def read_summary(code: str = "", day: Optional[str] = None) -> Dict[str, Any]:
    """一站式读:{date, market:{read,tilt,as_of,ts}, judgment:{tag,read,score}|None, judged:bool}。
    judged 区分"从未判"(false)与"判过无新闻"(true+judgment None);零 LLM。"""
    day = _norm_day(day)
    mk = latest_market(day)
    out: Dict[str, Any] = {"date": day, "market": mk, "judgment": None, "judged": False}
    code = (code or "").strip()
    if code:
        js = read_judgments([code], day)
        out["judged"] = code in js
        out["judgment"] = js.get(code)
    return out
