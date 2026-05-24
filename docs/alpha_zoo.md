# Alpha Zoo (v1.3.0)

A registry of named alpha formulas with a `alpha bench` CLI that emits
IC / IR / hit-rate per alpha against a chosen universe and period.

Two families ship in v1.3.0:

| Family | Source | Count (v1.3.0) | Designed for |
|---|---|---|---|
| `alpha101` | Kakushadze 2015 (arXiv:1601.00991) | 10 / 101 | US equities, generalises to A-share |
| `gtja191` | Guotai Junan Securities 2017 | 12 / 191 | A-share short-horizon (rev_5 / fwd_5d) |

Remaining alphas land in later patch releases. `qlib158` and `academic`
are placeholders for future work.

## CLI

### List all registered alphas

```bash
financial-analyst alpha list           # all families
financial-analyst alpha list gtja191   # one family
```

### Inspect one alpha's formula

```bash
financial-analyst alpha show alpha001
financial-analyst alpha show gtja003
```

Output:
```
alpha001  (alpha101)
Kakushadze 2015 — 101 Formulaic Alphas (arXiv:1601.00991)

Description: Recency of vol shock vs close — captures recent risk-on / risk-off pivots

Formula:
  rank(Ts_ArgMax(SignedPower(((returns<0)?stddev(returns,20):close),2.),5))-0.5
```

### Bench against a universe

```bash
# Default sample30 universe (30 A-share large caps shipped with the package)
financial-analyst alpha bench gtja191 \
    --universe sample30 --since 2024-06-01 --until 2024-12-31

# Your own universe file (one code per line, # for comments)
financial-analyst alpha bench --universe ~/.financial-analyst/universes/csi300.txt

# Different forward horizon
financial-analyst alpha bench --universe sample30 --fwd-days 20
```

Output is sorted by `|rank_IR|` descending (strongest signals first):

```
                   Alpha Bench — gtja191 / fwd_5d / 30 codes
┌─────────┬─────────┬─────────┬─────────┬────────┬─────────┬─────────┬────────┐
│ name    │ family  │   ic    │ rank_ic │   ir   │ rank_ir │ hit_ra… │ n_dat… │
├─────────┼─────────┼─────────┼─────────┼────────┼─────────┼─────────┼────────┤
│ gtja001 │ gtja191 │ -0.0439 │ -0.0434 │ -0.225 │  -0.225 │   48.4% │    133 │
│ gtja014 │ gtja191 │ +0.0331 │ +0.0525 │ +0.177 │  +0.201 │   51.8% │    134 │
│ gtja005 │ gtja191 │ +0.0251 │ +0.0471 │ +0.112 │  +0.190 │   51.3% │    129 │
│ ...                                                                          │
└─────────┴─────────┴─────────┴─────────┴────────┴─────────┴─────────┴────────┘
```

Negative IC = the alpha predicts reversal (still useful; just sign-flip
when using it in a model).

## Adding your own alpha

```python
# memories or plugins/my_alpha.py
from financial_analyst.factors.zoo.registry import AlphaSpec, register
from financial_analyst.factors.zoo.operators import rank, delta, correlation

def _my_alpha(p):
    return -1 * correlation(rank(p.close), rank(p.volume), 10)

register(AlphaSpec(
    name="my_alpha",
    family="custom",
    description="Negative 10-day rank-corr of close vs volume",
    formula_text="-1 * correlation(rank(CLOSE), rank(VOLUME), 10)",
    compute=_my_alpha,
))
```

Drop the file in `~/.financial-analyst/plugins/` and it loads on CLI
start via the existing `plugins.yaml` mechanism.

## Operator reference

All operators are in `financial_analyst.factors.zoo.operators`. They
consume and return `pd.Series` indexed by MultiIndex `(datetime, code)`.

### Cross-sectional
- `rank(x)` — cross-sectional percentile rank per date, in [0, 1]
- `scale(x, a=1.0)` — scale so sum(|x|) == a per date
- `indneutralize(x, group)` — demean x within group per date

### Time-series (per code, no cross-stock bleed)
- `ts_sum(x, n)`, `ts_mean(x, n)`, `stddev(x, n)`
- `ts_max(x, n)`, `ts_min(x, n)`, `ts_argmax(x, n)`, `ts_argmin(x, n)`
- `ts_rank(x, n)` — rank of latest value in last n bars
- `delta(x, n)` — x_t - x_{t-n}
- `delay(x, n)` — x_{t-n}
- `correlation(x, y, n)`, `covariance(x, y, n)` — rolling per code
- `decay_linear(x, n)` — linear-weighted MA, latest gets max weight
- `sma(x, n, m=1)` — GTJA-style geometric SMA, recursive EWMA-like

### Elementwise
- `signedpower(x, p)` — `sign(x) * |x|^p`
- `log(x)`, `sign(x)`, `abs_(x)`, `product(x, n)`, `power(x, p)`

Every `ts_*` operator uses `min_periods=window` — rows before the window
fills out are NaN, so alphas never emit a partial-window signal.

## Universes

### Built-in
- `sample30` — 30 hand-picked A-share large caps (shipped with the
  package under `config/universes/sample30.txt`)

### User-supplied
Drop a `<name>.txt` file under either:
- `~/.financial-analyst/universes/<name>.txt` (user-private, preferred)
- `config/universes/<name>.txt` (repo-local, version-controlled)

Format: one stock code per line, `#` starts a comment, blank lines
ignored. Codes use the Qlib convention `SH600519` / `SZ000858`.

### Generating CSI 300 / CSI 500
```bash
# From G:/stocks repo if available
python -c "
import pandas as pd
df = pd.read_csv('G:/stocks/stock_pool/csi300.csv')
codes = df['code'].tolist()
with open(f'{__import__(\"os\").path.expanduser(\"~\")}/.financial-analyst/universes/csi300.txt', 'w') as f:
    for c in codes: f.write(c + '\n')
"
```

## How the bench loop works

```
For each requested alpha A:
    series = A(panel)                       # (datetime, code) -> alpha value
    fwd = log(close_{t+n} / close_t)        # forward n-day log return
    for each date d:
        ic_d = corr(series_at_d, fwd_at_d)
    ic = mean(ic_d)
    ir = ic / std(ic_d)
    rank_ic / rank_ir: same with Spearman (rank-corr)
    hit_rate: cells where sign(alpha-cs_mean) == sign(fwd-cs_mean)
```

IC mean tells you direction-and-magnitude; IR tells you reliability;
hit rate tells you the simple "calls forward direction correctly" rate
(should be > 50% for a useful bullish alpha, < 50% for a contrarian
one).

## Known limitations (v1.3.0)

- **Subset of papers**: 10 + 12 alphas out of 101 + 191. Patch releases
  fill out the catalogue.
- **No industry-neutralisation**: `indneutralize` op exists but isn't
  used by shipped alphas yet (waiting on an industry classifier loader).
- **Single-frequency only**: panel is daily bars. Adding 5min support is
  a future PanelData extension.
- **No look-ahead protection on user alphas**: registering a user alpha
  doesn't gate it against using `close.shift(-1)` etc. The shipped 22
  are clean; review carefully if you add your own.

