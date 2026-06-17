# guanlan_v2.factorlib — 因子库(在仓自有后端)

guanlan 自有因子库,随 `cards` / `seats` 先例挂在薄壳 `create_app` 上(**不改 `engine/`、不碰 `fa-watch-wt`、不改 `G:/stocks`**)。
启动时把 `base/` + `mined/` 因子(引擎 **zoo-DSL** 表达式)经引擎 primitive 编译,注册进**引擎运行期 zoo registry**(进程级全局 dict),
使其立即出现在引擎 `/factor/list` 的 `registered`;并暴露 `/factorlib/*` 自有端点。

数据只经引擎 `get_data_paths` / `loaders.yaml`(本机 qlib bin):求值时引擎 `PanelData` 才碰数据,本层只持有 JSON 表达式串。

## 模块

| 文件 | 作用 |
|---|---|
| `__init__.py` | 导出 `register_library_factors()`(启动注册)、`build_factorlib_router()`、`LibraryFactorStore` |
| `qlib_to_zoo.py` | **Qlib-DSL → 引擎 zoo-DSL 确定性译写器**;译不动的抛 `UnsupportedFactor`(诚实失败) |
| `store.py` | `LibraryFactorStore`:读 `base/*.json`+`mined/*.json` → `validate_expr`+`compile_factor` → 注册进 zoo registry |
| `api.py` | `build_factorlib_router()`(工厂式,`APIRouter(prefix="/factorlib")`) |
| `base/*.json` | 迁移来的**基础因子**(zoo-DSL + 元数据) |
| `mined/*.json` | **自挖落点**(占位;与引擎 `UserFactorStore` 衔接) |

## Qlib → zoo 译写规则(`qlib_to_zoo.py`)

stocks 挖掘产物是 **Qlib 语法**,引擎 zoo 是**另一套**,二者不兼容。译写器做确定性变换:

- 字段去 `$`:`$close` → `close`、`$turnover_rate` → `turnover_rate`。
- 函数整词换名:`Ref→delay`、`Std→stddev`、`Mean→ts_mean`、`Sum→ts_sum`、`Corr→correlation`、`Cov→covariance`、`Max→ts_max`、`Min→ts_min`、`Delta→delta`、`Abs→abs_`、`Log→log`、`Sign→sign`、`Power→power`、`WMA→wma`、`Rank→ts_rank`。
- **拒绝(诚实失败,记台账、跳过)**:`If(` 三目、`Slope(`、`EMA(`、`Quantile(`、`Med(`、`Rsquare(`、`IdxMax(`/`IdxMin(` —— zoo `expr.py` 受限命名空间无安全对应物,不猜译。

## 迁移台账(每因子来源)

源:`G:/stocks/results/factor_mining/rolling_top30_factors.txt`(滚动验证 top30,按 20d |ICIR| 选)。
原始 30 条 → 译写校验后 **22 条可编译**(0 fail),其中 **19 条**收入本库(去重后),**8 条诚实跳过**(7×`If` + 1×`Slope`)。
全部经真实引擎 `compile_factor` + 全栈 `TestClient` 验证:`registered=19 / total=19 / skipped=0`,`/factor/list` registered 由 442 → **461**。

### base/(18 条,family=`library`)

**quality_reversal.json — 质量反转族**

| name | qlib 源(rolling_top30) | zoo expr |
|---|---|---|
| `lib_quality_v2` | `Ref($close,20)/$close-1-Std($close/Ref($close,1)-1,20)*10-Std($turnover_rate,20)/(Mean($turnover_rate,20)+1e-8)` | `delay(close,20)/close-1-stddev(close/delay(close,1)-1,20)*10-stddev(turnover_rate,20)/(ts_mean(turnover_rate,20)+1e-8)` |
| `lib_quality_10d` | `Ref($close,10)/$close-1-Std(...,10)*10-Mean($turnover_rate,10)` | `delay(close,10)/close-1-stddev(...,10)*10-ts_mean(turnover_rate,10)` |
| `lib_quality_40d` | `…,40…Mean($turnover_rate,40)` | `…,40…ts_mean(turnover_rate,40)` |
| `lib_quality_60d` | `…,60…Mean($turnover_rate,60)` | `…,60…ts_mean(turnover_rate,60)` |
| `lib_quality_4factor` | `…-Mean($turnover_rate,20)-Corr($close,$volume,20)` | `…-ts_mean(turnover_rate,20)-correlation(close,volume,20)` |
| `lib_contrarian_sentiment` | `(Ref($close,20)/$close-1)*(1-Mean($volume,5)/(Mean($volume,60)+1e-8))` | `(delay(close,20)/close-1)*(1-ts_mean(volume,5)/(ts_mean(volume,60)+1e-8))` |

**liquidity_turnover.json — 流动性 / 换手族**

| name | qlib 源 | zoo expr |
|---|---|---|
| `lib_turnover_cv20` | `Std($turnover_rate,20)/(Mean($turnover_rate,20)+1e-8)` | `stddev(turnover_rate,20)/(ts_mean(turnover_rate,20)+1e-8)` |
| `lib_turnover_jump_ratio` | `$turnover_rate/(Mean($turnover_rate,20)+1e-8)` | `turnover_rate/(ts_mean(turnover_rate,20)+1e-8)` |
| `lib_amount_surge_3d_20d` | `Mean($amount,3)/(Mean($amount,20)+1e-8)` | `ts_mean(amount,3)/(ts_mean(amount,20)+1e-8)` |
| `lib_amount_share_5d_60d` | `Sum($amount,5)/(Sum($amount,60)+1e-8)` | `ts_sum(amount,5)/(ts_sum(amount,60)+1e-8)` |
| `lib_amt_concentration_5v20` | `Sum($amount,5)/(Sum($amount,20)+1e-8)` | `ts_sum(amount,5)/(ts_sum(amount,20)+1e-8)` |
| `lib_amt_weighted_ret20` | `Mean($amount*($close/$open-1),20)/(Mean($amount,20)+1e-8)` | `ts_mean(amount*(close/open-1),20)/(ts_mean(amount,20)+1e-8)` |

**volatility_distress.json — 波动 / 困境族**

| name | qlib 源 | zoo expr |
|---|---|---|
| `lib_vol_up_risk_appetite` | `Std(...,20) * ($close/Ref($close,20)-1)` | `stddev(...,20) * (close/delay(close,20)-1)` |
| `lib_distress_highvol_lowamt` | `Std(...,20)/(Mean($amount,20)+1e-8)*1e9` | `stddev(...,20)/(ts_mean(amount,20)+1e-8)*1e9` |
| `lib_distress_drop_highturn` | `(Ref($close,10)/$close-1)*Mean($turnover_rate,5)/(Mean($turnover_rate,20)+1e-8)` | `(delay(close,10)/close-1)*ts_mean(turnover_rate,5)/(ts_mean(turnover_rate,20)+1e-8)` |
| `lib_beta_volume_cross` | `Std(...,60) * (Mean($volume,5)/(Mean($volume,20)+1e-8))` | `stddev(...,60) * (ts_mean(volume,5)/(ts_mean(volume,20)+1e-8))` |
| `lib_close_vwap_dev` | `Mean($close/$vwap-1,20)` | `ts_mean(close/vwap-1,20)` |
| `lib_ma_dev_composite` | `($close/Mean($close,5)-1+…+$close/Mean($close,60)-1)/4` | `(close/ts_mean(close,5)-1+…+close/ts_mean(close,60)-1)/4` |

### mined/(1 条占位,family=`library_mined`)

| name | qlib 源 | zoo expr |
|---|---|---|
| `mined_distress_combined` | `(Ref($close,10)/$close-1)*Mean($turnover_rate,5)/(Mean($turnover_rate,20)+1e-8)+Std(...,20)/(Mean($amount,20)+1e-8)*1e9` | `(delay(close,10)/close-1)*ts_mean(turnover_rate,5)/(ts_mean(turnover_rate,20)+1e-8)+stddev(...,20)/(ts_mean(amount,20)+1e-8)*1e9` |

### base/ta_indicators.json(TA 指标族,family=`ta`)

技术指标用引擎算子重建(**非引擎缺能力,只是先前没写进库**):`sma(x,n,m)` = GTJA 递归 EMA(α=m/n,P 日 EMA=`sma(x,P+1,2)`),配合 `ts_min`/`ts_max`/`stddev`/`delta`/`max_pair`/`cross`。
**入库门禁**:每条经 `scripts/verify_ta_indicators.py` POST `/factor/report` 实测 `status=ok` 才收(本族 20 条全 **20 ok / 0 bad**);台账见该脚本输出。

| 族 | 条目 |
|---|---|
| MACD | `ta_macd_dif` `ta_macd_dea` `ta_macd_hist` `ta_macd_golden_cross` `ta_macd_dead_cross` |
| RSI | `ta_rsi6` `ta_rsi12` `ta_rsi14` `ta_rsi24` |
| KDJ | `ta_kdj_k` `ta_kdj_d` `ta_kdj_j` `ta_kdj_golden_cross` |
| BOLL | `ta_boll_pctb` `ta_boll_bandwidth` `ta_boll_upper_break` |
| 其他 | `ta_wr14` `ta_bias20` `ta_roc20` `ta_atr14` |

**真缺口(未收,缺底层原语)**:`OBV`(expanding cumsum)、`CCI`(平均绝对偏差)、`SAR`(抛物线递归)。
**消费**:`register_all()` 启动注册进引擎 zoo(启动日志 `registered 39/39`,含本族 20);并由 `guanlan_v2/cards/refine.py` 读取,把范例注入「炼」的 system prompt(`/cards/refine` 据此对 MACD 写出 `sma` 重建式、对 OBV 诚实留空)。

### 诚实跳过(8 条,未收入;不可安全译写)

`If` 三目(7):`turnover_cv_extreme`、`extreme_vol_freq20`、`dist_rally_exhaustion`、`big_yang_freq_10d`、`dist_extreme_turnover_count`、`asym_rev_up20_fix`、`breakout_vol_confirm`。
`Slope`(1):`turnover_slope_20`。
> 这些含三目条件 / 线性斜率,zoo `expr.py` 受限命名空间无安全对应物。后续若引擎 `expr` 暴露 `filter_where`/`regbeta` 等价封装,可补译收入。

## REST 端点(`/factorlib/*`)

| 方法 路径 | 说明 |
|---|---|
| `GET /factorlib/list?validate=true` | 库内因子清单:`{ok,count,factors:[{name,expr,family,source,origin,description,qlib_src?,valid,reason?}]}` |
| `GET /factorlib/registered` | 当前 zoo registry 里属本库(family `library*`)的已注册项:`{ok,count,registered:[{name,family,formula}]}` |
| `POST /factorlib/validate` | 校验任意表达式(借引擎 primitive);`{expr,is_qlib}` → `{ok,zoo_expr,reason?}` |

## 注册方式(进引擎运行期 zoo registry,不改 engine/)

`register_library_factors()`(server `create_app` 在 seats 之后调用)对每条库因子:

1. `import financial_analyst.factors.zoo` 触发引擎内置三族(alpha101/gtja191/qlib158)注册;
2. `validate_expr(zoo_expr)` + `compile_factor(zoo_expr)` 编译成 `PanelData→Series`;
3. `unregister(name)` → `register(AlphaSpec(name, family="library", formula_text, compute))`
   —— 与引擎 `UserFactorStore.register_one` 同 replace 范式(重编译 compute 是新 fn,避免 frozen-collision raise)。

幂等、不崩:单条失败只记台账(`ledger`)、`skipped+1`,不影响其余、不阻断启动。
库因子与引擎 `/factor/save` 写入的 `user` 库**互不干扰**(本库 family=`library`/`library_mined`,user 库 family=`user`),均出现在 `/factor/list`。
