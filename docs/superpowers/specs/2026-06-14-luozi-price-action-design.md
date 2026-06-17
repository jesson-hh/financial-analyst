# 落子 · 价格行为(价量几何特征 + 方法论 prompt)设计

> 借鉴 PA_Agent(AGPL,**仅借思路 clean-room 重写,不复制其代码**),为落子补两层能力:确定性的价量几何特征 + 可开关的价格行为方法论 prompt。

**Goal:** 给落子每个 agent(策略实例)装上「价量几何特征」这层确定性真特征(替掉 scanSeat 的 MA/量比拍脑袋骨架、并喂进 LLM 研判),以及「价格行为方法论」这段可编辑、可开关的推理框架 prompt。

**Architecture:** 后端 `seats/price_action.py` 纯函数从 OHLC 算几何 + 渲染 prompt 块(供 decide LLM、随响应回前端);前端 `luozi-data.jsx` 一套 JS 镜像同一特征定义(供 scanSeat 启发式 + 决策卡显示);校场每策略一个开关 + 可编辑方法论文本。几何特征**始终计算并显示**(标「确定性·非LLM」);开关只决定是否把几何块 + 方法论注入 LLM 研判 prompt。

**Tech Stack:** Python(FastAPI,纯函数 + pytest TDD)/ React UMD(no-build,in-browser babel,`?v=` 版本)/ 引擎 `fetch_quote` 日线·5min,`_agg_5min_to_30min` 30min。

---

## 1. 背景与动机

- **审计结论**(见 memory `luozi-fake-audit`):落子后端 100% 真接入;前端唯一假料根因 = `evidenceFor()` 合成料;**scanSeat 是最薄的一环**——只用 MA5/MA20/量比/5日收益的拍脑袋规则,证据还是合成的。
- **PA_Agent 借鉴**:它把 K 线几何特征(实体比/影线/内含外包/突破/跟随…)本地确定性算出、以**数字**而非截图喂 LLM,两阶段诊断→决策。这套「确定性几何特征层」正好补 scanSeat 的缺口,且完全符合本仓「真接入零假料」红线(几何是算出来的事实,不是合成)。
- **许可证**:PA_Agent 是 AGPL-3.0(强传染 + 网络条款)。**本设计 clean-room 重写**:几何公式是公知技术分析数学(不受版权保护),特征选择/命名/A股 适配为本仓自有,**绝不复制 PA_Agent 源码**。

## 2. 范围与已对齐决策

用户三项决策(已对齐):

1. **方法论范围 = 每策略可编辑文本**:默认给一段 A股 适配模板,每个策略可在校场里像 `creed` 一样自由编辑。
2. **开关粒度 = 几何常显·方法论开关**:几何特征始终计算、决策卡始终显示(标确定性·非LLM)、始终喂 scanSeat;校场开关只控制「是否把方法论读法 + 几何块注入 LLM 研判 prompt」。
3. **默认与迁移 = 默认关·opt-in·存量不变**:新策略 `pa` 默认 false,不改变现有任何行为;用户在校场手动开。

**复用范围 = 落子专用(方案 A)**:几何特征作落子的确定性特征,**不**在本期升格为引擎因子库一等公民(那是更大的工程,留作后续 §13)。后端纯函数落 `guanlan_v2/seats/price_action.py`(落子作用域,日后可提升)。

**明确不做(YAGNI):** PA_Agent 式「诊断→按形态路由到多个 setup 文件」(外汇主观打法、A股 适配差、且大量重复已有 `claim_audit`);几何特征注册成引擎因子 / 算 IC / 进选股·工作流。

## 3. 几何特征集(clean-room · A股 适配)

后端 `compute_pa_features` 与前端 `paFeatures` **算同一套**(契约见 §11)。针对序列**最新一根**(决策 bar)计算,附最近 3 根的 `bar_type`。所有特征只用 **≤决策 bar** 的数据(PIT 红线,§11)。

记 `rng = high - low`,`prev_close` = 前一根 close。

| 键 | 中文 | 公式 / 口径 | 类型 | 不足/边界 |
|---|---|---|---|---|
| `body` | 实体比 | `abs(close-open)/rng` | float | rng≤0 → None |
| `upper_wick` | 上影比 | `(high-max(open,close))/rng` | float | rng≤0 → None |
| `lower_wick` | 下影比 | `(min(open,close)-low)/rng` | float | rng≤0 → None |
| `close_pos` | 收盘位 | `(close-low)/rng`(0=收下沿,1=上沿) | float | rng≤0 → None |
| `range_atr` | 振幅/ATR | `rng / ATR14` | float | <15 根 → None |
| `ema20_rel` | 距EMA20 | `(close-EMA20)/EMA20` | float | <20 根 → None |
| `bar_type` | K线型态 | 见下「分类」 | str | rng≤0 → `平` |
| `breakout` | 突破 | `high>max(前5根high)`→`突破前5高`;`low<min(前5根low)`→`跌破前5低`;否则`区间内`(两者皆中按 close 方向取) | str | <5 根 → None |
| `inside_streak` | 连续内含 | 自最新根向前数连续「内含bar」的根数 | int | 无 prev → 0 |
| `vol_ratio` | 量比 | `vol / mean(前5根 vol)` | float | <5 根或均量0 → None |
| `limit` | 涨跌停 | `pct=(close-prev_close)/prev_close`;按板幅 L 判:见下「涨跌停」 | str | 无 prev → None |
| `gap` | 跳空 | `open>prev_close*1.002`→`高开`;`<*0.998`→`低开`;否则`无` | str | 无 prev → None |
| `follow` | 跟随确认 | 前一根若强势,本根是否同向跟随:见下「跟随」 | str/None | 无 prev/前根非强势 → None |
| `recent` | 近3根型态 | `[bar_type(t-1), bar_type(t-2), bar_type(t-3)]`(各按上表分类;不足补 None) | list[str/None] | — |

**ATR14**:`TR = max(high-low, abs(high-prev_close), abs(low-prev_close))`,取最近 14 个 TR 的简单均值(需 ≥15 根:14 TR + 首根 prev_close)。
**EMA20**:close 的指数移动平均,span=20,需 ≥20 根方稳定,否则 None(诚实)。
> 实现注:引擎 `factors/zoo/operators.py` 有 EMA/ATR,但那是面板/groupby 表达式算子(耦合引擎因子框架)。本函数刻意自带极简 pandas 实现(`close.ewm(span=20).mean()` 等)以**保持纯函数、零 I/O、可直接 pytest 喂构造 df**,不引入引擎依赖。

**bar_type 分类(优先级从上到下):**
1. `rng<=0` → `平`
2. 有 prev 且 `high<=prev_high and low>=prev_low` → `内含bar`
3. 有 prev 且 `high>=prev_high and low<=prev_low`(严格包住) → `外包阳`(close≥open)/`外包阴`(close<open)
4. `body<0.1` → `十字`
5. `body>=0.5` → `趋势阳`(close>open)/`趋势阴`(close<open)
6. 其余 → `小阳`(close≥open)/`小阴`(close<open)

**涨跌停 limit**:板幅 L 由 code/name 决定——name 含 `ST`(含 `*ST`)→ 0.05;`688`(科创)/`300`(创业)前缀 → 0.20;`8`/`4` 前缀或 `BJ`(北交所)→ 0.30;否则 0.10。判:`pct≥L-0.003`→`涨停`;`pct≥0.7L`→`接近涨停`;`pct≤-(L-0.003)`→`跌停`;`pct≤-0.7L`→`接近跌停`;否则`正常`。(code 取归一后 `SH600519` 的数字核;name 来自 payload。)

**跟随 follow**(向后看·PIT 安全,只用 prev 与 current 两根已收 bar):
- 前一根 `趋势阳/外包阳` 且 `current.close>prev.close 且 current.close>current.open` → `已确认(多)`
- 前一根 `趋势阴/外包阴` 且 `current.close<prev.close` → `已确认(空)`
- 前一根强势但本根反向(阳后收阴破前低 / 阴后收阳破前高) → `转弱`
- 其余 → None

**30 分钟版**:同一函数算在 30min df 上,公式不变(rev/ATR/EMA 等口径自然变成「N 根 30min bar」);渲染时 `unit="根30分钟bar"`(沿用既有 §5 `render_factors` 口径)防 LLM 误读为「N 日」。

## 4. 后端:`guanlan_v2/seats/price_action.py`(纯函数 · TDD)

```python
def compute_pa_features(df, code: str = "", name: str = "") -> dict:
    """从 OHLC DataFrame(列:trade_date/open/high/low/close/vol[/amount],已 PIT≤asof,
    时间升序)算最新一根的价量几何特征(§3)。空/列缺 → {}。不足窗口的项诚实 None。
    code/name 仅用于涨跌停板幅判定(科创/创业/北交所/ST)。**纯函数,无 I/O。**"""

def render_pa_block(feat: dict, unit: str = "日") -> str:
    """把 feat 渲染成 prompt 文本块(§5 格式)。None → 「—」,绝不补值。feat 为空 → 空串。"""

PA_METHOD_DEFAULT: str = "...(§7 全文)..."
```

- 与引擎/qlib 解耦:只吃 DataFrame,不 import loader。便于 pytest 直接喂构造 df。
- 数值四舍五入 3 位(与既有口径一致);分类返回中文枚举字符串。

## 5. decide 接线(`guanlan_v2/seats/api.py`)

**payload 新增**(其余字段不变):
- `pa`:bool,默认 false——是否把几何块 + 方法论注入 LLM prompt。
- `pa_method`:str,默认 ""——本策略方法论文本(空且 pa 开 → 后端用 `PA_METHOD_DEFAULT` 兜底)。

**计算(始终,便宜)**:在 `fac` 算完之后(现 ~L1210),`pa_feat = compute_pa_features(df, c, name)`(try/except → {})。**无论 pa 开关都算**——几何要随响应回前端供决策卡显示(决策「几何常显」)。

**prompt 注入(仅 `pa` 开)**:`usr_p` 在「量化因子」行后插
`【价量形态·确定性(PIT≤决策bar·{unit})】{render_pa_block(pa_feat, unit)}\n`;
并在末尾 `_ask` 前插
`【价格行为读法(本席方法论·推理框架·不替代证据·证据不足给观望)】{pa_method or PA_METHOD_DEFAULT}\n`。
pa 关 → prompt 与现状完全一致(零回归)。

**响应**:返回 JSON 增加 `pa_features: pa_feat`(始终,供前端真 LLM 决策卡显示真几何)。

**落盘**(`_persist_decision` rec):增加 `pa`(bool)、`pa_features`(dict)。

`render_factors` 既有签名(§参考):`render_factors(fac, fields, unit="日")`,逐字段「中文名=值(语义句)」;`render_pa_block` 风格与之对齐(中文、None→「—」)。

## 6. 前端:scanSeat 升级 + 几何镜像(`ui/seats/luozi-data.jsx`)

- 新增 `paFeatures(bars, idx)`:JS 镜像 §3 全套(bar 字段为 `o/h/l/c/v`,date)。返回同形 dict。**与后端契约对齐(§11)**。
- 新增 `renderPaNote(feat)`:把几何浓缩成一句中文(供 scanSeat `note` 与卡片副行)。
- 新增常量 `window.LZ_PA_METHOD_DEFAULT`(= §7 文本;校场开启开关时预填 textarea)。
- **scanSeat 升级**(三模板,几何作硬过滤 + 置信调节 + 诚实注释;阈值取命名常量、便于调):
  - **动量**进场,在现有(MA5↑穿 MA20 · 收>MA20 · 量>1.05×)基础上加几何闸:`bar_type=='趋势阳' 或 breakout=='突破前5高'`,且 `close_pos≥0.55`,且 `body≥0.45`,且 `vol_ratio≥1.1`,且 `limit!=='涨停'`(涨停封板难成交,不追)。置信加权:`+min(0.1, (close_pos-0.5)+(body-0.5)+(vol_ratio-1))`。`note` 引用真几何。
  - **反转**进场,在现有(5日超跌 · 收<MA20×0.96 · 缩量 · 收红转拐)基础上加:`lower_wick≥0.3 或 close_pos≥0.6`,`bar_type!=='趋势阴'`,`limit!=='跌停'`。
  - **事件**进场,在现有(event 标志)基础上加:`gap=='高开' 或 bar_type=='趋势阳'`(确认)。
  - 每条决策附真 `geo = paFeatures(bars, idx)` 字段(供决策卡)。**`evidenceFor()` 的合成 factors/research/card 保持现状不动**(已被审计标「示意」,与本期新增的真 geo 是两回事,不在本期清理范围)。
  - **说明**:此改动会改变启发式回测的进场(更挑、更有意义)——这是目的(让骨架从拍脑袋变确定性),真 LLM run 路径(runRealThink)不受影响。
- **strategySave**:新增持久化 `pa`(默认 false)、`paMethod`(默认 "")。其余字段不动。

## 7. 价格行为方法论默认模板(`PA_METHOD_DEFAULT` / `LZ_PA_METHOD_DEFAULT`)

```
价格行为读法(A股·做多为主):
1. 趋势 vs 区间:连续同向趋势棒(实体大、影线短、收于端部)= 趋势;互相重叠、影线长、收于中部 = 区间/震荡。趋势中顺势,区间中高抛低吸或观望。
2. 突破与回踩:放量突破前高(实体强、收于上沿)后,优先等第一次缩量回踩不破前高/均线企稳再进,胜率高于追突破当根。突破后迅速收回、留长上影 = 假突破,警惕。
3. 信号棒 + 跟随确认:孤立一根强棒不够,要看其后是否被同向棒跟随确认;无跟随、被反向吞没 = 信号失效。
4. 两腿回调:上升趋势中的回调常走两腿,第二腿缩量不破关键支撑后的转强棒,是较稳的右侧买点。
5. 位置感:同样形态在低位/超跌区比在高位/拥挤区可靠;高位放量滞涨、长上影、量价背离 = 退潮信号,降权或止盈。
6. A股 特有口径:T+1 当日买入次日才能卖,需为隔夜留余地;涨停封板≠可任意买卖(流动性骤降),涨停打开放量要警惕,跌停同理;ST 股 ±5% 幅度小、波动定义不同;不做空,只在做多方向取信号,看空时以「观望/减仓」表达。
几何特征是确定性事实(本席已附),本读法只是推理框架,不替代证据;证据不足时给「观望」。
```

(可编辑——用户在校场改写后存入 `strategy.paMethod` 并随 decide 传后端。)

## 8. 校场开关 + 可编辑方法论(`ui/seats/luozi-foundry.jsx`)

- 新建/编辑表单加一行「价格行为研判」开关——镜像现有 `bind` 芯片/勾选交互(`editing.pa` 布尔,点击切换)。
- **开关为 on 时**,其下露出方法论 `textarea`——镜像现有 `creed` 文本框样式;`value=editing.paMethod`;若为空则预填 `window.LZ_PA_METHOD_DEFAULT`(首次开启自动填入默认,供用户改)。
- 保存(钤印)时把 `pa: editing.pa`、`paMethod: editing.paMethod` 一并传给 `lzStrategySave`。
- `newDraft` 默认 `pa:false, paMethod:''`(默认关)。
- 编辑既有策略:读出其 `pa/paMethod`(缺省 false/'',存量不变)。

## 9. decide 调用接线 + 决策卡显示(`ui/seats/luozi-panels.jsx`)

- **runDecide** 的 decide payload 增加:`pa: !!s.pa`,`pa_method: s.pa ? (s.paMethod || window.LZ_PA_METHOD_DEFAULT || '') : ''`(pa 关时不送 method)。
- **DecisionCard** 新增「价量形态·确定性」块(徽章 **确定性·非LLM**):
  - 数据源:启发式决策读 `decision.geo`;真 LLM 决策读 decide 响应 `pa_features`。
  - 渲染最新根的 bar_type/实体/收盘位/上下影/振幅×ATR/距EMA20/突破/量比/涨跌停/跳空/跟随 + 近3根型态。None → 「—」。
  - 方法论用上时(pa 开)给一行小字标注「已注入本席方法论(可在校场编辑)」。
  - **几何块常显**(与 pa 开关无关);开关只决定 LLM prompt 里有没有它。

## 10. 数据流

```
真 LLM 研判(decide):
 fetch_quote(日线180d / 5min→agg30min) ──PIT≤asof──▶ df(OHLCV)
   ├─ compute_factors(df) ─▶ fac ─▶ render_factors ─▶【量化因子】
   └─ compute_pa_features(df,code,name) ─▶ pa_feat
         ├─(始终)──▶ 响应 pa_features ──▶ 决策卡「价量形态」块(确定性·非LLM)
         └─(pa 开)─▶ render_pa_block ─▶【价量形态·确定性】+ pa_method ─▶【价格行为读法】─▶ LLM prompt

启发式骨架(scanSeat,前端,回测复盘):
 bars(OHLCV) ─▶ paFeatures(bars,idx) ─▶ 几何闸 + 置信 + note ─▶ decision.geo ─▶ 决策卡(确定性·非LLM)
```

## 11. 契约与不变量(后续别回退)

- **后端↔前端几何契约**:`compute_pa_features`(Python 权威)与 `paFeatures`(JS 镜像)**同一特征定义/公式/枚举字符串**。两边各有单测;前端用 2–3 个黄金用例与后端逐位对齐(§12)。改一边必同步另一边——在两处函数顶钉互指注释。
- **PIT 红线**:任一特征只用 ≤决策 bar 的数据;`follow` 向后看(prev→current)不越界;不足窗口诚实 None。**绝不**用 >决策时刻的 bar(含 forward follow-through)。
- **几何=事实,方法论=框架**:prompt 与卡片里几何标「确定性」、方法论标「推理框架·不替代证据」;LLM 失败/证据不足 → 观望(沿用现有兜底)。
- **几何常显·开关只管注入**:几何特征始终算/显;`pa` 仅控制 LLM prompt 注入。
- **诚实降级 ≠ mock**:数据不足/拉取失败 → 「—」或空块,绝不补合成值。

## 12. 错误处理与测试

**错误/降级**:
- df 空 / 列缺 → `compute_pa_features` 返 {} → 无几何块(decide 沿用既有空 df 处理);响应 `pa_features={}`,卡片块显空/「—」。
- 不足窗口(<20/<15/<5 根)→ 对应项 None → 渲染「—」。
- pa 开但 pa_method 空 → 后端 `PA_METHOD_DEFAULT` 兜底。
- 涨跌停板幅判定失败(code/name 异常)→ `limit=None`,不崩。

**后端 TDD(`tests/test_price_action.py`):**
- 趋势阳:`o=10,h=11,l=9.8,c=10.9` → body≈0.75、close_pos≈0.917、upper_wick≈0.083、lower_wick≈0.167、bar_type=趋势阳。
- 十字:body<0.1 → bar_type=十字。
- 内含bar:`high≤prev_high and low≥prev_low` → 内含bar;连续两根 → `inside_streak=2`。
- 外包阳/阴:严格包住前根 → 外包阳/外包阴。
- 突破:构造 6 根,末根 high>前5高 → `突破前5高`;末根 low<前5低 → `跌破前5低`;否则区间内。
- 涨跌停按板:主板 600 prev=10/c=11(+10%)→涨停;c=10.8(+8%)→接近涨停;科创 688 c=12(+20%)→涨停;`*ST` name c=10.5(+5%)→涨停。
- 跳空:open>prev_close → 高开;< → 低开。
- 跟随:前根趋势阳 + 本根收高于前收且收阳 → 已确认(多);前强本反 → 转弱。
- 不足窗口:<15 根 range_atr=None;<20 根 ema20_rel=None;<5 根 vol_ratio/breakout=None;首根 limit/gap=None。
- `render_pa_block`:None 项渲染「—」;空 feat → 空串;含 unit="根30分钟bar"。
- 30min:同函数喂 30min df 正常出特征。
- decide 接线测试:pa=true 时响应含 pa_features 且 prompt 装配含两块;pa=false 时响应仍含 pa_features 但 prompt 不含两块(零回归);落盘含 pa/pa_features。

**前端镜像对齐**:`paFeatures` 用与后端相同输入的 2–3 个黄金用例断言逐位一致(在浏览器 console 或注释化对照),确保契约不漂。

**浏览器 e2e(`?v` bump 后真跑):**
- 校场:建策略→开「价格行为研判」→方法论 textarea 露出且预填默认→改写→钤印保存→重开持久。
- 决策卡(启发式):几何块渲染 + 「确定性·非LLM」徽章 + 引用真几何;pa 关时几何块仍显(几何常显)。
- 真 LLM(让 agent 真跑,pa 开):`var/seats_decisions.jsonl` 末条含 `pa:true`+`pa_features`;卡片显真几何;pa 开标注方法论。
- pa 关:研判行为与现状一致(prompt 不含两块)。
- pytest 全绿;重启 9999;bump `?v`。

## 13. 文件清单

- **新建** `guanlan_v2/seats/price_action.py`(compute_pa_features / render_pa_block / PA_METHOD_DEFAULT)
- **新建** `tests/test_price_action.py`
- **改** `guanlan_v2/seats/api.py`(decide:算几何 + 响应 + pa 注入 + 落盘)→ **重启 9999**
- **改** `ui/seats/luozi-data.jsx`(paFeatures / renderPaNote / LZ_PA_METHOD_DEFAULT / scanSeat 升级 / strategySave +pa+paMethod)
- **改** `ui/seats/luozi-foundry.jsx`(开关 + 方法论 textarea + 保存传 pa/paMethod)
- **改** `ui/seats/luozi-panels.jsx`(DecisionCard 几何块 + runDecide 传 pa/pa_method)
- **改** `ui/seats/观澜 · 落子.html`(bump data/panels/foundry `?v`)
- 收尾:`ui/seats/README.md`、memory、(本仓无 git,「提交」=跑 pytest)

## 14. 范围外 / 后续

- 几何特征升格引擎因子库一等公民(全平台:选股/工作流/IC)——方案 B,大工程。
- PA_Agent 式多 setup 文件路由 / 两阶段诊断分流。
- 几何特征进 P3 加权混合信号(目前几何只喂 LLM + scanSeat,不进确定性信号权重)。
- 历史新闻/事件流接入几何(事件模板的 event 标志目前仍是 genBars 合成,真 bar 无此标志)。

## 15. 回滚 / 上线

- 全程默认关:`pa=false` 时**零行为变化**(prompt、信号、回测口径均同现状),几何块作为新增显示模块出现但不影响决策。
- 上线:改后端重启 9999;改 jsx bump `?v`;pytest 全绿后视为「提交」。
