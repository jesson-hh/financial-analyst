# 置信度校准回路 Implementation Plan(0612演习修复#3)

> **状态:已执行完毕并验收(2026-06-12)** — CC1-CC3 审查全过(CC1 合并审查 Approve,两个非阻塞观察:bool confidence 落<60档[LLM不输出bool,挂账]、非补零 asof 保守剔除[安全失败]),pytest **195 绿**(183→195),9999 已拉新;真机冒烟:GET /seats/calibration ok=True、34条研判 mature=0、四档 n=0 hit_rate=None+口径 note——**初期样本不足是设计内正确行为**,出场 bar 随交易日长出后命中率自动浮现。挂账:落子页 UI 徽章(audit_flags+校准合并做)、bool conf 端点层过滤。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `var/seats_decisions.jsonl` 持续积累的研判记录,统计各置信档(<60/60-69/70-79/80+)的**真实 5 日方向命中率**,经 `GET /seats/calibration` 下发,并在帷幄研判结果里把「置信85」旁边贴上该档历史命中率——把 LLM 的情绪数字钉到可证伪的统计上。

**Architecture:** 纯逻辑层 `guanlan_v2/seats/calibration.py`(records+收盘序列注入,零 IO,完全可测)→ IO 层挂 seats/api.py 新端点(读 jsonl + 引擎 loader 拉日线 + 10 分钟模块级缓存)→ ww_seats_decide content 在研判后追加校准行(档内成熟样本 ≥5 才显示命中率,失败静默)。**诚实口径**:基准=asof 当日(或其后首根)收盘进、horizon 根后收盘出,方向命中,不含成本;「观望」不可证伪不计入;未成熟(出场日未到)剔除——当前库里记录多为 06-11/12,初期大概率全档「样本不足」,这是正确行为,价值随时间积累。

**Tech Stack:** Python 3.13 / FastAPI / pytest;校准逻辑零第三方依赖

**硬约束(同前两修):**
- **本仓无 git——禁止 git 命令,"提交"=跑 pytest**
- pytest 口径:`& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`(当前基线 **183 绿**)
- 改 python 后 9999 重启 controller 收口统一做;GateGuard 四事实照做;用户指令原话:「开始3」

**已核事实:**
- 记录读取形:seats/api.py:177-196(`_DEC_LOG.read_text().splitlines()` 逐行 json.loads 坏行跳过);`_DEC_LOG` 模块内已有
- decide 记录字段:kind="decide"、code(如 SH688012)、direction(买入/卖出/观望)、confidence(int 或 None)、asof(YYYY-MM-DD)
- 日线拉取形:seats/api.py:456-470(`loader_factory.get_default_loader().fetch_quote(c, start, end, "day")` 返回 df 含 trade_date/close,同步函数须 `asyncio.to_thread`)
- ww 工具:console/tools.py `_self_get(path)` 已有(cards_query_impl :269 用法);seats_decide_impl :243-261 现有结构(已含 audit_flags ⚠ 行)

---

## Task 1: calibration.py 纯逻辑 + 单元测试(TDD)

**Files:**
- Create: `tests/test_calibration.py`
- Create: `guanlan_v2/seats/calibration.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_calibration.py`(完整文件):

```python
"""置信度校准(0612演习修复#3)单元测试。

口径:基准=asof 当日(或其后首根)收盘进、horizon 根后收盘出,方向命中,不含成本;
观望不可证伪不计入;未成熟(出场 bar 不存在)剔除。
"""
from guanlan_v2.seats.calibration import bucket_of, calibration_table, evaluate

# 合成日线:RISE 每日 +1,FALL 每日 -1(10 根,2026-06-01 起的工作日序)
DATES = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
         "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]
RISE = [(d, 100.0 + i) for i, d in enumerate(DATES)]
FALL = [(d, 100.0 - i) for i, d in enumerate(DATES)]
CLOSES = {"SH600001": RISE, "SH600002": FALL}


def _rec(code, direction, conf, asof, kind="decide"):
    return {"kind": kind, "code": code, "direction": direction,
            "confidence": conf, "asof": asof}


def test_buy_on_rising_is_hit():
    ev = evaluate([_rec("SH600001", "买入", 85, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is True and ev[0]["ret"] > 0


def test_buy_on_falling_is_miss():
    ev = evaluate([_rec("SH600002", "买入", 85, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is False and ev[0]["ret"] < 0


def test_sell_on_falling_is_hit():
    ev = evaluate([_rec("SH600002", "卖出", 70, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is True


def test_watch_excluded():
    assert evaluate([_rec("SH600001", "观望", 60, "2026-06-01")], CLOSES) == []


def test_immature_excluded():
    # asof=06-10,horizon=5 → 出场 bar 超出序列 → 未成熟剔除
    assert evaluate([_rec("SH600001", "买入", 85, "2026-06-10")], CLOSES, horizon=5) == []


def test_missing_code_or_conf_excluded():
    assert evaluate([_rec("SH999999", "买入", 85, "2026-06-01")], CLOSES) == []
    assert evaluate([_rec("SH600001", "买入", None, "2026-06-01")], CLOSES) == []


def test_asof_on_non_trading_day_uses_next_bar():
    # 06-06/06-07 是周末:asof=06-06 → 基准取 06-08 那根
    ev = evaluate([_rec("SH600001", "买入", 80, "2026-06-06")], CLOSES, horizon=3)
    assert len(ev) == 1
    assert abs(ev[0]["ret"] - (108.0 / 105.0 - 1)) < 1e-9   # 06-08 收105 → +3根=06-11 收108


def test_non_decide_kind_excluded():
    assert evaluate([_rec("SH600001", "买入", 85, "2026-06-01", kind="order")], CLOSES) == []


def test_bucket_boundaries():
    assert bucket_of(59) == "<60" and bucket_of(60) == "60-69"
    assert bucket_of(79) == "70-79" and bucket_of(80) == "80+" and bucket_of(100) == "80+"


def test_calibration_table_aggregates():
    ev = evaluate([
        _rec("SH600001", "买入", 85, "2026-06-01"),   # hit
        _rec("SH600002", "买入", 88, "2026-06-01"),   # miss
        _rec("SH600002", "卖出", 65, "2026-06-01"),   # hit
    ], CLOSES, horizon=5)
    table = calibration_table(ev)
    by = {b["bucket"]: b for b in table}
    assert by["80+"]["n"] == 2 and by["80+"]["hits"] == 1 and abs(by["80+"]["hit_rate"] - 0.5) < 1e-9
    assert by["60-69"]["n"] == 1 and abs(by["60-69"]["hit_rate"] - 1.0) < 1e-9
    assert by["<60"]["n"] == 0 and by["<60"]["hit_rate"] is None


def test_empty_inputs_safe():
    assert evaluate([], {}) == []
    table = calibration_table([])
    assert len(table) == 4 and all(b["n"] == 0 for b in table)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_calibration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guanlan_v2.seats.calibration'`

- [ ] **Step 3: 写实现** — 创建 `guanlan_v2/seats/calibration.py`(完整文件):

```python
"""置信度校准(0612演习修复#3)。

把研判记录的 LLM 置信度钉到可证伪统计上:各置信档的真实 N 日方向命中率。
口径(诚实声明,所有展示处沿用):
  基准 = asof 当日(或其后首根)收盘价进、horizon 根 bar 后收盘价出;
  命中 = 买入→区间收益>0 / 卖出→区间收益<0;不含交易成本;
  「观望」不可证伪 → 不计入;出场 bar 未到(未成熟)→ 剔除,等数据长出来。
纯函数零 IO:records 与收盘序列由调用方注入(端点层负责读 jsonl + 拉日线)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# (下界含, 上界不含, 档名)
_BUCKETS = [(0, 60, "<60"), (60, 70, "60-69"), (70, 80, "70-79"), (80, 101, "80+")]


def bucket_of(conf: float) -> Optional[str]:
    for lo, hi, name in _BUCKETS:
        if lo <= conf < hi:
            return name
    return None


def evaluate(records: List[Dict[str, Any]],
             closes_by_code: Dict[str, Sequence[Tuple[str, float]]],
             horizon: int = 5) -> List[Dict[str, Any]]:
    """成熟记录打分:返回 [{code,direction,confidence,asof,ret,hit}]。

    closes_by_code: {代码: [(YYYY-MM-DD, close), ...] 升序};缺码/缺值/未成熟/观望 → 剔除。
    """
    out: List[Dict[str, Any]] = []
    for r in records or []:
        try:
            if r.get("kind") != "decide":
                continue
            d = r.get("direction")
            if d not in ("买入", "卖出"):
                continue
            conf = r.get("confidence")
            if not isinstance(conf, (int, float)):
                continue
            code = str(r.get("code", "")).upper()
            asof = str(r.get("asof", ""))[:10]
            series = closes_by_code.get(code)
            if not series or not asof:
                continue
            idx = next((i for i, (dt, _) in enumerate(series) if dt >= asof), None)
            if idx is None or idx + horizon >= len(series):
                continue   # asof 晚于全序列 / 出场 bar 未到 → 未成熟
            entry, exitp = float(series[idx][1]), float(series[idx + horizon][1])
            if not entry:
                continue
            ret = exitp / entry - 1.0
            hit = (ret > 0) if d == "买入" else (ret < 0)
            out.append({"code": code, "direction": d, "confidence": float(conf),
                        "asof": asof, "ret": ret, "hit": hit})
        except Exception:  # noqa: BLE001 — 单条坏记录跳过,不挡整体
            continue
    return out


def calibration_table(evaluated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按置信档聚合:[{bucket, n, hits, hit_rate}](空档 n=0 hit_rate=None)。"""
    table = []
    for lo, hi, name in _BUCKETS:
        rows = [e for e in (evaluated or []) if lo <= e["confidence"] < hi]
        hits = sum(1 for e in rows if e["hit"])
        table.append({"bucket": name, "n": len(rows), "hits": hits,
                      "hit_rate": (hits / len(rows)) if rows else None})
    return table
```

- [ ] **Step 4: 跑测试确认通过**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_calibration.py -q`
Expected: **11 passed**

- [ ] **Step 5: 跑全量**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: **194 passed, 0 failed**(183+11)

## Task 2: GET /seats/calibration 端点(IO 层 + 缓存)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(`/decisions` 端点之后加新端点;`_DEC_LOG` 附近加缓存)

- [ ] **Step 1: 模块级缓存** — 在 `_DEC_LOG` 定义附近加:

```python
_CALIB_CACHE: dict = {}          # horizon → (epoch_ts, payload);TTL 600s
_CALIB_TTL = 600.0
```

- [ ] **Step 2: 新端点** — 在 `/decisions` 端点函数之后加:

```python
    @router.get("/calibration")
    async def seats_calibration(horizon: int = 5):
        """置信度校准:各置信档的真实 N 日方向命中率(口径见 calibration.py docstring)。
        读全量 decide 记录 + 引擎日线;10 分钟缓存;失败恒 HTTP200 诚实降级。"""
        try:
            hz = max(1, min(int(horizon or 5), 20))
            import time as _t
            hit = _CALIB_CACHE.get(hz)
            if hit and _t.time() - hit[0] < _CALIB_TTL:
                return JSONResponse(hit[1])

            records: list = []
            if _DEC_LOG.exists():
                for ln in _DEC_LOG.read_text(encoding="utf-8").splitlines():
                    try:
                        records.append(json.loads(ln))
                    except Exception:  # noqa: BLE001 — 坏行跳过
                        continue
            codes = sorted({str(r.get("code", "")).upper() for r in records
                            if r.get("kind") == "decide" and r.get("code")})

            import pandas as _pd
            from financial_analyst.data import loader_factory as _lf
            loader = _lf.get_default_loader()
            start = str((_pd.Timestamp.now() - _pd.Timedelta(days=400)).date())
            end = str(_pd.Timestamp.now().date())

            def _closes(c: str):
                df = loader.fetch_quote(c, start, end, "day")
                if df is None or len(df) == 0 or "close" not in df.columns:
                    return []
                dcol = "trade_date" if "trade_date" in df.columns else df.columns[0]
                return [(str(d)[:10], float(v)) for d, v in zip(df[dcol], df["close"])]

            closes_by_code: dict = {}
            for c in codes[:40]:   # 防御上限:跨票过多时截断(note 里写明)
                try:
                    closes_by_code[c] = await asyncio.to_thread(_closes, c)
                except Exception:  # noqa: BLE001 — 单票取数失败 → 该票记录自然剔除
                    closes_by_code[c] = []

            from guanlan_v2.seats.calibration import calibration_table, evaluate
            ev = evaluate(records, closes_by_code, horizon=hz)
            payload = {
                "ok": True, "horizon": hz,
                "total_decides": sum(1 for r in records if r.get("kind") == "decide"),
                "mature": len(ev),
                "buckets": calibration_table(ev),
                "note": ("口径:asof(或其后首根)收盘进、+N根收盘出,方向命中,不含成本;"
                         "观望不计入;未成熟剔除" + ("" if len(codes) <= 40 else f";票数{len(codes)}>40 已截断")),
            }
            _CALIB_CACHE[hz] = (_t.time(), payload)
            return JSONResponse(payload)
        except Exception as exc:  # noqa: BLE001 — 诚实降级
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
```

注意:`asyncio`/`json`/`JSONResponse` 该文件已 import(对照文件头确认,缺了就近补);**协程内严禁同步自 HTTP/同步重 IO 不经 to_thread**(loader 已包 to_thread ✓)。

- [ ] **Step 3: 语法冒烟 + 全量 pytest** — `ast.parse` seats/api.py OK;全量 Expected: 194 passed(端点逻辑由 Task 1 纯函数测试+收口真机冒烟覆盖,不写挂 app 集成测试——与 /decisions 端点现状口径一致)

## Task 3: ww 研判结果贴校准行 + 测试

**Files:**
- Modify: `guanlan_v2/console/tools.py` seats_decide_impl(:243-261)
- Modify: `tests/test_console_tools.py`(加 1 测试)

- [ ] **Step 1:** seats_decide_impl 在 `_af_line` 计算之后、return 之前加:

```python
    _cal_line = ""
    try:
        conf = r.get("confidence")
        if isinstance(conf, (int, float)):
            cal = _self_get("/seats/calibration?horizon=5")
            if cal.get("ok"):
                from guanlan_v2.seats.calibration import bucket_of
                b = bucket_of(float(conf))
                row = next((x for x in (cal.get("buckets") or []) if x.get("bucket") == b), None)
                if row and (row.get("n") or 0) >= 5:
                    _cal_line = (f"\n📐 置信校准: {b}档历史5日命中率 "
                                 f"{row['hit_rate'] * 100:.0f}%(n={row['n']})")
                elif row is not None:
                    _cal_line = f"\n📐 置信校准: {b}档成熟样本不足(n={row.get('n', 0)}),暂以原始置信为准"
    except Exception:  # noqa: BLE001 — 校准失败静默,不影响研判内容
        _cal_line = ""
```

并把 return 的 content 结尾从 `+ _af_line),` 改为 `+ _af_line + _cal_line),`。

- [ ] **Step 2:** `tests/test_console_tools.py` 追加(沿用现有 monkeypatch 风格):

```python
def test_seats_decide_content_shows_calibration(monkeypatch):
    from guanlan_v2.console import tools as ct
    monkeypatch.setattr(ct, "_self_post", lambda *a, **k: {
        "ok": True, "code": "SH688012", "name": "中微公司", "direction": "买入",
        "confidence": 85, "rationale": "x", "audit_flags": []})
    monkeypatch.setattr(ct, "_self_get", lambda *a, **k: {
        "ok": True, "buckets": [{"bucket": "80+", "n": 12, "hits": 7, "hit_rate": 7 / 12}]})
    r = ct.seats_decide_impl("SH688012", name="中微公司")
    assert r["ok"] and "置信校准" in r["content"] and "58%" in r["content"] and "n=12" in r["content"]
```

- [ ] **Step 3: 全量 pytest** — Expected: **195 passed, 0 failed**

## Task 4: 收口(controller 亲自做)

- [ ] 全量 pytest 终验(预期 195 绿)
- [ ] 重启 9999 + 探活
- [ ] 真机冒烟:`GET /seats/calibration?horizon=5` → ok:True、buckets 4 档、note 口径句;**当前库 asof 多为 06-11/12,预期 mature≈0 全档样本不足——正确行为非失败**(若历史里有早期 decide 记录则可能有少量成熟样本,核数字合理即可)
- [ ] memory 收口(live-drill 修复#3 行 + MEMORY.md;挂账:初期长期「样本不足」属预期、落子页 UI 徽章)

---

## Self-Review(已执行)

- 覆盖:#3 三层(纯逻辑/端点/帷幄显形)全有任务;初期样本不足的诚实行为写进规格与冒烟预期;观望剔除、未成熟剔除、非交易日 asof 顺延全有测试。
- 占位符扫描:无;代码全给。
- 类型一致:`evaluate/calibration_table/bucket_of` 签名全文一致;端点 payload 键(ok/horizon/total_decides/mature/buckets/note)与 Task 3 消费处一致;hit_rate=None 时 Task 3 有 n>=5 守卫不触 None 乘法。
