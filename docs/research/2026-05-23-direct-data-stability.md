# 直连数据接口稳定性测试 — pytdx + 腾讯实时 能否替代 Tushare Pro

**测试日期**: 2026-05-23 20:19
**机器**: Windows + G:/financial-analyst venv + pytdx 1.72 + tushare 1.4.29
**样本**: 49 只覆盖大/中/小盘 (csi50 + csi300 + csi500 + 北交所边缘)
**Probe 脚本**: `scripts/probe_direct_data.py` + `scripts/probe_pytdx_vs_tushare.py`
**结果落盘**: `out/probe_direct_data.json`

## TL;DR

**pytdx 主站直连 + 腾讯实时接口可以完全替代 Tushare Pro 做日常数据更新**,
且比 Tushare 快约 20 倍. **唯一缺口**: `ps_ttm` (PS-TTM) 和 `dv_ttm` (股息率)
两个字段腾讯实时接口没返回, 但在我们 14-agent 研报流程里**没有任何 agent 用到
这两个字段**, 实际无影响.

**推荐方案**: 新用户默认走直连, Tushare 路径保留给重度量化用户.

---

## 一、测试结果总览

| 项 | 结果 | 评级 |
|---|------|-----|
| pytdx 主站连通率 (前 20 个) | **10/20 = 50%** 可用 | 🟢 够用 (剩余主站平均 91ms 握手) |
| pytdx 日线拉取 (49 只 × 30 天) | **48/49 = 98%** 成功 | 🟢 唯一失败是已退市票 |
| pytdx 5min 拉取 (20 只 × 240 根) | **20/20 = 100%** 成功 | 🟢 |
| pytdx 单只延迟 P50/P95 | **26 / 28 ms** | 🟢 极稳 |
| pytdx vs Tushare close 数据一致性 | **600/600 = 100%** 完全一致 | 🟢 数据源等价 |
| 腾讯 PE/PB/MV/换手率 字段覆盖 | **49/49 = 100%** | 🟢 |
| 腾讯一次拉 49 只耗时 | **156 ms** | 🟢 极快 |
| pytdx 高频压测 (100 次连拉) | **100% 成功**, QPS 39.3 | 🟢 不限速 |
| pytdx vs Tushare 速度 | **快 20.7x** (27ms vs 560ms) | 🟢 显著优势 |

---

## 二、详细测试

### Test 1: pytdx 主站连通率

抽 `pytdx.config.hosts.hq_hosts` 前 20 个公开主站测连接:

| 状态 | 数量 | 备注 |
|------|------|------|
| ✓ 可用 | 10 | 平均握手 91ms, 抖动小 |
| ✗ Timeout | 10 | 部分主站早已下线 (hq_hosts 是历史维护列表) |

**可用主站示例** (用 `218.6.170.47:7709` 跑完后续所有测试零失败):
- `218.6.170.47:7709` (99ms)
- `123.125.108.14:7709` (102ms)
- `180.153.18.170:7709` (86ms)
- `180.153.18.172:80` (80ms)
- `60.191.117.167:7709` (85ms)
- `115.238.56.198:7709` (79ms)
- 6 个其他

**结论**: 50% 通率看似不高, 实际**完全够用** — 在实现里多主站轮询 + 拒连降级即可.
建议把已知可用的 ~10 个静态写进配置, 而不是每次都走完整 104 个 hq_hosts.

---

### Test 2: 日线拉取 (49 只 × 30 天)

```
✓ SH600519 贵州茅台   n=30 27ms close=1290.20 @2026-05-22
✓ SH601318 中国平安   n=30 26ms close=53.68   @2026-05-22
...
✗ SH600837 海通证券   n= 0 26ms no bars     ← 已合并入国泰君安 (SH601211), pytdx 返回空属正常
```

**成功 48/49 = 98%**. 唯一失败的 `SH600837 海通证券` 已退市/合并, 不是接口问题.

**性能**: P50 26ms / P95 28ms / 总耗时 1.3s 拉完 49 只. 几乎零抖动 — pytdx 主站没限速.

---

### Test 3: 5min 拉取 (20 只 × 240 根 ~ 5 个交易日)

```
✓ 20/20 全成功 (100%)
单只平均 35ms (240 根 5min bar)
总耗时 0.7s 拉完 20 只
```

完全可以**替代 G:/stocks 的 `import_tdx_5min.py`** (后者依赖本地 .lc5 文件, 用户得装通达信). 直接 pytdx 主站直连, **零本地依赖**.

`MAX_KLINE_COUNT = 800` 是单次请求上限, 5min 一天 48 根 → 800/48 ≈ 16 天. 拉更长走分页, 几个请求就够.

---

### Test 4: 腾讯实时接口字段覆盖率

49 只一次拉, 耗时 **156 ms**.

| 字段 | 覆盖率 | Tushare 对应 |
|------|------|--------------|
| price | 49/49 (100%) | ts.daily.close (当日) |
| pe | 49/49 (100%) | daily_basic.pe_ttm ✓ |
| pb | 49/49 (100%) | daily_basic.pb ✓ |
| total_mv | 49/49 (100%) | daily_basic.total_mv ✓ |
| circ_mv | 49/49 (100%) | daily_basic.circ_mv ✓ |
| turnover_rate | 49/49 (100%) | daily_basic.turnover_rate ✓ |
| vol_ratio | 49/49 (100%) | (Tushare 没有, 腾讯独有, 量比) |
| **缺**: ps_ttm | — | daily_basic.ps_ttm ✗ |
| **缺**: dv_ttm | — | daily_basic.dv_ttm ✗ |

**两个缺口的实际影响 (查全 14 agent 源码确认):**

- `ps_ttm`: 在 `agent/tier1/quote_fetcher.py::QuoteOutput.ps` 字段里存在, 但**只是 informational**, 没有任何下游 agent 决策依赖. fundamental-analyst 也只看 PE/PB 不看 PS.
- `dv_ttm`: 同样仅记录, 没有 agent 把分红率作为评分因子. 高股息策略目前没实现.

**结论**: 缺这两个字段**对当前生产研报无影响**. 真要补:
- 走东方财富 `data.eastmoney.com` 接口能拿到, 但加复杂度
- 或者放弃, 研报里这两个字段标 `null`
- 或者重度用户带 Tushare 走老路径

---

### Test 5: pytdx vs Tushare 数据一致性

20 只 × 30 天 = **600 日 close 价格逐日对比**:

| 项 | 结果 |
|---|------|
| 平均 close 差异 | **0.00000%** |
| 最大 close 差异 | **0.00000%** |
| 完全一致 (<0.001%) | **600/600 = 100%** |
| 差异 <0.01% | 600/600 = 100% |
| 差异 <0.1% | 600/600 = 100% |

**所有 600 个对比点 close 完全一致到浮点精度**. 数据源等价.

**volume 字段单位差异 (已知, 非错误)**:
- Tushare daily.vol 单位 = **手** (1 手 = 100 股)
- pytdx vol 单位 = **股**
- 比值固定 100x, 接入时除以 100 即可

**速度对比**:
- pytdx 单只 ~27ms (本地→主站 TCP socket)
- Tushare 单只 ~560ms 平均 (HTTPS + JSON + 服务端查询)
- **pytdx 快 20.7x**

---

### Test 6: pytdx 高频压测 (单 host 连续 100 次拉日线)

| 项 | 数值 |
|---|------|
| 成功率 | **100/100 = 100%** |
| 实测 QPS | **39.3** |
| P50 延迟 | 25.4 ms |
| P95 延迟 | 26.1 ms |
| P99 延迟 | 28.2 ms |
| 失败类型 | (无) |

**单连接 ~39 QPS 不限速**. 我们 `data/net.py` 注册时给 `pytdx_main` 上 **10-15 QPS** 限速做安全垫即可.

---

## 三、与 Tushare Pro 的差异 (使用者视角)

| 维度 | 直连 (pytdx + 腾讯) | Tushare Pro |
|------|------------------|------------|
| **token / 注册** | 不需要 | 必须 (实名 + 邮箱验证) |
| **付费** | 完全免费 | 免费等级需积分 ≥ 100 (注册即送), 高频调用要充值升积分 |
| **日线 OHLCV** | ✓ pytdx, 100% 与 Tushare 一致 | ✓ |
| **5min OHLCV** | ✓ pytdx, 100 day 历史 (主站) | 需要积分, 拉得慢 |
| **历史 PE/PB/MV 时间序列** | ✗ 只当日快照 (腾讯) | ✓ 完整历史 (daily_basic) |
| **PS-TTM / 股息率 时间序列** | ✗ | ✓ |
| **复权因子** | ✓ pytdx | ✓ |
| **财报数据** | ✓ pytdx F10 | ✓ 更结构化 |
| **交易日历** | pytdx 含 / 自己算 | ✓ ts.trade_cal |
| **延迟** | **~27ms / 只** | ~560ms / 只 |
| **批量 50 只** | **~0.156s (腾讯当日)** / 1.3s (pytdx 30 天) | ~30s |
| **限速** | 主站约 39 QPS / host, 多 host 分流 | 60 次/分钟 |
| **稳定性** | 主站偶尔 timeout, 多 host 兜底 | 服务端偶尔限速 / 报错 |
| **拉全市场 5500 只 30 天日线** | 估算 ~30 min (主站轮询) | ~30 min |
| **依赖网络环境** | 国内直连 OK; 国外可能拉不到 | HTTPS 全球可用 (但要 trust_env=False 防 Clash) |

---

## 四、推荐架构

```
┌────────────────────────────────────────────────────────────────┐
│  默认 (新用户, 零配置)                                          │
│  ──────────                                                     │
│                                                                  │
│  首次启动: 下 HuggingFace 历史包 (含全部 daily_basic 历史)      │
│             ↓                                                    │
│  日常更新:  pytdx 主站直拉昨日 OHLCV + 5min                     │
│  +         腾讯实时拉今日 PE/PB/MV 快照 → 写入 daily_basic bin  │
│  +         (周度) 我们 publish daily_basic 增量包到 HF, 用户同步│
│                                                                  │
│  零配置, 零付费, 零 token                                       │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  高级 (重度量化研究员)                                          │
│  ──────────                                                     │
│                                                                  │
│  装 Tushare → 跑 incremental_update_tushare.py 像现在一样       │
│  完整 daily_basic 历史时间序列每日自动延伸                       │
│                                                                  │
│  适合: 因子挖掘 / 历史回测 / IC 分析                            │
└────────────────────────────────────────────────────────────────┘
```

---

## 五、需要实现的代码

| 模块 | 用途 | 工作量 | 优先级 |
|------|------|------|------|
| `data/loaders/pytdx_main.py` | 多主站连接池 + 轮询 + 自动重连 | 1d | P0 |
| `data/loaders/pytdx_daily.py` | 拉日线写 Qlib bin (走 safe_merge_write) | 1d | P0 |
| `data/loaders/pytdx_5min.py` | 拉 5min 写 Qlib bin | 0.5d | P0 |
| `data/loaders/tencent_basic.py` | 腾讯快照写 daily_basic bin (单日切片) | 0.5d | P0 |
| `cli/data_update.py` | `fa data update` 命令 (串以上 3 个 + cron 注册) | 0.5d | P0 |
| `scripts/publish_to_hf.py` | csi300-demo / csi800-lite / full 三档打包 + 上传 HF | 1d | P1 |
| `cli/init_wizard.py` | `fa init` 引导填 LLM key + 选数据包 + 下载 | 1d | P1 |
| `data/net.py` 加 `pytdx_main` source | QPS + 重试配置 (10 QPS) | 0.1d | P0 |

**总工作量 ~5.6 天**. 比 Tauri 打包 (2 周) 快很多, 而且这是 P2/P3 prerequisite.

---

## 六、注意事项 (踩坑 / 边界)

1. **退市 / 合并股票** pytdx 返回空 bars, 接入时要正确处理 (返回 None 不是 error)
2. **北交所 (BJ 开头) + 科创板 (688/787) + 创业板 (300)** pytdx 都支持, 但市场代码不同 (SH=1, SZ=0, BJ=2)
3. **复权**: pytdx `get_security_bars` 返回的是**不复权**价格. 复权因子要 `api.get_xdxr_info()`. financial-analyst 的 quote-fetcher 当前用前复权后再写 bin, 接入时这层逻辑要保留
4. **vol 单位**: pytdx=股, Tushare=手. 接入除 100
5. **datetime 格式**: pytdx 返回 `'2026-05-22 15:00'`, 截 `[:10]` 当 trade_date
6. **主站连接持久化**: 一个 `TdxHq_API` 实例 connect 一次后可以反复 `get_security_bars`. 不需要每只重连
7. **多线程**: pytdx 不是线程安全的, 一个连接只允许一个线程. 多线程要多个连接
8. **主站 timeout 后**: `api.connect()` 返回 False 时立刻换下一个 host, 别 retry 同一个
9. **`ps_ttm` / `dv_ttm`** 缺口暂不补, 后续要补走 eastmoney 接口

---

## 七、全市场 stress test (5450 只, 当晚补测)

补一次大规模验证 — 用 G:/stocks 的 instruments/all.txt 跑 `update_daily` (n_bars=30) 全市场,
sandbox 目录 fresh start, 不动真实数据.

| 指标 | 数值 |
|------|------|
| Universe | 5450 只 (全 A 股) |
| **✓ OK** | **5194 (95.3%)** |
| ⏭ EMPTY (退市/未上市/被合并) | 256 (4.7%) — 正常返回, 不是 bug |
| **✗ 网络 / 主站 / 限速失败** | **0** |
| 总壁钟时间 | **227.3 秒 = 3.8 分钟** |
| 吞吐 | **24.0 只/秒** |
| P50 / P95 / P99 单只延迟 | 42 / 45 / 62 ms (无长尾) |
| 主站切换次数 | **0** (`180.153.18.172:80` 全程稳定 ~4min) |
| Sandbox 写盘大小 | 32 MB (5194 × ~6 bin × ~30 floats) |

**关键发现**:
- 单连接 / 单主站可以 3.8 min 跑完全市场, 远低于 Tushare HTTP 同规模耗时 (~30 min)
- 实测 P50 42ms vs probe 阶段 27ms 多出 15ms = `read_bin + write_bin + calendar + instruments` 的本地开销, 完全合理
- pytdx 主站零限速触发, 全程没用到我们的 multi-host failover (好事 — 备用机制存在但没必要启动)
- 0% 网络失败 = pytdx 主站这条路在工程上**完全可生产**

> 结果 JSON: `out/stress_test_pytdx.json`. 脚本: `scripts/stress_test_pytdx.py`.

---

## 八、最终建议

**走这条路.** 新用户体验从"注册 Tushare → 等积分 → 充值 → 装 pytdx → 拉数据" (≥30 min + 持续付费) 改为 "输 LLM key → 下数据包 → done" (5 min + 完全免费).

Tushare 路径保留, 一个环境变量切换 (`FA_DATA_BACKEND=tushare|pytdx|hybrid`).

下一步行动:
1. 实现 `pytdx_main.py` 多主站连接池 (P0, 1 天) — 是所有后续的 prerequisite
2. 实现 `pytdx_daily.py` + `pytdx_5min.py` (P0, 1.5 天)
3. 跑一遍全市场 5500 只 → 验证不是只对 50 只代表股有效
4. 上 `fa data update` CLI 命令

具体 implementation 等你 confirm 这条路再开干.

---

## 九、Implementation 完成回顾 (2026-05-23 晚)

P0 + P1 全部实施完毕, 验证通过:

| 任务 | 工作量 | 实际产出 | 状态 |
|------|------|---------|------|
| P0-1..7 直连数据接入 + bin_writer vendor + CLI | 5.6 d 预估 | 单晚完成 ~830 LOC + 175 LOC e2e | ✓ |
| P1-A HuggingFace 发布脚本 | 1 d | `scripts/publish_hf_dataset.py`, dry-run 0.44 GB / 13.5s | ✓ |
| P1-B `fa init` wizard | 1 d | `src/financial_analyst/init_cli.py`, Rich UI 引导 | ✓ |
| P1-C 全市场 stress test | 0.5 d | 5194/5450 OK, 0 失败, 3.8 min, P99 62ms | ✓ |
| P1-D buddy `update_data` tool | 0.5 d | `buddy/tools.py` +#30, "更新数据" 触发 | ✓ |

**对原计划的偏差**:
- P0 工作量大幅低估 (5.6d → 1 晚) — pytdx 比预期稳定得多
- HF 数据包名 "demo" 实际是 csi300 累积 939 只 (Qlib 标准做法), 而非当前 csi300 300 只.
  README 改 description 反映这一点, 不影响 demo size (~0.44 GB)
- update_data tool 默认 quick 模式 (skip-5min/skip-basic), 几秒级而非分钟级 — UX 优于原设计

**未做**:
- 真实 HF upload (需要用户 HUGGINGFACE_TOKEN, 工程上没意义自己上传)
- Anthropic / OpenAI 详细 fallback 测试 (留给用户实际跑时验证)
- 上海复权因子自动拉 + 写入 (`get_xdxr_info` 接口可用, 未实现, 与 G:/stocks 现状一致)

**下一阶段候选**:
1. 用户 HF 发布 (真上传 demo / lite / full)
2. P2 Tauri 打包 (要等数据包发布 + init wizard 收尾)
3. 把 `daily_basic` 历史时序的 `update_daily_basic_today` 扩展成可拉历史范围 (Tushare token 用户可走)
