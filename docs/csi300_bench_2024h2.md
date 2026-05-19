# CSI300 Alpha Bench Report — 2024-H2

A full-zoo benchmark over the **868-stock active CSI300 universe** for
**2024-06-01 to 2024-12-31** (144 trading days), forward 5-day returns.

> Data: 142 alphas × 868 stocks × 144 days = 17.7M cells per alpha.
> Wall-clock: panel build 68s + bench 95s = **2m43s** on commodity laptop.
> CSV: [`out/csi300_2024h2_bench.csv`](../out/csi300_2024h2_bench.csv)

---

## 1. Headline — qlib158 dominates

| Family | Count | Mean \|rank_IR\| | Median \|rank_IR\| | Max \|rank_IR\| | % alphas with \|rank_IR\|>0.25 |
|--------|------:|--------:|---------:|--------:|---:|
| **qlib158** | 56 | **0.181** | **0.157** | **0.542** | **30.4%** |
| gtja191 | 44 | 0.154 | 0.139 | 0.430 | 20.5% |
| alpha101 | 42 | 0.121 | 0.108 | 0.408 | 11.9% |

qlib158's simple OHLC ratios + volume statistics outperform both the
WorldQuant 101 and GTJA-191 catalogues on A-share large caps. The
intuition: alpha101 was designed on US equities; gtja191's most-cited
formulas are short-horizon specific; qlib158 is structurally biased
toward stable, low-noise inputs that survive cross-universe shifts.

## 2. Top 15 alphas on CSI300

| # | name | family | rank_IC | rank_IR | hit_rate | n_dates |
|--:|------|--------|--------:|--------:|--------:|--:|
| 1 | **qlib_VSTD60** | qlib158 | +0.055 | **+0.542** | 51.7% | 80 |
| 2 | gtja095 | gtja191 | -0.080 | -0.430 | 50.2% | 120 |
| 3 | qlib_STD10 | qlib158 | -0.082 | -0.416 | 48.4% | 130 |
| 4 | gtja052 | gtja191 | -0.058 | -0.409 | 48.4% | 113 |
| 5 | gtja042 ≡ alpha040 | gtja191 / alpha101 | +0.065 | +0.408 | 52.5% | 129 |
| 6 | qlib_VSUMP20 | qlib158 | -0.046 | -0.404 | 49.1% | 119 |
| 7 | qlib_STD5 | qlib158 | -0.070 | -0.395 | 48.9% | 135 |
| 8 | qlib_KLEN | qlib158 | -0.062 | -0.352 | 49.1% | 139 |
| 9 | qlib_VMA20 | qlib158 | +0.038 | +0.342 | 50.7% | 120 |
| 10 | qlib_CNTP60 | qlib158 | -0.045 | -0.338 | 49.4% | 80 |
| 11 | qlib_IMAX20 | qlib158 | -0.044 | -0.335 | 50.0% | 120 |
| 12 | qlib_ROC60 | qlib158 | +0.078 | +0.331 | 51.6% | 79 |
| 13 | alpha007 | alpha101 | +0.044 | +0.324 | 29.8% | 73 |
| 14 | qlib_STD20 | qlib158 | -0.063 | -0.321 | 49.0% | 120 |
| 15 | qlib_BETA20 | qlib158 | -0.052 | -0.318 | 48.9% | 120 |

(`alpha007` has hit_rate=29.8% with only 73 dates — large rank_IR magnitude
but low-confidence; flagged for review.)

## 3. Theme: **volatility wins on CSI300 in 2024-H2**

Five of the top 8 are volatility / dispersion measures:

* `qlib_VSTD60` — 60d volume stddev / current volume (**+** sign means
  high vol-vol predicts forward gain)
* `gtja095` — 20d stddev of dollar volume (**−** sign means high
  turnover-vol predicts reversal)
* `qlib_STD10 / STD5 / STD20` — close-price stddev / close (**−** sign:
  high price vol = reversion candidate)
* `qlib_KLEN` — daily range / open (**−** sign: wide-range days revert)

The mixed signs are not a contradiction: volume-stddev alphas predict
**continuation** of the volume regime, while price-stddev alphas predict
**reversal** of the price move. Both are coherent: volatility *of trading*
is a regime variable, volatility *of price* is a mean-reversion gauge.

## 4. Sample30 overfit alert

Every alpha in the prior sample30 top-7 lost substantial signal on
CSI300. Reliability rule-of-thumb: **trust nothing tested on <100 stocks**.

| Alpha | sample30 rank_IR | csi300 rank_IR | Δ |
|-------|-----------------:|---------------:|---:|
| qlib_CNTN60 | +0.531 | +0.100 | **−81%** |
| qlib_RESI60 | +0.278 | -0.061 | **sign flip** |
| gtja076 | -0.330 | -0.091 | **−72%** |
| qlib_RSQR60 | -0.508 | -0.172 | **−66%** |
| qlib_CNTP60 | -0.605 | -0.338 | -44% |
| qlib_ROC60 | +0.592 | +0.331 | -44% |
| qlib_BETA60 | -0.431 | -0.261 | **-39%** (most resilient) |

`qlib_BETA60` is the only sample30 leader that retained meaningful
signal. The CNTN60/RSQR60/RESI60 trio collapsed — suggests the
"high-trend-linearity → reversal" finding was a 30-stock artifact, not
a market property.

## 5. Hit-rate distribution

Hit rate measures the fraction of (date, code) cells where the alpha's
sign matches the forward return's sign (after cross-sectional demean).

```
hit < 45%         3 alphas  (strong contrarian — sign-flip candidates)
45-48%            2
48-49%           14
49-50%           49
50-51%           53
51-52%           14
52-55%            4
> 55%             0
```

Density is tight around 50% as expected for noisy short-horizon
prediction. **18 alphas (12.7%)** beat 51% hit rate, **19 alphas (13.4%)**
fall below 49% (useful as inverted signals).

## 6. Bottom 10 — sign agnostic to forward direction

These alphas are computationally fine but predictively flat on CSI300:

```
gtja008      rank_ir=+0.005  (was sample30 #2! collapsed to noise on 868 stocks)
qlib_KSFT    rank_ir=+0.006
qlib_CNTN20  rank_ir=+0.009
alpha054     rank_ir=+0.010
alpha055     rank_ir=-0.012
alpha041 ≡ gtja013  rank_ir=+0.013  (formula-identical; expected to track)
alpha025     rank_ir=-0.014
gtja002      rank_ir=-0.015
alpha053     rank_ir=+0.020
```

Note `alpha041` and `gtja013` are textually identical (`sqrt(high*low) -
vwap`); their bench rows match to 6 decimals, confirming the registry
isn't double-counting.

## 7. v1.3.3 regression-operator audit

The 18 alphas using the new `regbeta` / `regresi` / `rsqr` operators:

```
gtja095     -0.430  ★ top 2 overall
qlib_BETA20 -0.318
gtja160     -0.291
gtja027     -0.268
qlib_BETA60 -0.261
gtja128     -0.254
qlib_RSQR10 -0.225
...
qlib_RESI{5,10,60}  all in (-0.06, +0.04)  ← residuals add little
```

`regbeta` paid off (BETA10/20/60 all strong); `rsqr` weaker (R² magnitude
doesn't carry as much info as the slope itself); `regresi` borderline
useless on CSI300 (residuals at 5/10/60d are noise). The operators were
worth implementing — `gtja095` alone is now zoo #2.

## 8. Recommended top-10 alphas for production use

After dropping sample30-overfit candidates and small-`n_dates` outliers,
the alphas most likely to survive on out-of-sample data:

```
1. gtja042 ≡ alpha040  +0.408   (10d vol-of-high × high-vol corr)
2. qlib_VSTD60         +0.542   (60d volume volatility)
3. gtja095             -0.430   (20d stddev of dollar volume)
4. qlib_STD10          -0.416   (10d close stddev / close)
5. gtja052             -0.409   (26d wick ratio)
6. qlib_VSUMP20        -0.404   (volume-direction fraction)
7. qlib_KLEN           -0.352   (daily range / open)
8. qlib_BETA20         -0.318   (20d close-vs-time slope)
9. qlib_ROC60          +0.331   (60d close ratio)
10. qlib_IMAX20        -0.335   (high-recency position)
```

The 5 with high \|rank_IR\| **and** hit_rate > 50% (alphas in #1, #5
through #15) are the strongest production candidates because they're
both magnitude-significant and directionally accurate.

## 9. Next steps for v1.3.x

1. **Run a half-year forward bench** (2025-Q1, 2025-Q2 once data
   available) to confirm the volatility-theme finding holds out of
   sample.
2. **Industry-neutralise the volatility alphas** — VSTD60 and STD10
   may carry industry exposure (utilities have structurally low vol);
   `indneutralize` once we have the industry classifier loader.
3. **Drop or sign-flip the noise alphas** at the bottom — gtja008,
   alpha054/055, alpha041≡gtja013 add no value at current sample size.
4. **Bench longer horizons**: `--fwd-days 20` to see which alphas
   retain signal at medium-term holding periods.

---

*Bench reproducibility*: re-run `financial-analyst alpha bench
--universe csi300_active --since 2024-06-01 --until 2024-12-31 --top 30`.
The active CSI300 universe file is checked in under
[`config/universes/csi300_active.txt`](../config/universes/csi300_active.txt)
(868 codes, sourced from `G:/stocks/stock_data/cn_data/instruments/csi300.txt`
filtered to "active before 2024-06-01 AND still listed at 2026-04-23").
