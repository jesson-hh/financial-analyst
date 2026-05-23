# 数据 Pipeline — G:\stocks 数据流完整说明

> 本文档读者: 想搞清楚 financial-analyst 的数据从哪来 / 怎么更新 / 出问题怎么查的研究员.
>
> 一句话: **financial-analyst 只读, 不拉. 数据拉取在 G:\stocks 项目的 scripts/ 下**.
> 两个项目通过 Qlib bin + Parquet 共享磁盘.

## 一、目录布局 (single source of truth)

```
G:/stocks/stock_data/
├── cn_data/                          # 日线 Qlib bin
│   ├── calendars/day.txt             # 全市场交易日历
│   ├── instruments/all.txt           # 股票池 (~5500 只)
│   ├── features/<code>/              # 每只股票一个目录 (小写, e.g. sh600519/)
│   │   ├── open.day.bin              # OHLCV
│   │   ├── high.day.bin
│   │   ├── low.day.bin
│   │   ├── close.day.bin
│   │   ├── volume.day.bin
│   │   ├── pe_ttm.day.bin            # daily_basic 估值
│   │   ├── pb.day.bin
│   │   ├── ps_ttm.day.bin
│   │   ├── dv_ttm.day.bin
│   │   ├── total_mv.day.bin
│   │   ├── circ_mv.day.bin
│   │   ├── turnover_rate.day.bin
│   │   ├── factor_xxx.day.bin        # 34 因子 (rev_20 / vol_20 / ...)
│   │   └── sentiment_xxx.day.bin     # 情绪分
├── cn_data_5min/                     # 5min Qlib bin (~7 天滚动, TDX 源)
├── cn_data_1min/                     # 1min Qlib bin (历史, ZIP 源)
└── parquet/                          # 非时序数据
    ├── financials/                   # 财报
    ├── tdx_f10/                      # TDX F10 原始文本
    ├── tdx_f10_index.parquet         # F10 索引
    ├── institutional/                # 机构持仓
    ├── events/                       # 事件
    └── news/                         # 新闻
```

**Qlib bin 格式**: 每个 `.bin` 文件 = `[4-byte float32 start_index] + [float32 数组]`.
`start_index` 是该股票在日历里第一天的位置. NaN 填空隙.

详见 [`src/financial_analyst/data/loaders.py`](../../src/financial_analyst/data/loaders.py)
+ `db.py::QLIB_FIELD_MAP`.

---

## 二、单一入口原则 (2026-04-14 事故后强制)

> **⚠ 历史事故**: 同一个 "拉日线写 bin" 的逻辑在 4 个脚本里复刻, 其中一个忘了
> safe_merge_write 直接整体覆盖, 把 5500 只股票 pe_ttm / pb / ps_ttm / dv_ttm /
> total_mv / circ_mv / turnover_rate 的历史从 ~2500 天剪到 6 天. 根治: **日线只留
> 一个入口**, 其它全移到 `G:/stocks/scripts/_deprecated/`.

**每日更新只跑这两条**:

| 频率 | 脚本 | 来源 | 字段 | 走的写盘函数 |
|------|------|------|------|------------|
| 每日 | `G:/stocks/scripts/incremental_update_tushare.py` | Tushare API | OHLCV + pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate | `bin_writer.safe_merge_write` |
| 每周 | `G:/stocks/scripts/import_tdx_5min.py` | TDX 本地 `.lc5` | 5min OHLCV (`.lc5` 只保留 ~7 天, 必跑) | 同上 |

**全量回补** (首次建库 / 灾难恢复):
```bash
python G:/stocks/scripts/incremental_update_tushare.py --since 20160101
```

**禁止事项** (违反 → 复活事故):
- `G:/stocks/scripts/_deprecated/` 下的脚本**不要再引用**:
  - `pull_tushare_data.py`
  - `fast_ohlcv_update.py`
  - `daily_update.py`
  - `pull_valuation_history.py`
  - `import_tdx_daily.py`
- 不要新建日线拉取脚本. 要加字段就在 `incremental_update_tushare.py` 里扩 `BASIC_FIELDS`.
- 增量写入**必须**走 `src.data.bin_writer.safe_merge_write`. 裸 `write_bin` 只留给
  全量导入 (例如 ZIP 重建).

详细复盘见 [`G:/stocks/scripts/_deprecated/README.md`](file:///G:/stocks/scripts/_deprecated/README.md).

---

## 三、非每日数据 (按需跑)

### 财务报表 (季报 / 年报)
```bash
python G:/stocks/scripts/import_tdx_financial.py
# 数据从 TDX 本地: D:\app\new_test2\T0002\hq_cache\*.dat
# 输出: G:/stocks/stock_data/parquet/financials/
```

### TDX F10 (公司大事 / 龙虎榜 / 大宗 / 异动)
```bash
# 全市场预热 (~30 min/csi300)
G:/financial-analyst/.venv/Scripts/financial-analyst.exe news-collect \
    --universe csi300 --topk-v4 50 --limit 30
# 增量 (daily_tdx_cron 单一入口)
G:/financial-analyst/.venv/Scripts/financial-analyst.exe news-collect --cron daily_tdx_cron
```

F10 内容: 15 大类 (公司大事 / 财务摘要 / 龙虎榜 / 大宗 / 融资融券 / 主要财务 / 增减持
/ 研究报告 / ...). 完整列表 + 数据契约见 CLAUDE.md `tdx_f10` 段.

知名游资白名单 (赵老哥 / 宁波桑田路 / 孙哥 / 章建平 / 拉萨游资 等 30+ 见
`src/data/tdx_f10_collector.KNOWN_GAME_CAPITAL`. 可扩展.

### 分钟级历史回补 (从 ZIP)
```bash
# 一次性 — 把 G:/stocks/raw_data/zip/*.zip 解到 cn_data_5min/
python G:/stocks/scripts/export_minute_to_bin.py --freq 5min
python G:/stocks/scripts/export_minute_to_bin.py --freq 1min
```

---

## 四、配置 financial-analyst 读 G:/stocks 数据

```yaml
# G:/financial-analyst/config/loaders.yaml
qlib_binary:
  provider_uri:
    day:   G:/stocks/stock_data/cn_data
    5min:  G:/stocks/stock_data/cn_data_5min
    1min:  G:/stocks/stock_data/cn_data_1min
  region: cn
```

financial-analyst 第一次启动会 `qlib.init(provider_uri=PROVIDER_URI_MAP, region='cn')`,
之后 14-agent 都用 `D.features()` / `D.instruments()` 读. **零拷贝, 内存映射**.

验证读到了:
```bash
financial-analyst loaders
# → qlib_binary  day=G:/stocks/stock_data/cn_data (calendar=2010-01-04..2026-05-23, n=3892)
```

---

## 五、Tushare 配额管理

`scripts/incremental_update_tushare.py` 内置:
- HTTP (不是 HTTPS, 避免 Windows 系统代理 SSL 拦截)
- 自动 sleep 控速 (默认每接口 0.5s)
- 失败重试 3 次, 指数退避

每日 ~1500 次 API 调用. 免费等级 (积分 100) 限 60次/分钟, 不会触发. 若要跑全 5500
只股票 + 财报字段 (~5500 × 3 = 16500 次), 升级到积分 5000+.

监控:
```bash
# 看上次跑的耗时 + API 调用数
tail G:/stocks/logs/incremental_update.log
```

---

## 六、数据 Pipeline 监控

### IC 衰减监控 (自动)
```bash
# cron / scheduled-tasks
python G:/stocks/strategy/factors/ic_monitor.py
# 扫 strategy/factors/*.csv, 衰减 > 阈值的因子追加到 pitfalls.md + log.md
```

输出: 因子在最近滚动窗口 ICIR 跌破阈值 → 写告警. financial-analyst 下次跑研报时
`bear-advocate` retrieval 命中 pitfall, 自动避雷.

### 数据质量自检
```bash
python G:/stocks/src/data/data_checker.py --code SH600519
# 输出: missing days / NaN ratio / outliers / 与 Tushare 重新拉一次对比
```

定期跑这个能尽早发现 "增量写盘出问题" 类故障.

### 增量更新失败告警

`incremental_update_tushare.py` 异常 → 写入 `G:/stocks/logs/incremental_update.log`
+ retcode != 0. Windows 任务计划 / cron 应捕获 retcode 报警.

---

## 七、跨项目协作约定

| Repo | 职责 | 写权限 |
|------|------|--------|
| **G:/stocks** | 数据 ingestion + 量化研究 (因子挖掘 / 模型训练 / 回测) | 写 `stock_data/` |
| **G:/financial-analyst** | Agent swarm + UI 后端 + 报告生成 | 只读 `stock_data/`, 写自己的 `out/` + `memories/` |

**别在 financial-analyst 里加数据拉取脚本**. 任何 ingestion 都进 G:/stocks/scripts/.

跨项目共享的"经验" (factor_insights / pitfalls / rating_system 等 markdown):

- G:/stocks 是 source-of-truth (`G:/stocks/strategy/`)
- G:/financial-analyst/memories/ 是 per-agent 副本 (按 agent 视角拆分)
- 漂移检测脚本: `G:/financial-analyst/scripts/audit_experience_files.py`

```bash
python G:/financial-analyst/scripts/audit_experience_files.py
# 输出: strategy 各文件 vs memories 各文件 mtime 差异 / size 差异. 提示 source-of-truth 修哪边
```

详细映射表见脚本里的 `CANONICAL_MAP`.

---

## 八、性能 / 容量基线

| 指标 | 数值 (2026-05-23 单机 G:/) |
|------|------|
| `cn_data` 全量大小 | ~25 GB |
| `cn_data_5min` ~7 天 | ~1 GB |
| `cn_data_1min` 历史 | ~80 GB (按需) |
| `parquet/financials` | ~3 GB |
| `parquet/tdx_f10/` 全市场预热 | ~5 GB |
| 单次研报读盘 (1 个 code, 含 factor 计算) | <500 ms |
| 全市场 v4 ranking (5500 只, 现场训 LGB) | ~3 min |

128 GB 物理内存机器, financial-analyst 限 50 GB. `mem_guard` 模块在
`src/financial_analyst/utils/mem_guard.py` 防止 5min 数据全量加载触 OOM.

---

## 九、常见错误

### Q: `D.features() returned empty DataFrame`
A: 1) `qlib.init` 没跑 / `provider_uri` 错; 2) 该股票目录不存在 (Windows reserved
   name 如 CON/PRN 走 `_qlib_CON` 前缀, 见 `bin_writer.code_to_fname()`); 3) 日期
   范围超出 calendar.

### Q: `pe_ttm.day.bin` 文件大小只有 24 字节
A: 历史被覆盖事故复活? 立刻 `git log --since 30-days` 看哪个脚本动了 bin_writer.
   恢复:
```bash
python G:/stocks/scripts/incremental_update_tushare.py --since 20160101 --fields pe_ttm
```

### Q: TDX 5min 数据缺最近几天
A: 1) 没跑 `import_tdx_5min.py` (每周必须); 2) TDX 客户端关了; 3) `.lc5` 文件超过
   ~7 天会被 TDX 自动删, 错过窗口只能从 5min 全量 ZIP 重导.

### Q: `tushare TushareError: 您每分钟最多访问该接口 N 次`
A: 调高 `incremental_update_tushare.py` 的 `SLEEP_BETWEEN_CALLS` 或升级 Tushare 积分.

---

## 十、相关阅读

- [`zero_to_report.md`](zero_to_report.md) — 第一次跑 financial-analyst
- [`14_agents.md`](../architecture/14_agents.md) — agent 怎么用这些数据
- [`data_ingest.md`](../data_ingest.md) — financial-analyst 端自带的 CSV / Tushare ingester (脱离 G:/stocks 跑)
- [`G:/stocks/scripts/_deprecated/README.md`](file:///G:/stocks/scripts/_deprecated/README.md) — 数据覆盖事故复盘
- [`G:/stocks/CLAUDE.md`](file:///G:/stocks/CLAUDE.md) — G:/stocks 项目主索引
