# 交付物① · `market_factor_report@1` schema + 因子电池 v1

日期:2026-07-17 · 状态:**草案待用户审**
归属:R2 spec AMEND-1/2 的落地件;实现落 Phase 5(Bootstrap Lane 0);产出者=`market.factor`(确定性 worker),消费者=`market.regime`/`market.rotation`(LLM)+ Evaluator。
已拍板依赖:D1(60 交易日+5/20 摘要+250 分位)、D3(下游三轴概率分布)、D5(#25 miner 演化通道)。

## 0. 红线(继承 v1.1/R2,此处冻结进 schema 语义)

- LLM 不看原始标量:`market.factor` 把原料算成**带参走势向量**,LLM 只读本 report。
- 确定性:同一输入快照 + 同一电池版本 → 逐字节相同输出(全部字段进 content_digest)。
- 诚实:缺历史覆盖 → 该因子 `UNAVAILABLE`;**绝不补零、绝不拿当前快照冒充历史**;序列真实起点 `first_date` 显形。
- 电池=版本化注册表:加/改因子 = 注册表 bump(miner draft→人审),**不改架构**。

## 1. Report 信封

```python
class MarketFactorReport(DigestModel):          # market_factor_report@1
    schema_version: Literal["1"]
    as_of: UtcDateTime                          # 快照时点(收盘或盘中)
    clock_mode: Literal["eod", "intraday"]
    battery_digest: DigestHex                   # 全部 factor_id@version+params 的 canonical digest
    universe_registry_version: NonEmptyStr      # 题材/行业分类学版本(东财概念清单版本)
    factors: tuple[FactorSeries, ...]           # canonical 按 factor_id 排序
    coverage_summary: CoverageSummary           # {n_ok, n_degraded, n_unavailable}
```

## 2. 逐因子记录 `FactorSeries`

```python
class FactorPoint(ContractModel):
    date: NonEmptyStr                           # YYYY-MM-DD 交易日
    value: FiniteFloat

class FactorSeries(DigestModel):
    factor_id: NonEmptyStr                      # 如 "breadth.divergence"
    definition_version: NonEmptyStr             # "1";改定义/参数必 bump
    family: Literal["breadth","flow","rot","vol","val","temp"]
    params: <冻结参数 dict,进 digest>            # 窗口/平滑/标准化/阈值
    universe: NonEmptyStr                       # "all_a" / "concept:<ver>" ...
    frequency: Literal["daily"]                 # v1 全日频;盘中态=当日末点
    status: Literal["OK","DEGRADED","UNAVAILABLE"]
    series: tuple[FactorPoint, ...]             # ≤60 交易日(D1);UNAVAILABLE 时空
    summary: FactorSummary | None               # {latest, chg_5d, chg_20d, pct_250d}
                                                # pct_250d 覆盖不足 → None(不硬算)
    coverage: FiniteFloat                       # [0,1] 窗内有效点占比
    n_days: NonNegativeInt
    first_date: NonEmptyStr | None              # 序列真实起点(归档起点诚实显形)
    missing_policy: NonEmptyStr                 # 本因子缺料语义一句话
    available_at: UtcDateTime                   # 最晚原料可知时刻(PIT 依据)
    provenance: <sources + snapshot_refs>       # 上游数据面/快照引用
    reason: NonEmptyStr | None                  # DEGRADED/UNAVAILABLE 必填原因
```

## 3. 因子电池 v1(17 因子,全部映射到真实数据面)

> params 均为 **v1 冻结默认值**(`definition_version="1"`),标 ⏳ 的阈值待经验库成熟后走 validation 调参(TrialLedger 计账),**不拍脑袋改**。

### 广度族 breadth(5)

| factor_id | 计算 | params v1 | 源(真实产物/工具) | 历史覆盖 |
|---|---|---|---|---|
| `breadth.ad_ratio@1` | (涨−跌)/总 → MA5/MA20 + 20日斜率 | ma=[5,20], slope_win=20 | **breadth panel**(up_count/down_count,现成历史)+ market_tape 当日 | 全(panel 起点) |
| `breadth.nhnl@1` | (创20/60日新高 − 新低)/总数 | wins=[20,60] | **新计算**,原料=breadth loader 全市场 close 二进制(现成) | 全 |
| `breadth.limit_strength@1` | 涨停数 3日EMA;炸板率=zb/(zt+zb) | ema=3 | breadth panel `limit_up_total`(历史)+ market_tape zt/zb(当日起) | 涨停数全;**炸板率从快照归档起点** |
| `breadth.ladder@1` | 最高连板(limit_days 真连板口径,07-15 修复)+ 首板晋级率 | — | market_tape 连板梯队/涨停池 | **从涨停池归档起点** |
| `breadth.divergence@1` ★ | z(指数20日收益) − z(广度20日变化),z 用 250 日窗拟合(只用当点之前) | ret_win=20, z_win=250, alert=+1.5 ⏳ | 指数日线(stocks 正本)+ `ad_ratio` 序列 | 全 |

### 资金族 flow(2)

| factor_id | 计算 | params v1 | 源 | 历史覆盖 |
|---|---|---|---|---|
| `flow.northbound@1` | 5/20日累计净额 + 20日斜率 + 250日分位 | wins=[5,20], pct=250 | market_tape 北向(hgt+sgt 最新分钟和);**sgt 护栏:点密度<半数→当日置空** | 从北向留档起点 |
| `flow.main_pct@1` | 全市场主力净流入 250日百分位 | pct=250 | fundflow 大盘主力分解(沪深双 secid) | **从快照归档起点** |

### 轮动族 rot(6,含 AMEND-2 扩容 4)

| factor_id | 计算 | params v1 | 源 | 历史覆盖 |
|---|---|---|---|---|
| `rot.hhi@1` | 概念板块净流入(正部归一)HHI + top3 占比 | topk=3 | fundflow clist 双端 | 归档起点 |
| `rot.diffusion@1` | top3 净流入概念内上涨成分占比 | topk=3 | fundflow + 概念归属(ww_live_text) | 归档起点 |
| `rot.dispersion@1` | 行业日收益截面 std(fid=f3 口径,07-15 修复) | — | 行业排名 | 归档起点 |
| `rot.ladder_theme@1` | 最高板+梯队人数按题材分布 → top 题材占据度 | topk=3 | market_tape 梯队 + 涨停原因归因 | 归档起点 |
| `rot.leader_persist@1` | top3 主线领涨股 5日 identity 重合率 | win=5 | fundflow 板块领涨股 | 归档起点 |
| `rot.flow_streak@1` | top3 主线连续净流入天数 | — | fundflow 历史快照 | 归档起点 |
| `rot.theme_burst@1` | 新入 universe 题材首日:题材内涨停数/成交占比 | — | 概念 universe 版本 diff + market_tape | universe 版本化起点 |

### 波动/估值/温度(3)

| factor_id | 计算 | params v1 | 源 | 历史覆盖 |
|---|---|---|---|---|
| `vol.rv@1` | 指数 RV20 + 短长比 RV5/RV20 | wins=[5,20] | 指数日线 | 全 |
| `val.pct@1` | 指数 PE/PB 五年分位 | win=5y | baidu_valuation_percentile(B⑤ 已接) | 上游口径 |
| `temp.astock@1` | 打板温度(现成口径,**温度系数/冰点阈值 25 不动**——用户已拍板) | 上游口径 | market_tape astock_temp | 从 macro 快照起点 |

## 4. 渲染契约(`render_for_prompt`,喂 regime/rotation 的唯一出口)

- 整体为**不可信定界块**,头部声明 `as_of / clock_mode / universe_registry_version / battery_digest 前8位`。
- 每因子渲染:一行 summary(`latest | Δ5d | Δ20d | pct250`)+ 60 日紧凑序列(3 位有效数字,按周分行)。
- **UNAVAILABLE 因子必须显式一行**:`<factor_id>: UNAVAILABLE(<reason>)` ——缺席本身是信息,绝不静默省略。
- DEGRADED 标注 coverage 与 reason。
- 下游 LLM 引用规则(写进 regime/rotation skill):承重判断必须引用 `factor_id`+数值;禁止引用块外数据。

## 5. PIT 与快照归档依赖(诚实前置)

- series 每点按 `available_at` 口径可知;回测态由 PitGuard 裁,前视=`FutureDataRefused`。
- **归档依赖(新小交付,建议与事件库小 phase 同批)**:rot 族 6 因子 + 炸板率/北向/主力分位的**历史序列**依赖 market_tape/fundflow 快照落盘归档(现状 SWR 现拉不留历史;macro 快照月轮转 72573b8 已有同款先例可抄)。归档开始前这些因子短序列诚实(`first_date` 显形),**不回填、不伪造**。
- 冷启动语义:序列不足 → `summary.pct_250d=None`、`divergence` z 窗不足 → UNAVAILABLE;`market.regime` skill 的冷启动条款(AMEND-4)据此写。

## 6. 演化通道(与 #25/#26 对齐)

- 调参:经验库成熟 realized → validation IC/命中率 → Evaluator 调参 → **新 definition_version** → 人审上线;sealed holdout 不参与。
- 加因子:`market.factor_miner`(#25)draft → 人审 → 注册表 bump(battery_digest 变);修订节流 N=3 期成熟观察(D6)。
- universe 演化:新题材进东财概念清单 = `universe_registry_version` bump,泛型因子自动纳入,**不需要新因子**。

## 7. 留给实现期的三个已知项

1. market_tape/fundflow 快照归档小交付(§5,rot 族历史序列前置)。
2. `breadth.nhnl` 为唯一全新计算(原料现成,~一个函数)。
3. `divergence.alert=+1.5` ⏳ 为 v1 冻结默认,首批经验库成熟后第一个进 validation。
