# ETF Data Layer (子项目 A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 financial-analyst 加一套 ETF 数据层,使 `ETFLoader` 能读出单只 ETF 的全维度数据(价格 / NAV / 折溢价 / 持仓 / 申赎流 / 费率-基准 / 跟踪误差),为子项目 B(ETF 深度分析)供数。

**Architecture:** 专用命名空间。价格走 pytdx→ `stock_data/cn_data_etf` qlib bin;基金指标走 Tushare `fund_*`→ `stock_data/parquet/etf_*.parquet`;实时折溢价走 akshare→ `etf_spot.parquet`。loader/updater/CLI 落 financial-analyst,数据写共享 `stock_data/`。复用 fa 现有 `bin_writer` / `qlib_binary` loader / `TushareLoader._query` 模式 / pytdx pool。**所有 bin 写入单进程**(吸取本会话并行损坏日历的事故教训)。

**Tech Stack:** Python 3.13;pandas;fa `data/{loaders,updaters}`、`data_cli.py`(typer)、`config/universes/`;Tushare HTTP `fund_*`、pytdx、akshare;pytest(mock 网络)。

**参考 spec:** `docs/superpowers/specs/2026-05-30-etf-data-layer-design.md`

---

## File Structure

| 文件 | 职责 | 新建/改 |
|---|---|---|
| `src/financial_analyst/data/paths.py` | 加 `DataPaths.qlib_etf` 解析(cn_data_etf) | 改 |
| `src/financial_analyst/data/universe.py` | `resolve_universe_codes('etf')` 读 etf.txt | (通用, 加文件即可) |
| `config/universes/etf.txt` | ETF 池种子(保底宽基/行业/主题码) | 新 |
| `src/financial_analyst/data/etf_universe.py` | `build_etf_universe()`:akshare spot top-N ∪ 种子 → 写 etf.txt | 新 |
| `src/financial_analyst/data/updaters/etf_price.py` | `update_etf_daily_batch()`:pytdx→cn_data_etf bin | 新 |
| `src/financial_analyst/data/updaters/etf_fund.py` | `_fund_query()` + 写 etf_basic/nav/share/holdings/div/index.parquet | 新 |
| `src/financial_analyst/data/updaters/etf_spot.py` | akshare → etf_spot.parquet | 新 |
| `src/financial_analyst/data/loaders/etf.py` | `ETFLoader` 7 方法 | 新 |
| `src/financial_analyst/data_cli.py` | `fa data update-etf` 子命令 | 改 |
| `tests/data/test_etf_*.py` | 各组件单测(mock 网络) | 新 |

**码格式约定**(全程统一):qlib `SH510300`/`SZ159915`(内部主格式)↔ Tushare `510300.SH`(复用 `TushareLoader._to_tushare_code`)↔ pytdx `(1,'510300')`(复用 `pytdx_pool.qlib_code_to_pytdx`,要 SH/SZ 前缀)↔ bin 目录 `sh510300`(复用 `bin_writer.code_to_fname`)。

---

## Task 1: ETF 路径解析 (DataPaths.qlib_etf)

**Files:**
- Modify: `src/financial_analyst/data/paths.py`
- Test: `tests/data/test_etf_paths.py` (Create)

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_etf_paths.py
from financial_analyst.data.paths import get_data_paths

def test_qlib_etf_defaults_beside_day(monkeypatch, tmp_path):
    for v in ("FA_QLIB_URI", "FA_QLIB_ETF_URI"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("FA_QLIB_URI", str(tmp_path / "cn_data"))
    assert get_data_paths().qlib_etf == tmp_path / "cn_data_etf"

def test_qlib_etf_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FA_QLIB_ETF_URI", str(tmp_path / "myetf"))
    assert get_data_paths().qlib_etf == tmp_path / "myetf"
```

- [ ] **Step 2: 跑测试确认失败** — `pytest tests/data/test_etf_paths.py -v` → FAIL (`DataPaths` 无 `qlib_etf`)

- [ ] **Step 3: 实现** — `DataPaths` 加字段 + 属性,`get_data_paths` 解析 etf override(env 优先,其次 yaml `provider_uri.etf`(仅当 dict)):

```python
# DataPaths 新增字段 (放 news_data_root 后, 默认 None 保持兼容)
    qlib_etf_uri: Optional[str] = None

    @property
    def qlib_etf(self) -> Path:
        if self.qlib_etf_uri:
            return Path(self.qlib_etf_uri)
        return self.qlib_day.parent / "cn_data_etf"
```

```python
# get_data_paths(): return 前
    env_etf = os.getenv("FA_QLIB_ETF_URI")
    prov = entry.get("provider_uri")
    yaml_etf = prov.get("etf") if isinstance(prov, dict) else None
    etf_uri = env_etf or yaml_etf
    return DataPaths(qlib_uri=qlib_uri, parquet_root=parquet_root,
                     news_data_root=news_data_root, qlib_etf_uri=etf_uri)
```

- [ ] **Step 4: 跑测试确认通过** — `pytest tests/data/test_etf_paths.py -v` → PASS

- [ ] **Step 5: 提交**(commit message 无 Co-Authored-By 行)

```bash
git add src/financial_analyst/data/paths.py tests/data/test_etf_paths.py
git commit -m "feat(data): resolve qlib_etf path (cn_data_etf namespace)"
```

---

## Task 2: ETF 池种子 + universe 解析

**Files:**
- Create: `config/universes/etf.txt` + `src/financial_analyst/_resources/config/universes/etf.txt`(bundled 副本)
- Test: `tests/data/test_etf_universe_resolve.py` (Create)

- [ ] **Step 1: 写种子文件**(两处同内容):

```
# ETF universe seed (broad + major sector/thematic). build_etf_universe 会并入 akshare top-AUM.
SH510300
SH510500
SH510050
SH512100
SH588000
SH588080
SZ159915
SZ159949
SZ159919
SH512880
SH512010
SH512690
SH512660
SH515030
SH516160
SH518880
SH513050
SH510880
```

- [ ] **Step 2: 写失败测试**

```python
# tests/data/test_etf_universe_resolve.py
from financial_analyst.data.universe import resolve_universe_codes

def test_resolve_etf_returns_seed_codes():
    codes = resolve_universe_codes("etf")
    assert "SH510300" in codes and "SZ159915" in codes
    assert all(c[:2] in ("SH", "SZ") for c in codes)
```

- [ ] **Step 3: 跑确认失败** — `pytest tests/data/test_etf_universe_resolve.py -v` → FAIL (resolves to [])

- [ ] **Step 4: 实现** — `resolve_universe_codes` 已通过 `find_config("universes/{name}.txt")` 通用解析;只需 etf.txt 存在 + bundled 副本(供 wheel)。若有 `scripts/sync_bundled_config.py` 用它同步,否则手工放两处。无需改 universe.py 逻辑。

- [ ] **Step 5: 跑确认通过** — PASS

- [ ] **Step 6: 提交**

```bash
git add config/universes/etf.txt src/financial_analyst/_resources/config/universes/etf.txt tests/data/test_etf_universe_resolve.py
git commit -m "feat(data): add etf universe seed + resolver wiring"
```

---

## Task 3: ETF 池构建脚本 (akshare top-AUM ∪ 种子)

**Files:**
- Create: `src/financial_analyst/data/etf_universe.py`
- Test: `tests/data/test_build_etf_universe.py` (Create)

- [ ] **Step 1: 写失败测试**(mock akshare)

```python
# tests/data/test_build_etf_universe.py
import pandas as pd
from financial_analyst.data import etf_universe

def test_build_merges_seed_and_topaum(monkeypatch, tmp_path):
    fake = pd.DataFrame({"代码": ["510300", "159915", "999999"],
                         "总市值": [9e10, 8e10, 1e5], "换手率": [5.0, 4.0, 0.01]})
    monkeypatch.setattr(etf_universe, "_akshare_spot", lambda: fake)
    out = tmp_path / "etf.txt"
    codes = etf_universe.build_etf_universe(top_n=2, seed=["SH510300", "SZ159001"], out_path=out)
    assert "SH510300" in codes and "SZ159001" in codes and "SZ159915" in codes
    assert "SZ999999" not in codes
    assert out.read_text(encoding="utf-8").strip()
```

- [ ] **Step 2: 跑确认失败** — FAIL (module missing)

- [ ] **Step 3: 实现**

```python
# src/financial_analyst/data/etf_universe.py
from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import pandas as pd

def _akshare_spot() -> pd.DataFrame:
    import akshare as ak
    return ak.fund_etf_spot_em()   # 列含 代码/总市值/换手率

def _to_qlib(code6: str) -> str:
    c = str(code6).zfill(6)
    return ("SH" if c[0] in "56" else "SZ") + c   # 5xx→SH, 1xx→SZ

def build_etf_universe(top_n: int = 200, seed: Optional[List[str]] = None,
                       out_path: Optional[Path] = None) -> List[str]:
    spot = _akshare_spot().dropna(subset=["总市值"]).sort_values("总市值", ascending=False)
    top = [_to_qlib(c) for c in spot["代码"].head(top_n)]
    merged = sorted(set((seed or []) + top))
    if out_path:
        Path(out_path).write_text("\n".join(merged) + "\n", encoding="utf-8")
    return merged
```
> akshare 列名以 `fund_etf_spot_em()` 实际为准(实现时 `print(spot.columns)` 核对)。

- [ ] **Step 4: 跑确认通过** — PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/etf_universe.py tests/data/test_build_etf_universe.py
git commit -m "feat(data): build_etf_universe (akshare top-AUM + seed)"
```

---

## Task 4: ETF 价格 updater (pytdx → cn_data_etf bin, 单进程)

**Files:**
- Create: `src/financial_analyst/data/updaters/etf_price.py`
- Test: `tests/data/test_etf_price.py` (Create)

**先读**:`data/updaters/pytdx_kline.py`(`update_daily`/`fetch_daily`/`_DAILY_FIELD_MAP`)、`data/bin_writer.py`(`safe_merge_write`/`load_calendar`/`build_calendar_index`/`save_instruments`/`read_bin`/`code_to_fname`)。

- [ ] **Step 1: 写失败测试**(mock fetch_daily)

```python
# tests/data/test_etf_price.py
from financial_analyst.data.updaters import etf_price
from financial_analyst.data import bin_writer

def test_update_one_writes_bin(tmp_path, monkeypatch):
    etf_uri = tmp_path / "cn_data_etf"; (etf_uri / "calendars").mkdir(parents=True)
    (etf_uri / "calendars" / "day.txt").write_text("2026-05-28\n2026-05-29\n", encoding="utf-8")
    bars = [{"datetime": "2026-05-28 15:00", "open": 4.9, "high": 5.0, "low": 4.8, "close": 4.92, "vol": 100, "amount": 49000},
            {"datetime": "2026-05-29 15:00", "open": 4.92, "high": 4.95, "low": 4.90, "close": 4.94, "vol": 120, "amount": 59000}]
    monkeypatch.setattr(etf_price, "fetch_daily", lambda client, code, n_bars=800: bars)
    etf_price.update_etf_one(str(etf_uri), client=None, code="SH510300")
    si, arr = bin_writer.read_bin("SH510300", "close", "day", str(etf_uri))
    assert round(float(arr[-1]), 2) == 4.94
```

- [ ] **Step 2: 跑确认失败** — FAIL (module missing)

- [ ] **Step 3: 实现** — 镜像 `pytdx_kline.update_daily` 但 provider_uri=ETF 根、vol/100→volume、单进程:

```python
# src/financial_analyst/data/updaters/etf_price.py
from __future__ import annotations
from typing import List, Optional
from financial_analyst.data.bin_writer import (
    build_calendar_index, load_calendar, save_instruments, safe_merge_write, read_bin,
)
from financial_analyst.data.updaters.pytdx_pool import PytdxClient
from financial_analyst.data.updaters.pytdx_kline import fetch_daily, _DAILY_FIELD_MAP

def update_etf_one(etf_uri: str, client, code: str, n_bars: int = 800) -> int:
    cal = load_calendar(etf_uri, freq="day"); cidx = build_calendar_index(cal)
    bars = fetch_daily(client, code, n_bars=n_bars)
    if not bars: return 0
    rows = []
    for b in bars:
        key = b.get("datetime", "")[:10]
        if key not in cidx: continue
        rec = {dst: float(b[src]) * mul for src, (dst, mul) in _DAILY_FIELD_MAP.items()
               if src in b and b[src] is not None}
        rows.append((cidx[key], rec))
    if not rows: return 0
    for dst in {d for _, (d, _) in _DAILY_FIELD_MAP.items()}:
        pv = [(p, r[dst]) for p, r in rows if dst in r]
        if pv:
            safe_merge_write(code, dst, "day", etf_uri, [p for p, _ in pv], [v for _, v in pv])
    return 1

def update_etf_daily_batch(etf_uri: str, codes: List[str], n_bars: int = 800,
                           client: Optional[PytdxClient] = None, progress: bool = True) -> dict:
    own = client is None; client = client or PytdxClient()
    st = {"ok": 0, "empty": 0, "failed": 0, "total": len(codes)}
    try:
        for i, code in enumerate(codes, 1):
            try:
                st["ok" if update_etf_one(etf_uri, client, code, n_bars) else "empty"] += 1
            except Exception:
                st["failed"] += 1
            if progress and i % 50 == 0: print(f"  [etf {i}/{len(codes)}] {st}")
    finally:
        if own: client.close()
    _rebuild_etf_instruments(etf_uri, codes)
    return st
```
`_rebuild_etf_instruments(etf_uri, codes)`:对每个 code `read_bin(code,'close','day',etf_uri)` 取真实首末非 NaN → calendar 日期 → `save_instruments({code:(start,end)}, etf_uri, market="all")`(单进程聚合写一次)。`fetch_daily` 在 etf_price 命名空间 re-export 便于 monkeypatch。**严格单进程,绝不并发**(本会话事故根因)。

- [ ] **Step 4: 跑确认通过** — PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/updaters/etf_price.py tests/data/test_etf_price.py
git commit -m "feat(data): etf price updater (pytdx -> cn_data_etf bin, single-process)"
```

---

## Task 5: ETF 基金指标 updater (Tushare fund_*)

**Files:**
- Create: `src/financial_analyst/data/updaters/etf_fund.py`
- Test: `tests/data/test_etf_fund.py` (Create)

- [ ] **Step 1: 写失败测试**(mock `_fund_query`)

```python
# tests/data/test_etf_fund.py
import pandas as pd
from financial_analyst.data.updaters import etf_fund

def test_update_basic_writes_parquet(tmp_path, monkeypatch):
    fake = pd.DataFrame({"ts_code": ["510300.SH"], "name": ["300ETF"], "management": ["华泰柏瑞"],
                         "m_fee": [0.5], "c_fee": [0.1], "benchmark": ["沪深300指数收益率"],
                         "fund_type": ["ETF"], "invest_type": ["被动指数型"], "list_date": ["20120528"]})
    monkeypatch.setattr(etf_fund, "_fund_query", lambda token, api, **kw: fake)
    monkeypatch.setattr(etf_fund, "_resolve_index_code", lambda b: "000300.SH")
    etf_fund.update_etf_basic(["SH510300"], parquet_root=tmp_path, token="x")
    df = pd.read_parquet(tmp_path / "etf_basic.parquet")
    assert "510300.SH" in df["ts_code"].values
    assert {"m_fee", "c_fee", "benchmark", "index_code"} <= set(df.columns)
```

- [ ] **Step 2: 跑确认失败** — FAIL

- [ ] **Step 3: 实现** — `_fund_query` 镜像 `TushareLoader._query` 但加 `Accept-Encoding: identity`(绕 brotli);token 从 env;各 `update_etf_*` 写 parquet:

```python
# src/financial_analyst/data/updaters/etf_fund.py
from __future__ import annotations
import os, time
from pathlib import Path
from typing import List, Optional
import pandas as pd
from financial_analyst.data.net import domestic_session
from financial_analyst.data.loaders.tushare import TushareLoader

TUSHARE_URL = "http://api.tushare.pro"

def _token(token: Optional[str] = None) -> str:
    t = token or os.getenv("FA_TUSHARE_TOKEN") or os.getenv("TUSHARE_TOKEN")
    if not t: raise ValueError("TUSHARE_TOKEN missing (set FA_TUSHARE_TOKEN)")
    return t

def _fund_query(token: str, api: str, fields: str = "", **params) -> pd.DataFrame:
    req = {"api_name": api, "token": token, "params": params}
    if fields: req["fields"] = fields
    r = domestic_session().post(TUSHARE_URL, json=req, timeout=30,
                                headers={"Accept-Encoding": "identity"})
    d = r.json()
    if d.get("code") != 0: raise Exception(f"tushare {api} failed: {d.get('msg','')}")
    return pd.DataFrame(d["data"]["items"], columns=d["data"]["fields"])

def _ts(codes): return [TushareLoader._to_tushare_code(c) for c in codes]

_INDEX_CACHE = {}
def _resolve_index_code(benchmark: str) -> Optional[str]:
    # 对 index_basic 的 name 做包含匹配; 缓存一次. 匹配不上→None.
    ...

def update_etf_basic(codes, parquet_root, token=None) -> pd.DataFrame:
    df = _fund_query(_token(token), "fund_basic", market="E")
    df = df[df["ts_code"].isin(_ts(codes))].copy()
    df["index_code"] = df["benchmark"].map(_resolve_index_code)
    df.to_parquet(Path(parquet_root) / "etf_basic.parquet", index=False)
    return df

def update_all_fund(codes, parquet_root, token=None):
    tok = _token(token)
    update_etf_basic(codes, parquet_root, tok)
    # nav/share/holdings/div: 逐 ts_code 拉 (这些 API 按单基金查), concat 写 parquet, time.sleep 轻节流
    # index: 读 etf_basic.index_code 去重 → _fund_query("index_daily") 写 etf_index.parquet
    ...
```
> nav→etf_nav(unit_nav/accum_nav/adj_nav/net_asset)、share→etf_share(fd_share)、holdings→etf_holdings(symbol/mkv/stk_mkv_ratio,季度)、div→etf_div(div_cash)。逐码循环失败跳过不中断。单位以 Tushare 文档为准,loader 注释标注。

- [ ] **Step 4: 跑确认通过** — `pytest tests/data/test_etf_fund.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/updaters/etf_fund.py tests/data/test_etf_fund.py
git commit -m "feat(data): etf fund updater (tushare fund_* -> etf_*.parquet, identity header)"
```

---

## Task 6: ETF 实时折溢价 updater (akshare)

**Files:**
- Create: `src/financial_analyst/data/updaters/etf_spot.py`
- Test: `tests/data/test_etf_spot.py` (Create)

- [ ] **Step 1: 写失败测试**(mock akshare)

```python
# tests/data/test_etf_spot.py
import pandas as pd
from financial_analyst.data.updaters import etf_spot

def test_spot_writes_parquet(tmp_path, monkeypatch):
    fake = pd.DataFrame({"代码": ["510300"], "IOPV实时估值": [4.93], "基金折价率": [-0.2],
                         "最新份额": [1.2e10], "总市值": [9e10], "换手率": [3.5]})
    monkeypatch.setattr(etf_spot, "_akshare_spot", lambda: fake)
    etf_spot.update_etf_spot(["SH510300"], parquet_root=tmp_path, asof="2026-05-29")
    df = pd.read_parquet(tmp_path / "etf_spot.parquet")
    row = df[df["ts_code"] == "SH510300"].iloc[0]
    assert {"ts_code","asof","iopv","premium_discount_pct","shares","aum"} <= set(df.columns)
    assert abs(row["premium_discount_pct"] - (-0.2)) < 1e-6
```

- [ ] **Step 2: 跑确认失败** — FAIL

- [ ] **Step 3: 实现**

```python
# src/financial_analyst/data/updaters/etf_spot.py
from __future__ import annotations
from pathlib import Path
from typing import List
import pandas as pd

def _akshare_spot() -> pd.DataFrame:
    import akshare as ak
    return ak.fund_etf_spot_em()

def _to_qlib(c): c = str(c).zfill(6); return ("SH" if c[0] in "56" else "SZ") + c

def update_etf_spot(codes: List[str], parquet_root: Path, asof: str) -> pd.DataFrame:
    s = _akshare_spot().copy(); s["ts_code"] = s["代码"].map(_to_qlib)
    s = s[s["ts_code"].isin(set(codes))]
    out = pd.DataFrame({"ts_code": s["ts_code"], "asof": asof,
        "iopv": s.get("IOPV实时估值"), "premium_discount_pct": s.get("基金折价率"),
        "shares": s.get("最新份额"), "aum": s.get("总市值"), "turnover": s.get("换手率")})
    out.to_parquet(Path(parquet_root) / "etf_spot.parquet", index=False)   # 每次覆盖
    return out
```
> akshare 列名以实际为准。

- [ ] **Step 4: 跑确认通过** — PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/updaters/etf_spot.py tests/data/test_etf_spot.py
git commit -m "feat(data): etf spot updater (akshare premium/discount snapshot)"
```

---

## Task 7: ETFLoader (B 层消费接口)

**Files:**
- Create: `src/financial_analyst/data/loaders/etf.py`
- Test: `tests/data/test_etf_loader.py` (Create)

**先读**:`data/loaders/qlib_binary.py`(构造器实参名 + `fetch_quote`,ETFLoader 内部用它读 cn_data_etf)。

- [ ] **Step 1: 写失败测试**

```python
# tests/data/test_etf_loader.py
import pandas as pd
from financial_analyst.data.loaders.etf import ETFLoader

def test_premium_discount_from_spot(tmp_path):
    pd.DataFrame({"ts_code": ["SH510300"], "asof": ["2026-05-29"], "iopv": [4.93],
                 "premium_discount_pct": [-0.2], "shares": [1e10], "aum": [9e10], "turnover": [3.5]}
                ).to_parquet(tmp_path / "etf_spot.parquet", index=False)
    ld = ETFLoader(parquet_root=tmp_path, etf_qlib_uri=tmp_path / "cn_data_etf")
    assert abs(ld.fetch_etf_premium_discount("SH510300")["realtime_premium_discount_pct"] - (-0.2)) < 1e-6

def test_meta_from_basic(tmp_path):
    pd.DataFrame({"ts_code": ["510300.SH"], "name": ["300ETF"], "m_fee": [0.5], "c_fee": [0.1],
                 "benchmark": ["沪深300"], "index_code": ["000300.SH"], "fund_type": ["ETF"],
                 "invest_type": ["被动指数型"]}).to_parquet(tmp_path / "etf_basic.parquet", index=False)
    ld = ETFLoader(parquet_root=tmp_path, etf_qlib_uri=tmp_path / "cn_data_etf")
    m = ld.fetch_etf_meta("SH510300")
    assert m["total_fee"] == 0.6 and m["index_code"] == "000300.SH"
```

- [ ] **Step 2: 跑确认失败** — FAIL

- [ ] **Step 3: 实现** — 独立类,7 方法读 parquet + bin(quote 复用 QlibBinaryLoader):

```python
# src/financial_analyst/data/loaders/etf.py
from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd
from financial_analyst.data.paths import get_data_paths
from financial_analyst.data.loaders.tushare import TushareLoader

class ETFLoader:
    def __init__(self, parquet_root: Optional[Path] = None, etf_qlib_uri: Optional[Path] = None):
        p = get_data_paths()
        self.parquet_root = Path(parquet_root or p.parquet_root)
        self.etf_qlib_uri = Path(etf_qlib_uri or p.qlib_etf)

    def _pq(self, name): 
        f = self.parquet_root / f"{name}.parquet"
        return pd.read_parquet(f) if f.exists() else pd.DataFrame()

    def _ts(self, code): return TushareLoader._to_tushare_code(code)

    def fetch_etf_quote(self, code, start, end, freq="day"):
        from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
        return QlibBinaryLoader(provider_uri=str(self.etf_qlib_uri)).fetch_quote(code, start, end, freq)
        # ↑ 核对 QlibBinaryLoader 构造器实参名, 对齐

    def fetch_etf_meta(self, code):
        b = self._pq("etf_basic"); row = b[b["ts_code"] == self._ts(code)]
        if row.empty: return {}
        r = row.iloc[0]
        return {"name": r.get("name"), "m_fee": r.get("m_fee"), "c_fee": r.get("c_fee"),
                "total_fee": float(r.get("m_fee", 0) or 0) + float(r.get("c_fee", 0) or 0),
                "benchmark": r.get("benchmark"), "index_code": r.get("index_code"),
                "fund_type": r.get("fund_type"), "invest_type": r.get("invest_type")}

    def fetch_etf_premium_discount(self, code):
        s = self._pq("etf_spot"); row = s[s["ts_code"] == code]
        return {"realtime_premium_discount_pct":
                float(row.iloc[0]["premium_discount_pct"]) if not row.empty else None}
    # fetch_etf_nav / fetch_etf_holdings / fetch_etf_flow / fetch_tracking_error 同构:
    #   nav: etf_nav 按 ts_code; holdings: etf_holdings 最新 end_date top-N;
    #   flow: etf_share fd_share 差分(申赎净额)+ aum=fd_share*unit_nav 趋势;
    #   tracking_error: etf_nav 日收益 vs etf_index(按 meta.index_code) 日收益 std*sqrt(252).
    #   每个配一条 mock-parquet 单测.
```

- [ ] **Step 4: 跑确认通过** — `pytest tests/data/test_etf_loader.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data/loaders/etf.py tests/data/test_etf_loader.py
git commit -m "feat(data): ETFLoader (quote/nav/premium-discount/holdings/flow/meta/tracking-error)"
```

---

## Task 8: CLI `fa data update-etf` + token 治理

**Files:**
- Modify: `src/financial_analyst/data_cli.py`、`.env.example`
- Test: `tests/data/test_etf_cli.py` (Create)

**先读**:`data_cli.py` 的 `update_cmd`(157-300)+ `_resolve_codes` + `_resolve_provider_uri`,镜像。

- [ ] **Step 1: 写失败测试**(typer CliRunner,mock 三管线)

```python
# tests/data/test_etf_cli.py
from typer.testing import CliRunner
from financial_analyst import data_cli

def test_update_etf_calls_pipelines(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("financial_analyst.data.updaters.etf_price.update_etf_daily_batch",
                        lambda *a, **k: calls.append("price") or {"ok":1,"total":1,"empty":0,"failed":0})
    monkeypatch.setattr("financial_analyst.data.updaters.etf_fund.update_all_fund",
                        lambda *a, **k: calls.append("fund"))
    monkeypatch.setattr("financial_analyst.data.updaters.etf_spot.update_etf_spot",
                        lambda *a, **k: calls.append("spot"))
    r = CliRunner().invoke(data_cli.data_app, ["update-etf", "--codes", "SH510300"])
    assert r.exit_code == 0 and {"price","fund","spot"} <= set(calls)
```

- [ ] **Step 2: 跑确认失败** — FAIL (no update-etf)

- [ ] **Step 3: 实现** — `@data_app.command("update-etf")`:解析 codes(默认 `resolve_universe_codes('etf')`)→ 确保 `cn_data_etf/calendars/day.txt` 存在(从 `get_data_paths().qlib_day/calendars/day.txt` 复制)→ `etf_price.update_etf_daily_batch`(单进程)→ `etf_fund.update_all_fund` → `etf_spot.update_etf_spot(asof=today)` → `last_update.mark_updated('etf')`。`.env.example` 加 `FA_TUSHARE_TOKEN=` 注释(旧 `TUSHARE_TOKEN` 已失效,填有效 token)。

- [ ] **Step 4: 跑确认通过** — `pytest tests/data/test_etf_cli.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add src/financial_analyst/data_cli.py .env.example tests/data/test_etf_cli.py
git commit -m "feat(cli): fa data update-etf (price+fund+spot, single-process) + token env note"
```

---

## Task 9: 真数据冒烟验证

**Files:** 无(只跑命令)

- [ ] **Step 1: 配有效 token** — 把有效 token 写进 `.env` 的 `FA_TUSHARE_TOKEN`(从 `G:/stocks/scripts/incremental_update_tushare.py` 取可用 token;**不要硬编码进 src**)。

- [ ] **Step 2: 单只冒烟** — `fa data update-etf --codes SH510300`。预期:`cn_data_etf/features/sh510300/close.day.bin` 生成、`etf_basic/etf_nav/etf_spot.parquet` 含该码行、无报错。

- [ ] **Step 3: ETFLoader 读出验证**

```bash
python -c "from financial_analyst.data.loaders.etf import ETFLoader; L=ETFLoader(); print(L.fetch_etf_meta('SH510300')); print(L.fetch_etf_premium_discount('SH510300'))"
```
预期:total_fee 合理(~0.6)、index_code 解析到、折价率合理。

- [ ] **Step 4: 全量池更新** — `fa data update-etf`(默认 etf.txt ~100-300 只,单进程,~几分钟)。抽查 3 只 ETFLoader 七方法都有数。

- [ ] **Step 5: 提交**(若 etf.txt 被 build 刷新)

```bash
git add config/universes/etf.txt src/financial_analyst/_resources/config/universes/etf.txt
git commit -m "chore(data): refresh etf universe via build_etf_universe"
```

---

## 收尾(全部任务后)
- 跑 `pytest tests/data/test_etf_*.py -v` 确认全绿。
- final code-reviewer(subagent-driven-development 末步)。
- 更新 `docs/data_contract.md`:加 ETF 数据(cn_data_etf + etf_*.parquet)字段/单位/路径约定。
- ⚠ **不碰 stock 的 cn_data**;ETF 完全独立;**所有 bin 写入单进程**(本会话事故教训)。
- commit message **不加** `Co-Authored-By` 行(用户规则)。
```
