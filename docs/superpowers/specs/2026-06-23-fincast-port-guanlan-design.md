# FinCast 生成栈港进 guanlan(Spec 2)· 设计文档

**日期**:2026-06-23
**状态**:设计已确认,待写实施计划
**前置**:[[dl-ensemble-layer]] Spec 1(统一 DL 集成层)已交付合 main(`355cff9`)+ Task7 真机验真(FinCast 经 stocks 脚本+sync 激活,DL 真参与)。本 Spec 2 把 FinCast **生成栈**从 stocks 港进 guanlan,去掉 stocks 仓脚本/sync 依赖。

---

## 1. 背景与目标

Spec 1 的 DL 集成层 producer-agnostic,只读 `var/v4_fincast_pred.parquet`。当前该预测表由 **stocks 侧** `G:/stocks/tsfm_exp/scripts/fincast_daily_predict.py`(GPU 推理)产出 + `scripts/sync_fincast.py` 桥接搬来。本项目把这套生成栈港进 guanlan:**FinCast 模型代码 / 权重 / 推理脚本 / 输出全归 guanlan**,读 guanlan 自己的 close,直写 guanlan `var/`,去掉 stocks 仓脚本与 sync。

**目标**:guanlan 自有 `scripts/fincast_predict.py`(用 conda stocks GPU 解释器跑)→ 直产 `var/v4_fincast_pred.parquet` → regen 读它 DL 混合。刷新 DL 参与从「stocks 跑脚本 + sync + regen」简化为「guanlan 跑脚本 + regen」。

## 2. 范围与诚实边界

**做(本增量)**
- vendor FinCast 模型仓代码(`fincast_repo`)+ 4GB 权重(`v1.pth`)进 guanlan(gitignore)
- guanlan 自有 `scripts/fincast_predict.py`(移植 stocks 脚本逻辑·读 guanlan close·直写 guanlan var)
- 内置 `FinCastAdapter`(用 vendored `tools.inference_utils.get_model_api` 加载 v1.pth)
- 去掉 `sync_fincast.py`(deprecate/删)+ 更新运维口径
- setup 文档(一次性 vendor 拷贝步骤)

**诚实边界(港移去掉什么、仍共享什么)**
- ✅ **去掉**:stocks 仓脚本 `fincast_daily_predict.py` 依赖、`sync_fincast.py` 桥接、stocks qlib `D.features` 调用
- ⚠️ **仍共享(刻意,非本范围)**:① **qlib close 数据目录** —— guanlan `DEFAULT_PROVIDER = "G:/stocks/stock_data/cn_data"`(regen.py:35),guanlan 既有配置就指 stocks 目录,本项目不动;② **conda stocks GPU 环境**(用户选的复用解释器,已有 torch cu128 + FinCast 依赖)
- 即:本项目是「去 stocks **仓代码/脚本/sync** 依赖」,**非物理全独立**(数据目录 + GPU 解释器仍共享)

**不做(范围外)**
- 把 qlib close 数据目录拷进 guanlan(巨大·guanlan 既有配置问题)
- 新建 guanlan 专属 GPU conda 环境(用户选复用 stocks 环境)
- FinCast 模型微调/重训(只做零样本推理)
- Spec 3 工作流 LSTM 升格

## 3. 架构 / 数据流

```
一次性 setup(vendor):
  cp -r G:/stocks/tsfm_exp/fincast_repo        ->  G:/guanlan-v2/vendor/fincast_repo   (gitignore)
  cp    G:/stocks/tsfm_exp/models/fincast/v1.pth -> G:/guanlan-v2/vendor/models/fincast/v1.pth (gitignore·4GB)

刷新预测(conda stocks python 跑 guanlan 脚本·GPU·非请求路径):
  <conda stocks python> scripts/fincast_predict.py --date <D>
    ① sys.path 插:guanlan engine/(financial_analyst)+ vendor/fincast_repo/src(tools/ffm/data_tools)
    ② QlibBinaryLoader(DEFAULT_PROVIDER) 读 universe × context_len(512)日 close,截至 D
    ③ FinCastAdapter(vendored code + vendor/models/fincast/v1.pth · GPU bf16 · batch 64)批量预测 pred_ret_5d
    ④ 直写 var/v4_fincast_pred.parquet(契约 eval_date/instrument/pred_ret_5d · 同日覆盖 · rolling-keep 60日)
  → regen <D>(Spec 1 的层读 var/v4_fincast_pred.parquet → apply_dl_ensemble DL 混合)→ 重启 9999

命门(同 Spec 1):GPU 推理离线(conda stocks),绝不在 9999 请求路径跑模型。
```

## 4. 组件详述

### 4.1 vendor(一次性港移)
- `vendor/fincast_repo/`:从 `G:/stocks/tsfm_exp/fincast_repo`(= git clone `vincent05r/FinCast-fts`)整目录拷入。提供 `src/tools/inference_utils.get_model_api` + `ffm.*` + `data_tools.*`(FinCast 1B decoder-only + MoE 架构代码)。
- `vendor/models/fincast/v1.pth`:从 `G:/stocks/tsfm_exp/models/fincast/v1.pth`(3.97 GB)拷入。
- **`.gitignore`** 加 `vendor/fincast_repo/` 与 `vendor/models/`(若未覆盖)—— 同 4GB 权重/artifacts 不入库惯例。
- **setup 文档** `scripts/setup_fincast.md`:记录拷贝来源 + `git clone https://github.com/vincent05r/FinCast-fts vendor/fincast_repo` 复现路径 + 权重获取说明。

### 4.2 `scripts/fincast_predict.py`(guanlan 自有推理脚本)
移植 stocks `fincast_daily_predict.py`(实测逻辑:argparse / 拉 close / 截 eval_date / topn 过滤 / build context window / 有效标的过滤 / 批量推理 / rolling-keep 写 parquet),三处改 guanlan 化:
- **argparse**:`--date`(评估日,缺省=guanlan 最新交易日)、`--topn`、`--context-len`(默认 512)、`--horizon`(5)、`--batch-size`(64)、`--min-valid-frac`(0.9)——与 stocks 脚本同。
- **close 读取(替 stocks qlib `D.features`)**:用 guanlan `QlibBinaryLoader(DEFAULT_PROVIDER)` 向量化读 universe 的 `close` 面板(截至 eval_date,取末 context_len 日),建 `(N instruments × context_len)` float32 矩阵。**复用 breadth/v4 同款向量化读 close 路径**(非逐码 `fetch_quote` 循环,守性能;~5000 码)。instrument 口径 = qlib SH######(与 v4 universe 一致,Task7 已证 5027 匹配)。
- **FinCastAdapter(内置·见 4.3)**:加载 vendored 代码 + `vendor/models/fincast/v1.pth`,GPU 推理。
- **输出(替 stocks 落点 + sync)**:直写 `<repo>/var/v4_fincast_pred.parquet`,扁平列 `eval_date/instrument/pred_ret_5d`(Spec 1 `_load_dl_for_date` 读的契约),同日覆盖 + rolling-keep 60 日(防爆盘,同 stocks)。

### 4.3 `FinCastAdapter`(港移 ~50 行)
移植 stocks `zero_shot_daily.py:FinCastAdapter`:
- 把 `vendor/fincast_repo/src` 加 sys.path → `from tools.inference_utils import get_model_api`。
- 定位 `vendor/models/fincast/v1.pth`。
- `cfg = SimpleNamespace(backend='gpu', model_path=pth, model_version='v1', horizon_len=5, context_len=clip(512,32,1024), num_experts=4, gating_top_n=2, load_from_compile=True, forecast_mode='mean')`。
- `self.ffm_api = get_model_api(cfg)`;`predict(contexts, horizon)` 调 `ffm_api.forecast(序列, 频率) → (mean, full)`,转 pred_ret_5d。
- 可放进 `scripts/fincast_predict.py` 内,或 `guanlan_v2/.../fincast_adapter.py`(放脚本内即可,YAGNI)。

### 4.4 去 stocks 依赖
- `scripts/sync_fincast.py`:标记 deprecated(顶部 docstring + 可保留兼容,或删)。新流程不再需要它。
- 更新运维口径(记忆 + 若有 ops 文档):刷新 DL = `<conda stocks python> scripts/fincast_predict.py --date <总市值覆盖日>` → `regen <date>` → 重启 9999(去 sync 一步)。

## 5. PIT / 诚实合约(红线)
- **serving 零推理**:GPU 推理只在 conda stocks 离线脚本里跑;9999 请求路径绝不加载模型(沿用 Spec 1 命门)。
- **PIT 无前视**:close 只取 ≤ eval_date(零样本 FinCast v1 无训练窗 cutoff → Spec 1 provenance lookahead=null,本脚本不引入 cutoff)。
- **契约一致**:输出列 `eval_date/instrument/pred_ret_5d` 与 stocks sync 产出**逐字一致**(Spec 1 层不改即读)。

## 6. 测试
GPU 模型推理本身难单测;按可测面分:
- **轻量单测**(纯函数,无 GPU):close 面板 → context 矩阵构建(末 context_len 日 / 有效标的过滤 min_valid_frac / ffill-bfill)、输出 parquet 契约 + rolling-keep 60 日逻辑(同日覆盖 + 保留最近 N 日)。
- **集成验证**(有 GPU,见 §7):跑真脚本端到端。

## 7. 验证(集成·真数据)
1. 一次性 vendor 拷贝(fincast_repo + v1.pth 进 guanlan)。
2. `<conda stocks python> scripts/fincast_predict.py --date 2026-06-22` → 确认产 `var/v4_fincast_pred.parquet`:当日 ~5000 条、列契约对、与 stocks 脚本同口径(可对比当日 pred 与 Task7 的一致性,允许小数值差=同模型同权重应近似/相同)。
3. `regen 2026-06-22` → `v4_dl_provenance.json` `active:true`、fincast 源 weight>0、n_has~5000 → live `/screen/run` DL 混合(= Task7 同款结果,但**全由 guanlan 脚本驱动,零 stocks 脚本/sync**)。
4. 确认全程不碰 `G:/stocks/tsfm_exp/scripts/` 与 `sync_fincast.py`。

## 8. 风险与坑
- **4GB 权重拷贝**:`vendor/models/fincast/v1.pth` 占盘 4GB;gitignore 必须覆盖,别误入库。
- **conda stocks 跨环境 import guanlan**:脚本用 conda stocks python 跑,需 sys.path 插 guanlan engine/(`QlibBinaryLoader` 只依赖 numpy/pandas,conda stocks 有 → 可行);若 `financial_analyst` 还有别的 import 链拖入缺失依赖,退路 = guanlan 主 CPU 环境预导出 close 面板 parquet,脚本只读它(解耦 import)。
- **fincast_repo/src 的 import 链**:`tools/ffm/data_tools` 可能依赖特定包(stocks 环境已装);vendored 后用 conda stocks 跑应一致(同解释器)。
- **GPU 内存/版本**:bf16 + RTX 5090 + torch cu128(Task7 已验环境 OK);batch 64 实测 5027 只 31.9s。
- **向量化 close 读取**:逐码 `fetch_quote` 循环 5000 次慢;须用向量化面板读(breadth 同款),否则脚本慢几分钟。
- **date 对齐**:`--date` 须 = 总市值覆盖日(同 Spec 1/Task7 口径),否则 regen v4_total 塌;脚本默认取 guanlan 最新交易日(`_latest_trade_date` 同款)。

## 9. 范围外 / 后续
- qlib close 数据目录独立化(拷 cn_data 进 guanlan)= 独立大项目,非本范围。
- 新建 guanlan 专属 GPU 环境 = 用户选复用 stocks 环境,不做。
- Spec 3:工作流 LSTM 升格(产 `var/dl_pred_lstm.parquet` 同契约,加进 `default_dl_sources()`)。
