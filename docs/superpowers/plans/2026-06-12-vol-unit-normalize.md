# 成交量量纲校准 Implementation Plan(K线VOL空缺+量比因子失真修复)

> **状态 2026-06-12:Task 1 + 1b + 2(审计,见文末结论)+ 3 + 4(产物重算)全部完成,pytest 204 绿。**
>
> **Task 4(审计结论落地,用户拍板「开始」):离线 compute 路径校准 + 产物重算**。三支柱直读 `_read_bin` 不走 fetch_quote,读取层修复对其无效 → 新建 `guanlan_v2/strategy/compute/units.py::normalize_frame_units(df, vol_col="volume", ref_vol=None)`(与引擎同检测带;v4 直接模式修 volume+amount,breadth/mainline 无 vol 列用 ref_vol 参照模式只修 amount 千元批次)。接线三处:v4.py build_feature_panel 窗口切片后、breadth.py build_breadth_panel、mainline.py build_sector_panel_industry(各 +volume 参照读)。tests/test_panel_units.py 4 测试(合成×3+三支柱契约钉)。**重算验真**(end=06-09):新旧 v4 顶 200 重合 154/200(77%),**科创板顶 200 占比 33→36 只**(系统性压低解除),lgb_pct Spearman 0.784(LGB 在干净特征上重训,分数实质移动=修复生效),共同顶 200 的 v4_total Spearman 0.985(留存票排序稳定);/screen/run serving 新产物正常。
>
> **Task 1b(计划外新发现,已修)**:用户复验立昂微 30 分图右段(06-10/06-11)VOL 仍无柱 → 确诊**第二种污染形态:分钟数据 vol=手 且 amount 整日全缺**(r 自检因需 amount>0 而失明;立昂微 5min 06-01/06-10/06-11 三天 amount 全 NaN、vol 恰为日线 1/100)。修法 = `QlibBinaryLoader._crosscheck_intraday_vol(code, df)`:freq≠day 时,对「当日 amount 全缺」的日用**日线(已校准为股)交叉定标**——日合计与日线 vol 比值∈[50,200] → 整日 vol×100;日线缺该日(盘中今日)或比值≈1 → 原样;异常原样返回。挂 `fetch_quote` 出口第二道。测试 `test_crosscheck_intraday_vol_subprocess`(合成 stub 单测 + 立昂微真数据集成测)+ 契约钉增补。验真:活服务 /seats/daily?freq=5min 立昂微 06-10=1.06亿股/06-11=9730万股(与日线「量9730万」精确吻合),浏览器 30 分图右段柱子恢复。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复日线数据中 vol/amount 量纲批次混杂(正常段 vol=股·amount=元;污染段 vol=手 或 vol=手+amount=千元,tushare 风格批次混入)——在引擎 loader 读取层做确定性单位自检校准,一处修复使 /seats/daily(K线VOL显示)、研判因子(turnover_20 量比)、live_eval、逐bar真跑、置信校准全链受益。

**Architecture:** `engine/.../data/loaders/qlib_binary.py` 加模块级纯函数 `_normalize_vol_units(df)`(vectorized:r=(amount/close)/vol 自检,r∈[50,200]→vol×100;r∈[0.05,0.2]→vol×100 且 amount×1000;NaN/零量/缺列/异常原样返回),挂 `fetch_quote` 出口。**不改写任何数据文件**(读取层校准,source-agnostic)。离线 regen 产物(v4/breadth)是否被污染段影响→只读审计出报告,重算另行决策。

**Tech Stack:** Python 3.13 / pandas / pytest(引擎测试须子进程强制仓内路径——venv .pth 指旧仓的已知坑)

**硬约束:**
- **本仓无 git——禁止 git 命令,"提交"=跑 pytest**;pytest 口径同前(基线 **197 绿**);改引擎须重启 9999(controller 收口做);GateGuard 四事实照做;用户指令原话:「开始」;**G:/stocks 只读绝不写**

**已核事实(controller 实测,中微 SH688012 loader 层):**
```
2026-03-13 vol=6,691,022  amount=20.9亿  ratio=1.0   ← 正常(股+元)
2026-03-16 vol=108,614    amount=339.8万 ratio=0.1   ← 双错(vol=手 且 amount=千元)
2026-03-31 vol=80,954     amount=25.4亿  ratio=102.4 ← vol=手
2026-04-24 vol=172,672    amount=55.2亿  ratio=98.9  ← vol=手
2026-06-09 vol=212,813    amount=59.5亿  ratio=98.0  ← vol=手
2026-06-10 vol=29,607,940 amount=87.0亿  ratio=1.0   ← 正常
```
(ratio = (amount/close)/vol)
- 调用链:`/seats/daily`、decide/live_eval 的 `compute_factors`(turnover_w=vol[-1]/avg(vol,w))、逐bar真跑、置信校准端点全走 `loader.fetch_quote`(qlib_binary.py:275-290 → `_build_df(code, QUOTE_FIELD_MAP, ...)`)
- 后果实证:中微「20日量比8.85倍」为量纲假象(amount 口径真实放量 ~1.4 倍);前端 VOL 柱按全窗 max 归一,「手」段高度<1px 视觉消失
- 正常 bar 的 r≈VWAP/close∈[0.9,1.1],与 [50,200]/[0.05,0.2] 检测带无重叠,零误伤

---

## Task 1: `_normalize_vol_units` + fetch_quote 挂接 + 子进程测试(TDD)

**Files:**
- Create: `tests/test_vol_units.py`
- Modify: `engine/financial_analyst/data/loaders/qlib_binary.py`(模块级函数 + fetch_quote 出口一行)

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_vol_units.py`(完整文件;子进程模式因 venv .pth 指旧仓):

```python
"""成交量量纲校准单元测试(子进程强制仓内 engine——venv .pth 指旧仓的已知坑)。

口径:vol 应为「股」、amount 应为「元」;r=(amount/close)/vol 自检:
≈1 正常不动;∈[50,200] vol=手 → ×100;∈[0.05,0.2] vol=手+amount=千元 → vol×100,amount×1000。
"""
import subprocess

_PY = r"G:\financial-analyst\.venv\Scripts\python.exe"

_SCRIPT = r'''
import sys; sys.path.insert(0, r"G:/guanlan-v2/engine")
import pandas as pd
import financial_analyst.data.loaders.qlib_binary as qb
assert "guanlan-v2" in qb.__file__.replace("\\", "/"), qb.__file__
df = pd.DataFrame({
    "close":  [100.0, 100.0, 100.0, 100.0, float("nan"), 100.0],
    "vol":    [1_000_000.0, 10_000.0, 10_000.0, 0.0, 5.0, 1_050_000.0],
    "amount": [100_000_000.0, 100_000_000.0, 100_000.0, 0.0, 1.0, 100_000_000.0],
})
# row0 正常(股+元) r=1 不动;row1 vol=手 r=100 → vol×100
# row2 双错(手+千元) r=0.1 → vol×100, amount×1000;row3 零量停牌不动
# row4 close NaN 不动;row5 r≈0.95(正常 VWAP 偏离)不动
out = qb._normalize_vol_units(df.copy())
assert out.loc[0, "vol"] == 1_000_000 and out.loc[0, "amount"] == 100_000_000
assert out.loc[1, "vol"] == 1_000_000 and out.loc[1, "amount"] == 100_000_000
assert out.loc[2, "vol"] == 1_000_000 and abs(out.loc[2, "amount"] - 100_000_000) < 1e-6
assert out.loc[3, "vol"] == 0.0
assert out.loc[4, "vol"] == 5.0 and out.loc[4, "amount"] == 1.0
assert out.loc[5, "vol"] == 1_050_000.0
assert qb._normalize_vol_units(pd.DataFrame()).empty
nocol = pd.DataFrame({"close": [1.0]})
assert qb._normalize_vol_units(nocol).equals(nocol)
print("VOLNORM OK")
'''


def test_normalize_vol_units_subprocess():
    r = subprocess.run([_PY, "-c", _SCRIPT], capture_output=True, text=True, timeout=120)
    assert "VOLNORM OK" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")


def test_fetch_quote_pins_normalize():
    # 契约钉:fetch_quote 出口必须经 _normalize_vol_units(防回退)
    src = open(r"G:\guanlan-v2\engine\financial_analyst\data\loaders\qlib_binary.py",
               encoding="utf-8").read()
    assert "def _normalize_vol_units" in src
    body = src.split("def fetch_quote")[1].split("\n    def ")[0]
    assert "_normalize_vol_units(" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_vol_units.py -q`
Expected: 2 failed(`_normalize_vol_units` 不存在)

- [ ] **Step 3: 实现** — `engine/financial_analyst/data/loaders/qlib_binary.py`:

(a) 模块级 helper 区加:

```python
def _normalize_vol_units(df: "pd.DataFrame") -> "pd.DataFrame":
    """单位自检校准(2026-06-12 量纲跳变修复):vol 应为「股」、amount 应为「元」。

    历史增量批次混入 tushare 风格单位(vol=手,或 vol=手+amount=千元),致 K 线 VOL
    柱视觉消失、turnover 量比因子虚高 ~100 倍。用 amount/close 隐含股数自检:
    r=(amount/close)/vol —— ≈1 正常;∈[50,200] → vol×100;∈[0.05,0.2] → vol×100
    且 amount×1000。正常 bar 的 r≈VWAP/close∈[0.9,1.1],与检测带无重叠零误伤。
    NaN/零量/缺列原样;任何异常原样返回(校准绝不挡数据)。
    """
    try:
        if df is None or len(df) == 0:
            return df
        for col in ("close", "vol", "amount"):
            if col not in df.columns:
                return df
        c, v, a = df["close"], df["vol"], df["amount"]
        ok = c.notna() & v.notna() & a.notna() & (c > 0) & (v > 0) & (a > 0)
        if not bool(ok.any()):
            return df
        r = pd.Series(float("nan"), index=df.index, dtype="float64")
        r[ok] = (a[ok] / c[ok]) / v[ok]
        hand = ok & r.between(50.0, 200.0)            # vol=手
        dual = ok & r.between(0.05, 0.2)              # vol=手 且 amount=千元
        if bool(hand.any()):
            df.loc[hand, "vol"] = v[hand] * 100.0
        if bool(dual.any()):
            df.loc[dual, "vol"] = v[dual] * 100.0
            df.loc[dual, "amount"] = a[dual] * 1000.0
        return df
    except Exception:  # noqa: BLE001 — 校准自身故障绝不挡数据
        return df
```

(b) `fetch_quote`(:275-290)末行 `return self._build_df(code, QUOTE_FIELD_MAP, start, end, freq=freq)` 改:

```python
        return _normalize_vol_units(self._build_df(code, QUOTE_FIELD_MAP, start, end, freq=freq))
```

(5min/1min 同走此口——正常 bar r≈1 不动,安全通吃。)

- [ ] **Step 4: 跑测试** — `pytest tests/test_vol_units.py -q` → **2 passed**

- [ ] **Step 5: 全量** — Expected: **199 passed, 0 failed**(197+2)

## Task 2: 污染面审计(只读,出报告不改码)

**Files:** Read only;结论追加到本计划末尾「## 审计结论」节

- [ ] **Step 1:** 子进程(sys.path 指仓内 engine)抽样 20 只票(盯盘池 10 只:300750/600519/002594/300308/601012/600036/688283/605358/688012/605117 + csi300 任取 10 只)各拉 260 根,统计校准触发率(hand/dual 各多少 bar、集中日期段),输出表
- [ ] **Step 2:** 判断离线 regen 产物是否受污染:Read `guanlan_v2/strategy/compute/breadth.py` 与 `v4.py` 中 vol/amount 的使用处(成交额加权/turn 因子/amihud 等),结合污染日期段评估 06-09 regen 产物失真面;**只出结论**(无影响/轻微/建议重算+理由),不动产物
- [ ] **Step 3:** 结论(数字+文件:行号)Edit 进本计划「## 审计结论」节

## Task 3: 收口(controller 亲自做)

- [ ] 全量 pytest 终验(199 绿)
- [ ] 重启 9999 + 探活
- [ ] 真机验证①:`/seats/daily?code=SH688012&n=260` 全部 bar 的 (amount/close)/vol ∈[0.5,2],03-16/04-24/06-09 修复后 vol 与邻近段同量级
- [ ] 真机验证②:POST /seats/decide(SH688012, date=今日)→ factors_std.turnover_20 显著回落(预期 ~1.x 而非 8.85)且 rationale 量比叙述随之诚实
- [ ] 真机验证③:浏览器落子页刷新 → 中微 K 线 VOL 空缺段柱子恢复(截图给用户)
- [ ] memory 收口(live-drill 增「量纲修复」节;**历史落盘不改写**——演习/真跑记录里 turnover_20 偏高值留档并注明口径,新研判起用干净数据;置信校准方向命中不受量纲影响)

---

## Self-Review(已执行)

- 覆盖:显示(VOL柱)、因子(turnover)、全链 serving 一处修;离线产物审计只读分期;历史落盘不改写(诚实)。
- 占位符扫描:无;实现/测试代码全给。
- 类型一致:`_normalize_vol_units(df)->df` 纯函数;检测带 [50,200]/[0.05,0.2] 与测试样例一致;契约钉防回退。

---

## 审计结论(2026-06-12)

> 只读审计(Task 2),口径:`loader._build_df(code, QUOTE_FIELD_MAP, ...)` 取**校准前 raw**,r=(amount/close)/vol;hand=r∈[50,200](vol=手)、dual=r∈[0.05,0.2](vol=手+amount=千元)。审计当日 vendored 三产物 mtime=2026-06-12 17:09~17:13、end=2026-06-09(pe_ttm 只到 06-09),即「06-09 产物」=当前在役产物。

### 1. 日线触发率(20 票 × 2025-06-01~2026-06-12,共 5000 bar)

| 票 | bars | hand | dual | normal | 污染% |
|---|---|---|---|---|---|
| 盯盘池 8 只(300750/600519/002594/300308/601012/600036/605358/605117)及 csi300 补足 10 只(601318/600276/601888/600900/601166/000858/002475/600030/600887/601398) | 各250 | 各59 | 各1 | 各190 | **24.0%** |
| SH688283 / SH688012(科创板) | 各250 | 各41 | 各1 | 208/199 | **16.8%** |
| **合计** | **5000** | **1144** | **20** | 3827 | **23.3%** |

**污染是按日期整批的,不是按票的**:
- **dual 仅 1 天 = 2026-03-16**,20/20 票全中(全市场扫描:5264 dual + 204 hand / 5482 有效 ≈ **99.9%**)→ 该日 amount 全市场 ≈ 真值 1/1000。
- **hand 连续段 = 2026-03-17 ~ 2026-06-11**(审计时数据末日),每个交易日 20/20 票(科创板例外段 18/20)。月分布(bar 数):2026-03=220、04=388、05=360、06=176。
- **科创板(SH688)是独立批次**:04-01~04-23 与 06-10/06-11 清洁(=股),其余同 hand。全市场单日验证:04-10 hand=4889、normal=604(normal 全部 SH68);**06-09(regen end)hand=5512/5527 ≈ 99.7% 全板块**;06-11 hand=4889、SH68 全清洁 606。
- **03-16 之前基本干净但有常驻底噪**:2025-09-15/12-01、2026-01-15/02-13/03-13 全市场扫描 dual 恒 ≈57 只 + hand 0~60 只(≈1.1~2.3%)——存在一小撮代码 amount 长期=千元,读取层校准同样兜住,源头重灌难覆盖。

### 2. 分钟线 amount 缺失面(20 票 × 2026-05-01~2026-06-12)

- 5min 滚动窗实存 **2026-05-06 ~ 2026-06-11(27 天)**。
- 「amount 整日全缺」:**06-01、06-10、06-11 三天 20/20 票全中**;另 06-02 在 SZ300750/SZ002594/SZ300308/SZ002475 4 票上也全缺。
- 这些天分钟 vol 量纲:日线(已校准为股)÷分钟日合计 = **100.0**(vol=手)——除 SH688283/SH688012 两只科创板 = **1.0**(已是股)。与 `_crosscheck_intraday_vol` 的 [50,200]→×100、≈1→不动 设计完全吻合,零误判。

### 3. regen 产物影响评估(三支柱直读 `_read_bin`,**不走 fetch_quote,读取层校准对其无效**;`qlib_binary.py:323` 校准只挂 fetch_quote,`:281-299 _build_df/_read_bin` 仍出 raw)

**① breadth(`guanlan_v2/strategy/compute/breadth.py`)——轻微**
- 用 amount 不用 vol:`:87` 读 amount、`:113` amount>0 停牌滤、`:144` total_amount_yi、`:148` amount_pct_60d/250d、`:158-192` 60 日 OLS 残差。hand 批 amount=元不受害;**唯一伤 = 03-16 dual**。
- 产物实测:amt_resid(03-16) = **-20942**(前段 std≈4244 的 ~5σ 假尖峰),lu_resid(03-16)=+50.5/pct60=0.967 假高。
- 反事实定量(只修 03-16 total_amount_yi 449→23292 重算):03-16~06-09 共 **58 天**残差受牵连,mean|Δ| lu_resid=5.2 / amt_resid=756;但**下游真正消费的 pct60 两列**只有各 **3 天** |Δ|>0.1(03-16/03-30/04-02 与 03-16/03-17/03-20),**06-09 头部:lu_resid_pct60 不变、amt_resid_pct60 0.150→0.167(1 个名次格)**。市场节奏/市场周期当前读数几乎无感,失真集中在 3 月中~4 月中归档段。
- 03-16 stock_count=5177 与邻日持平(千元仍>0,过滤未误杀)。

**② mainline(`compute/mainline.py`)——状态结论无影响**
- amount 仅入 `:122` 过滤、`:146` total_amount、`:162` total_amount_yi、`:182-186` 滚动分位、`:191` amt_rank_today。产物实测 03-16 全市场 total_amount_yi **23727→448.5(≈1/53)**。
- 但 `classify_status(:230-250)` 判据 = ex_*(mean_ret,close 系)+ top10_ratio(lu_rank←lu_count,close 系)+ lu_max_mv(市值)+ lu_count——**全部不含 amount** → 主线状态(mainline/decay/revival/…)不受污染;失真只在展示性 amount 列(03-16 行 + 含它的 20/60 日分位窗,量级 ≤1/60 名次)。

**③ v4(`compute/v4.py`)——建议重算**
- vol/amount 入 10 个因子:vol_ratio_5_20(:86)、amihud_20(:88)、amount_ratio_5_20(:94)、vol_trend_5_60(:96)、corr_close_vol_20(:97)、amt_cv(:102)、obv_slope(:105)、quiet_dip(:110)、vol_dry(:111)、vol_spike(:116);`build_feature_panel(:142)` 直读 raw volume。
- **06-09 预测截面实证**(5478 只全市场,raw vs 逐票校准后,Spearman/中位数比):

| 因子 | Spearman | median(raw/校准) | 判定 |
|---|---|---|---|
| vol_ratio_5_20 / corr_close_vol_20 / vol_dry / vol_spike / quiet_dip / amount_ratio_5_20 / amt_cv | 1.0000 | 1.00 | 窗口内量纲齐次,自消,无伤 |
| amihud_20 | 1.0000 | **×100** | 排序保形但**整体偏离训练分布 100 倍** → LGB 在干净段学的分裂阈值失效,因子判别力被钝化 |
| obv_slope | 1.0000 | **×0.01** | 同上(×1/100 OOD) |
| **vol_trend_5_60** | **0.5856** | ×0.25 | **真失真**:60 日窗跨 03-12/13 清洁日;科创板批次差 → SH688 中位 0.038 vs 其余 0.245(校准后两者均≈0.9~1.0)= **对科创板系统性 ~6.5× 压低**,排序与真值仅 0.59 相关 |

- 训练面:2026-03-16~06-04 约 56 个交易日 ×~5300 票 ≈ **5.5% 训练行**带污染 vol 特征(跨界窗 03-16~04-20 段最毒)。label(close 系)干净。
- 缓冲项:adaptive 择时 7 因子在 06-09 全部干净/自消;顶 200 五维评分的 volume 维用 vol_ratio_5_20(自消);breadth 残差 broadcast 是按日常数,不改当日截面排序。失真主通道 = LGB score(3 个 vol 因子被废/被偏)→ final_score 的 50% 权重。
- **结论:v4_ranking_latest(date=2026-06-09)的排名受真实可测失真,方向 = 科创板被 vol_trend_5_60 系统性错标为「极端缩量」+ amihud/obv 两因子判别力钝化。建议重算。**

**④ 顺带核验**:regen 3.5 步 factor_ic 走 `factors/zoo/panel.py:408 loader.fetch_quote`(已校准路径),06-12 17:13 再生的 factor_ic 产物**干净**;model_health/快照档随 v4 出,继承 v4 失真。

### 最终判定

1. **日线污染面**:2026-03-16(dual,全市场 99.9%)+ 2026-03-17~06-11(hand,全市场 ≈99.7%,科创板 04-01~04-23 与 06-10/11 例外清洁);之前一年干净(常驻底噪 ~1.1% dual)。抽样 20 票总触发率 **23.3%**(读取层校准命中率,即修复的真实覆盖面)。
2. **分钟线**:06-01/06-10/06-11(+部分票 06-02)amount 整日全缺且 vol=手(科创板除外,已是股),`_crosscheck_intraday_vol` 设计与实况吻合。
3. **产物**:mainline 状态**无影响**;breadth resid 头部**轻微**(06-09 pct60 偏移 ≤0.017,失真集中 3~4 月归档段);**v4 建议重算**。
4. **重算方案(关键前提)**:三支柱直读 `_read_bin`,**先把 `_normalize_vol_units` 接进 compute 路径再重算,否则重算=原样复现失真**——最薄改法:`v4.build_feature_panel`(v4.py:139-156)、`breadth.build_breadth_panel`(breadth.py:86-90)、`mainline.build_sector_panel_industry`(mainline.py:92-98)三处逐票 frame 组好后过一次 `_normalize_vol_units`(列名对齐 close/vol/amount),然后 `python -m guanlan_v2.strategy.compute.regen 2026-06-09` + 重启 9999。源头重灌 bin 亦可,但盖不住 ~57 只常驻 dual 底噪,且 G:/stocks 写入权限另议。
