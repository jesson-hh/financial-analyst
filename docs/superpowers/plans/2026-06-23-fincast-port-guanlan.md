# FinCast 生成栈港进 guanlan(Spec 2)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 FinCast 生成栈港进 guanlan —— vendor 模型代码+4GB 权重,guanlan 自有 `scripts/fincast_predict.py`(读 guanlan close · 直写 `var/v4_fincast_pred.parquet` · 去 sync),用 conda stocks GPU 解释器跑。

**Architecture:** 一次性 vendor(`vendor/fincast_repo/` + `vendor/models/fincast/v1.pth`,gitignore)。纯函数 helper(`guanlan_v2/strategy/compute/fincast_io.py`:`build_context_matrix` + `write_pred_rolling`)可 TDD;`scripts/fincast_predict.py` 用 guanlan `QlibBinaryLoader._read_bin` 逐码读 close 建面板(= breadth 路径)+ 内置 `FinCastAdapter`(vendored code + v1.pth · GPU)+ helper 写出。Spec 1 的层不改即读 `var/v4_fincast_pred.parquet`。

**Tech Stack:** Python 3.13(guanlan 主env 跑测试)+ conda stocks(GPU 跑脚本)、pandas/numpy、pytest、torch/CUDA(FinCast)。**前置参考**:`docs/superpowers/specs/2026-06-23-fincast-port-guanlan-design.md`。

**全局坑**:
- **两个 Python 环境**:测试用 guanlan 主 env(`python -m pytest`,从仓根 `G:/guanlan-v2`);真脚本用 **conda stocks**(`D:/app/miniconda/envs/stocks/python.exe`,GPU)。纯 helper(fincast_io)在两 env 都可 import(只 numpy/pandas)。
- **GateGuard**:每文件首改先报 facts。**引擎 fork 路径**:测试顶 prepend 仓内 `engine/`。
- **4GB 权重**:`vendor/models/fincast/v1.pth` gitignore 必须覆盖,别误入库。
- **当前在 main**(工作树干净);本系列建议在 main 或独立分支做。

---

### Task 1: vendor 港移(模型代码 + 4GB 权重 + gitignore + setup 文档)

把 FinCast 模型代码与权重从 stocks 拷进 guanlan,gitignore,写 setup 文档。无单测(setup);Step 4 验证。

**Files:**
- Create(拷贝):`vendor/fincast_repo/`(从 `G:/stocks/tsfm_exp/fincast_repo`)、`vendor/models/fincast/v1.pth`(从 `G:/stocks/tsfm_exp/models/fincast/v1.pth`,3.97GB)
- Modify: `.gitignore`(加 `vendor/fincast_repo/`、`vendor/models/`)
- Create: `scripts/setup_fincast.md`

- [ ] **Step 1: 拷贝 vendor(conda 无关,纯文件)**

```bash
mkdir -p /g/guanlan-v2/vendor/models/fincast
cp -r "/g/stocks/tsfm_exp/fincast_repo" /g/guanlan-v2/vendor/fincast_repo
cp "/g/stocks/tsfm_exp/models/fincast/v1.pth" /g/guanlan-v2/vendor/models/fincast/v1.pth
ls -d /g/guanlan-v2/vendor/fincast_repo/src && ls -la /g/guanlan-v2/vendor/models/fincast/v1.pth
```
Expected: `src/` 存在 + `v1.pth` ~3.97GB。

- [ ] **Step 2: gitignore(防 4GB / 外部代码入库)**

在 `.gitignore` 追加(先 Read 确认未重复):
```
# Spec 2: vendored FinCast 模型代码 + 4GB 权重(外部·git clone vincent05r/FinCast-fts)
vendor/fincast_repo/
vendor/models/
```
验证未跟踪:`cd /g/guanlan-v2 && git status --short vendor/ | head` → 应**无**输出(被 ignore)。

- [ ] **Step 3: setup 文档** —— 新建 `scripts/setup_fincast.md`:

```markdown
# FinCast 港移 setup(一次性)

FinCast 生成栈(模型代码 + 4GB 权重)体积大、为外部资产,gitignore 不入库。新机/重置后按此还原:

## 1. 模型代码(FinCast-fts)
    git clone https://github.com/vincent05r/FinCast-fts vendor/fincast_repo
  或从既有机器拷:`cp -r G:/stocks/tsfm_exp/fincast_repo vendor/fincast_repo`
  需提供 `vendor/fincast_repo/src/tools/inference_utils.py`(get_model_api)+ ffm/data_tools。

## 2. 权重(v1.pth · 3.97GB)
    放到 vendor/models/fincast/v1.pth
  从既有机器:`cp G:/stocks/tsfm_exp/models/fincast/v1.pth vendor/models/fincast/v1.pth`

## 3. GPU 解释器
  用 conda stocks 环境跑(已装 torch cu128 + FinCast 依赖):
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date <总市值覆盖日>

## 刷新 DL 参与(日常)
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date <D>
    python -m guanlan_v2.strategy.compute.regen <D>
  然后重启 9999(刷 LRU)。
```

- [ ] **Step 4: 提交(只提 .gitignore + 文档;vendor 被 ignore 不入库)**

```bash
cd /g/guanlan-v2 && git add .gitignore scripts/setup_fincast.md
git commit -m "chore(fincast-port): vendor FinCast 代码/权重(gitignore)+ setup 文档

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `fincast_io.py` 纯函数 helper(context 矩阵 + rolling 写出)· TDD

**Files:**
- Create: `guanlan_v2/strategy/compute/fincast_io.py`
- Test: `tests/test_fincast_io.py`

- [ ] **Step 1: 写失败测试** —— 新建 `tests/test_fincast_io.py`:

```python
# tests/test_fincast_io.py
# FinCast 港移纯函数门禁:close 面板→context 矩阵(末N日/有效标的过滤/ffill);rolling-keep 写出契约。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.fincast_io import build_context_matrix, write_pred_rolling  # noqa: E402


def _panel(dates, codes, fill=1.0):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({c: [fill] * len(idx) for c in codes}, index=idx)


def test_build_context_matrix_shape_and_tail():
    dates = pd.date_range("2026-01-01", periods=600, freq="D")
    panel = _panel(dates, ["SH600000", "SZ000001"])
    chosen, arr = build_context_matrix(panel, "2026-06-22", context_len=512, min_valid_frac=0.9)
    assert set(chosen) == {"SH600000", "SZ000001"}
    assert arr.shape == (2, 512)               # (N instruments, T context)
    assert arr.dtype == np.float32


def test_build_context_matrix_drops_invalid():
    dates = pd.date_range("2026-01-01", periods=600, freq="D")
    panel = _panel(dates, ["SH600000", "SZ000001"])
    panel["SZ000001"] = np.nan                  # 全 NaN → 无效
    chosen, arr = build_context_matrix(panel, "2026-06-22", context_len=512)
    assert chosen == ["SH600000"]
    assert arr.shape == (1, 512)


def test_build_context_matrix_cuts_to_eval_date_no_future():
    dates = pd.date_range("2026-01-01", periods=600, freq="D")
    panel = _panel(dates, ["SH600000"])
    # eval_date 取中间某天:窗口末日 = eval_date,不含未来
    chosen, arr = build_context_matrix(panel, "2026-05-01", context_len=100)
    assert arr.shape == (1, 100)
    # 不抛、窗口截到 eval_date(由 panel.loc[:eval_date] 保证)


def test_build_context_matrix_too_short_raises():
    dates = pd.date_range("2026-01-01", periods=50, freq="D")
    panel = _panel(dates, ["SH600000"])
    try:
        build_context_matrix(panel, "2026-02-15", context_len=512)
        assert False, "应抛 ValueError(面板太短)"
    except ValueError:
        pass


def test_write_pred_rolling_contract_and_overwrite(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    # 首写
    df1 = write_pred_rolling(p, "2026-06-20", ["SH600000", "SZ000001"], [0.01, -0.02], keep_days=60)
    assert list(df1.columns) == ["eval_date", "instrument", "pred_ret_5d"]   # 扁平契约
    # 同日重写覆盖(不重复)
    write_pred_rolling(p, "2026-06-20", ["SH600000"], [0.05], keep_days=60)
    out = pd.read_parquet(p)
    d20 = out[pd.to_datetime(out["eval_date"]) == pd.Timestamp("2026-06-20")]
    assert len(d20) == 1 and float(d20.iloc[0]["pred_ret_5d"]) == 0.05   # 覆盖,留最后
    # 新日期累加
    write_pred_rolling(p, "2026-06-21", ["SH600000"], [0.03], keep_days=60)
    out = pd.read_parquet(p)
    assert pd.to_datetime(out["eval_date"]).nunique() == 2


def test_write_pred_rolling_keep_days(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    for i in range(5):
        write_pred_rolling(p, f"2026-06-{10+i:02d}", ["SH600000"], [0.01 * i], keep_days=3)
    out = pd.read_parquet(p)
    assert pd.to_datetime(out["eval_date"]).nunique() == 3   # 只保留最近 3 日
    assert pd.to_datetime(out["eval_date"]).max() == pd.Timestamp("2026-06-14")
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_fincast_io.py -v` → FAIL（ModuleNotFoundError: fincast_io）。

- [ ] **Step 3: 实现** —— 新建 `guanlan_v2/strategy/compute/fincast_io.py`:

```python
# -*- coding: utf-8 -*-
"""FinCast 港移纯函数 helper:close 面板→context 矩阵 + 预测表 rolling-keep 写出。
无 GPU/无引擎依赖(只 numpy/pandas),guanlan 主env 与 conda stocks 都可 import。
"""
from __future__ import annotations

import os
from typing import List, Tuple

import numpy as np
import pandas as pd


def build_context_matrix(panel: pd.DataFrame, eval_date, context_len: int = 512,
                         min_valid_frac: float = 0.9) -> Tuple[List[str], np.ndarray]:
    """close 面板(datetime 索引 × instrument 列)→ (chosen 标的, (N×context_len) float32 矩阵)。
    截到 ≤ eval_date(不看未来),取末 context_len 日;末值非空 & 非NaN比例≥min_valid_frac 才入选;
    ffill→bfill 补窗内洞。面板长度 < context_len 抛 ValueError。"""
    panel = panel.loc[:pd.Timestamp(eval_date)]
    if len(panel) < context_len:
        raise ValueError(f"面板长度 {len(panel)} < context_len {context_len};多拉历史")
    window = panel.tail(context_len)
    last_row = window.iloc[-1]
    valid_frac = window.notna().mean(axis=0)
    mask = last_row.notna() & (valid_frac >= min_valid_frac)
    chosen = window.columns[mask].tolist()
    if not chosen:
        raise ValueError("无有效标的(末值非空 + 非NaN比例达标)")
    sub = window[chosen].ffill().bfill()
    arr = sub.to_numpy(dtype=np.float32).T   # (N, T)
    return chosen, arr


def write_pred_rolling(out_path: str, eval_date, chosen: List[str], preds,
                       keep_days: int = 60) -> pd.DataFrame:
    """写 FinCast 预测表(扁平契约 eval_date/instrument/pred_ret_5d):同日覆盖 + 只保留最近 keep_days 日。
    返回写入后的全表 DataFrame。"""
    ed = pd.Timestamp(eval_date)
    new_df = pd.DataFrame({"eval_date": ed, "instrument": list(chosen),
                           "pred_ret_5d": np.asarray(preds, dtype=np.float32)})
    if os.path.exists(out_path):
        old = pd.read_parquet(out_path)
        if "eval_date" not in old.columns:
            old = old.reset_index()
        old = old[pd.to_datetime(old["eval_date"]) != ed]      # 同日覆盖
        combined = pd.concat([old[["eval_date", "instrument", "pred_ret_5d"]], new_df], ignore_index=True)
        dates = sorted(pd.to_datetime(combined["eval_date"]).unique())
        if len(dates) > keep_days:
            keep = set(pd.to_datetime(dates[-keep_days:]))
            combined = combined[pd.to_datetime(combined["eval_date"]).isin(keep)]
    else:
        combined = new_df
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    combined = combined.reset_index(drop=True)
    combined.to_parquet(out_path, index=False)
    return combined
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_fincast_io.py -v` → PASS（6 passed）。

- [ ] **Step 5: 提交**

```bash
cd /g/guanlan-v2 && git add tests/test_fincast_io.py guanlan_v2/strategy/compute/fincast_io.py
git commit -m "feat(fincast-port): fincast_io 纯函数(context矩阵+rolling写出·TDD)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `scripts/fincast_predict.py`(guanlan 自有推理脚本)

移植 stocks `fincast_daily_predict.py`,close 读取改 guanlan `_read_bin`、输出直写 var、内置 `FinCastAdapter`、用 fincast_io helper。GPU 部分由 Task 5 真机验。

**Files:**
- Create: `scripts/fincast_predict.py`

- [ ] **Step 1: 先 Read 参考源**
  - `engine/financial_analyst/data/loaders/qlib_binary.py`(`QlibBinaryLoader` + `_read_bin(code, field)` 返回值/索引 —— 确认是 datetime 索引 Series)
  - `guanlan_v2/strategy/compute/breadth.py:48-92`(`list_all_instruments` + `loader._read_bin(code,"close")` 用法 —— 逐码读 close 建面板的真实范式)
  - `guanlan_v2/strategy/compute/regen.py:35,122`(`DEFAULT_PROVIDER` + `_latest_trade_date`)
  - `G:/stocks/tsfm_exp/scripts/zero_shot_daily.py`(`class FinCastAdapter` —— 移植它,改 `fincast_repo` 路径指 guanlan vendor)

- [ ] **Step 2: 实现** —— 新建 `scripts/fincast_predict.py`:

```python
# -*- coding: utf-8 -*-
"""FinCast v1 零样本每日批量推理(guanlan 自有·港移自 stocks tsfm_exp)。

用 conda stocks GPU 解释器跑:
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date 2026-06-22

读 guanlan 自己的 close(QlibBinaryLoader 直读二进制)→ FinCast(vendor/fincast_repo + v1.pth · GPU)
→ pred_ret_5d → 直写 var/v4_fincast_pred.parquet(Spec 1 DL 集成层契约;去 sync)。
**命门**:GPU 推理离线;9999 请求路径绝不跑模型。
"""
import argparse
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "engine"))                    # financial_analyst
sys.path.insert(0, str(_REPO / "vendor" / "fincast_repo" / "src"))  # tools/ffm/data_tools

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader  # noqa: E402
from guanlan_v2.strategy.compute.breadth import list_all_instruments     # noqa: E402
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date  # noqa: E402
from guanlan_v2.strategy.compute.fincast_io import build_context_matrix, write_pred_rolling  # noqa: E402

CONTEXT_LEN, HORIZON, BATCH = 512, 5, 64
WEIGHTS = str(_REPO / "vendor" / "models" / "fincast" / "v1.pth")
OUT = str(_REPO / "var" / "v4_fincast_pred.parquet")


class FinCastAdapter:
    """港移自 stocks zero_shot_daily.FinCastAdapter:vendored 代码 + v1.pth · GPU。"""
    def __init__(self, weights: str, horizon: int = HORIZON, context_len: int = CONTEXT_LEN):
        if not os.path.exists(weights):
            raise FileNotFoundError(f"FinCast 权重缺:{weights}(见 scripts/setup_fincast.md)")
        from tools.inference_utils import get_model_api   # noqa: WPS433 (vendor/fincast_repo/src)
        cfg = SimpleNamespace(
            backend="gpu", model_path=weights, model_version="v1",
            horizon_len=horizon, context_len=min(max(context_len, 32), 1024),
            num_experts=4, gating_top_n=2, load_from_compile=True, forecast_mode="mean",
        )
        print(f"[fincast] 加载 {weights} ...", flush=True)
        self.ffm_api = get_model_api(cfg)
        self.horizon = horizon

    def predict(self, contexts: np.ndarray, horizon: int) -> np.ndarray:
        # ffm_api.forecast(list[1d array], list[freq]) -> (mean, full);取末日预测价转 5 日收益。
        seqs = [contexts[i].astype(np.float32) for i in range(contexts.shape[0])]
        mean, _ = self.ffm_api.forecast(seqs, ["D"] * len(seqs))
        mean = np.asarray(mean, dtype=np.float32)          # (N, horizon)
        last_close = contexts[:, -1]                        # (N,)
        end_price = mean[:, -1]                             # (N,) 第 h 天预测价
        return end_price / last_close - 1.0
```

```python
def _read_close_panel(loader, codes, eval_date, context_len):
    """逐码 _read_bin(code,'close')(= breadth 路径)→ (datetime × instrument) close 面板,
    截到 ≤ eval_date。"""
    series = {}
    for code in codes:
        try:
            c = loader._read_bin(code, "close")            # datetime 索引 Series(mirror breadth.py:88)
            if c is not None and len(c):
                series[code] = c
        except Exception:                                   # noqa: BLE001 — 单码读失败跳过
            continue
    if not series:
        raise RuntimeError("无任何可读 close(检查 provider_uri)")
    panel = pd.DataFrame(series).sort_index()
    panel.index = pd.DatetimeIndex(panel.index)
    return panel.loc[:pd.Timestamp(eval_date)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=guanlan 最新交易日)")
    ap.add_argument("--context-len", type=int, default=CONTEXT_LEN)
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--batch-size", type=int, default=BATCH)
    ap.add_argument("--min-valid-frac", type=float, default=0.9)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    args = ap.parse_args()

    eval_date = args.date or _latest_trade_date(args.provider)
    print(f"评估日 {eval_date} · provider {args.provider}", flush=True)
    loader = QlibBinaryLoader(args.provider)
    codes = list_all_instruments(args.provider)
    print(f"全市场 {len(codes)} 码,读 close 面板 ...", flush=True)
    panel = _read_close_panel(loader, codes, eval_date, args.context_len)
    chosen, arr = build_context_matrix(panel, eval_date, args.context_len, args.min_valid_frac)
    print(f"有效标的 {len(chosen)} 只 · 窗口 {arr.shape[1]} 日,加载 FinCast ...", flush=True)

    adapter = FinCastAdapter(WEIGHTS, horizon=args.horizon, context_len=args.context_len)
    t0 = time.time()
    preds = []
    for b in range(0, len(chosen), args.batch_size):
        batch = arr[b:b + args.batch_size]
        preds.append(np.asarray(adapter.predict(batch, args.horizon), dtype=np.float32))
        done = min(b + args.batch_size, len(chosen))
        if (b // args.batch_size) % 10 == 0 or done == len(chosen):
            print(f"  {done}/{len(chosen)} 耗时 {time.time() - t0:.1f}s", flush=True)
    preds = np.concatenate(preds)
    print(f"完成 {len(preds)} 条 · 均值 {preds.mean():+.4f} · {time.time() - t0:.1f}s", flush=True)

    out = write_pred_rolling(OUT, eval_date, chosen, preds, keep_days=60)
    print(f"已写 {OUT}({len(out)} 条 · {pd.to_datetime(out['eval_date']).nunique()} 日)", flush=True)


if __name__ == "__main__":
    main()
```

注:`ffm_api.forecast` 的真实返回/入参形以 `vendor/fincast_repo/src/tools/inference_utils` 为准 —— Step 1 Read 时核对(stocks `FinCastAdapter.predict` 即此口径);若 forecast 签名不同,按 vendored 真实 API 调整 `predict`(本步唯一可能需现场对齐处,GPU 真跑见 Task 5)。

- [ ] **Step 3: 语法/import 冒烟(guanlan 主 env,不跑 GPU)**

Run: `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('scripts/fincast_predict.py',encoding='utf-8').read()); print('语法 ok')"`
Expected: `语法 ok`。(真 import 含 vendor/torch,留 conda stocks 真跑 Task 5。)

- [ ] **Step 4: 提交**

```bash
cd /g/guanlan-v2 && git add scripts/fincast_predict.py
git commit -m "feat(fincast-port): guanlan 自有 fincast_predict.py(读guanlan close·直写var·去sync)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: deprecate `sync_fincast.py` + 运维口径

**Files:**
- Modify: `scripts/sync_fincast.py`(docstring 顶部加 deprecated 注记;保留兼容不删)

- [ ] **Step 1: 实现** —— 在 `scripts/sync_fincast.py` 模块 docstring 顶部加一段 deprecated 注记(先 Read 确认 docstring 起始,把注记插在 `"""` 之后、原文之前):

```
[DEPRECATED 2026-06-23 · Spec 2] FinCast 生成已港进 guanlan,改用
scripts/fincast_predict.py(guanlan 自有·读 guanlan close·直写 var/v4_fincast_pred.parquet)。
本桥接(stocks→guanlan 搬数据)不再需要;保留仅作历史/回退兼容。
```

- [ ] **Step 2: 冒烟语法** —— `cd G:/guanlan-v2 && python -c "import ast; ast.parse(open('scripts/sync_fincast.py',encoding='utf-8').read()); print('ok')"` → `ok`。

- [ ] **Step 3: 提交**

```bash
cd /g/guanlan-v2 && git add scripts/sync_fincast.py
git commit -m "chore(fincast-port): sync_fincast 标记 deprecated(生成已港进 guanlan)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 真机集成验证(conda stocks 跑 guanlan 脚本 → regen → DL active)

**Files:** 无(验证)。**需 GPU + conda stocks + vendor 已就位(Task 1)。**

- [ ] **Step 1: 纯函数测试 + 语法** —— `cd G:/guanlan-v2 && python -m pytest tests/test_fincast_io.py -q`(6 passed)+ Task3/4 冒烟语法 ok。

- [ ] **Step 2: conda stocks 跑 guanlan 脚本**
```bash
"D:/app/miniconda/envs/stocks/python.exe" /g/guanlan-v2/scripts/fincast_predict.py --date 2026-06-22 2>&1 | tail -15
```
Expected: 加载 v1.pth → 批量推理 ~5000 只 → `已写 .../var/v4_fincast_pred.parquet`。确认 parquet:当日 ~5000 条、列 `eval_date/instrument/pred_ret_5d`、值有限。(对比当日 pred 与 Task7 stocks 脚本产出应近似/相同——同模型同权重同 close。)

- [ ] **Step 3: regen + 验 DL active**
```bash
cd /g/guanlan-v2 && python -m guanlan_v2.strategy.compute.regen 2026-06-22 > var/_regen_spec2.log 2>&1; tail -4 var/_regen_spec2.log
cat guanlan_v2/strategy/vendor/artifacts/v4_dl_provenance.json
```
Expected: `v4_dl_provenance.json` `active:true` · fincast `weight>0` · `n_has~5000` ·`lookahead:null`。

- [ ] **Step 4: 重启 9999 + live /screen DL 混合**
重启 9999(杀 9999 监听 PID 等看门狗);POST `/screen/run`(空 body)→ `v4_provenance.active:true`、`sources[].model_id=fincast` weight>0。**全程零 stocks 脚本 / 零 sync**(确认未碰 `G:/stocks/tsfm_exp/scripts/` 与 `sync_fincast.py`)。

- [ ] **Step 5: 清理** —— 删临时 `var/_regen_spec2.log`。

---

## Self-Review(已对 spec 核对)

- **Spec §4.1 vendor**:Task 1(拷贝+gitignore+setup 文档)。✓
- **Spec §4.2/§4.3 脚本+adapter**:Task 3(fincast_predict.py + 内置 FinCastAdapter + _read_close_panel)。✓
- **Spec §6 测试(纯函数)**:Task 2(build_context_matrix + write_pred_rolling·TDD 6 测)。✓
- **Spec §4.4 去 sync**:Task 4(deprecate)。✓
- **Spec §7 验证**:Task 5(conda stocks 跑脚本 → regen → DL active → live /screen)。✓
- **Spec §5 红线**:serving 零推理(GPU 只在离线脚本)、PIT close≤eval_date(build_context_matrix 截断)、契约 eval_date/instrument/pred_ret_5d(write_pred_rolling + 测)。✓
- **占位扫描**:无 TBD;纯函数(Task2)+ 脚本(Task3)给完整代码;GPU/真数据部分(close reader/adapter/regen)给集成验证步(其性质)。Task3 注明 `ffm_api.forecast` 签名以 vendored 真实 API 为准(Step1 Read 核对)——这是 GPU 黑盒的合理现场对齐点,非占位。
- **类型一致**:`build_context_matrix`(panel,eval_date,context_len,min_valid_frac)→(chosen,arr);`write_pred_rolling`(out,eval_date,chosen,preds,keep_days)→df;脚本 import 并用之,签名跨任务一致。契约列 `eval_date/instrument/pred_ret_5d` 与 Spec 1 `_load_dl_for_date` 读的一致。✓
- **范围**:仅 Spec 2(港生成栈);qlib 数据目录独立化/新 GPU 环境/Spec3 LSTM 范围外。✓
