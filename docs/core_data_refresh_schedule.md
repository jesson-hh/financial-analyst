# P0 核心数据盘后生产链

## 根因

2026-09-04 现场审计确认，`v4`、DL provenance 与 PIT 同时停在
2026-08-28，不是再生器把新数据漏掉，而是上游 OHLCV/daily_basic 在该日后没有
任何活跃生产调度。9999 内的 regen 调度只在 `close ∩ pe_ttm` 水位前进后工作，
不能凭空生成上游行情。

## 唯一调度动作

工作日 18:30（香港本地时间）由 Windows Task Scheduler **直接启动 Python**：

```text
D:\app\miniconda\envs\stocks\python.exe
python scripts/run_scheduled_core_data_refresh.py --execute
```

股票与 PIT 包装器仍调用 Stocks 已有的安全入口：

```text
run_data_block_update.py --block auto_refresh \
  --sources cn_day,cn_daily_basic,fincast_daily_pred,pit_store
```

顺序由 Stocks runner 固定为 OHLCV、daily_basic、stock_basic（自动依赖）、
FinCast、资讯/政策全文、PIT 与可见性审计。该链成功后，包装器再启动仓内
`guanlan_v2.datafeed.index_refresh`，只追加既有 `399300.SZ` 的沪深 300 日线尾部。
两阶段共用单飞锁、`IgnoreNew` 与八小时硬顶；指数子进程另有 90 秒上限。
总回执写入
`var/core-data-refresh/latest.json`，日志写入同目录且不记录凭据值。
`GET /data/health` 的运维项 `core_data_refresh_scheduler` 会直接读取计划任务 XML，
逐项核验动作、参数、工作目录、交互用户、重入策略、补跑、时限与盘后工作日触发，
并附上最近一份收口后的运行回执。任务缺失、禁用、漂移或最近失败均不会标绿；
老版本回执没有指数阶段时显式显示 `pending_first_run`，不能以股票链成功冒充指数已更新。

## 沪深 300 日线追加范围

2026-09-05 的一次性补尾已把 `stock_data/parquet/etf_index.parquet` 从 5 月 29 日
补到 9 月 4 日，但当时没有增加日常调度。9 月 8 日新增的代码闭合该持续更新入口，
不会重新注册或修改 Windows 计划任务；既有任务下次执行会读取新包装器。

- 复用仓内 ETF 模块的 `Tushare index_daily` 单次查询。凭证通过 Stocks 现有
  `src/data/tushare_credentials.py` 读取，不恢复已退役的环境 token，也不复制到日志。
- 不调用原来的 `update_etf_index` 全表覆盖器；不扩大 ETF/指数清单，不能由此声称
  全指数/ETF 数据已接通。
- 交易日历可能预生成至年末，不能当作已落盘水位。默认完成上界取“当前盘后日历日”与
  `parquet/audit/cn_day_quality/latest.json` 的成功 `verified_through` 二者较早值。
  15:15 前不写当日；质量回执必须为已完成的 `pass`，带有效 run_id 和非未来审计时间。
  读取后到提交前若质量回执 SHA 变化则拒绝本轮追加。
  可用 `--through-session 2026-09-07` 进一步缩小上界，不能超过时钟或成功行情水位；
  无 `--execute` 时只读显示实际 `expected_session`，不会联网或创建回执。最大补尾 120 天。
  查询从原尾日前 30 天开始，只需一段重叠窗口。源端缺日、未来日期、错代码、重复日期、
  非法 OHLC/量额、少于 5 个重叠日或重叠价格/量额不一致都会失败且保留原文件。
- 只追加原尾日之后的日线，旧表所有行和字段保持原值。写入前备份、复核原 SHA，
  暂存文件读回验证后原子替换；失败回执区分原股票链已完成与指数追加失败。
- 数据旁的 `stock_data/parquet/audit/index_daily/<run>/` 保存备份与事务回执；
  调度目录 `var/core-data-refresh/index_daily/` 保存独立运行回执，`latest.json`
  投影指数尾日、追加行数及失败原因。上游错误只记录固定原因码，不记录响应或凭证值。

本次验收只运行隔离测试，不执行生产指数更新。首份实际生产成功回执出现前，Y5
保持“代码已接，等待首次生产验收”。股票链失败时不运行指数阶段；指数失败时总状态为
`partial_failed`，保留 `core_status=completed`。

2026-09-08 15:18 的生产前置只读检查：正式指数仍到 9 月 4 日，cn_day 的成功水位
为 9 月 7 日，虽然 9 月 8 日已经收盘且日历包含该日，单独验收仍限定为
`--through-session 2026-09-07`。当晚原核心链成功收口后，默认上界才随其成功回执前进。

本机曾验证 Task Scheduler 派生的 PowerShell/cmd loader 会冻结，因此计划任务的
Action 不经过任何 shell；注册脚本本身只在人工安装时运行。

## 安装与核验

先只读查看计划：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/install_core_data_refresh_task.ps1
```

确认后注册或精确更新同名任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/install_core_data_refresh_task.ps1 -Apply
```

安装器覆盖同名任务前会把原 XML 备份到
`var/core-data-refresh/task-backups/`。任务使用当前交互用户运行，确保 `G:` 映射和
本地凭据上下文可见；机器关机错过时由 `StartWhenAvailable` 补跑。
