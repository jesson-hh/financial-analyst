# guanlan_v2/strategy 版本戳 (PROVENANCE)

- 复制日期: 2026-06-05
- 源: fa-watch/research/strategy(代码/知识) + engine fork(L3 评分器) + G:/stocks(产物)
- 漂移哨兵: tests/test_strategy_provenance.py

| 文件 | KB | SHA256(16) | 角色 |
|---|---|---|---|
| `vendor/artifacts/fincast_daily_pred.parquet` | 296.4 | `4E56A6088FEBDAA5` | FinCast/FM(未来) |
| `vendor/artifacts/market_breadth_resid.parquet` | 44.4 | `BF323A373BE8F041` | R27 残差(未来) |
| `vendor/artifacts/monthly_mainlines_panel.parquet` | 9053.4 | `40AA691848220260` | L2 主线月度面板 |
| `vendor/artifacts/spot_2026-04-03.parquet` | 584.3 | `E656C48C47E687D5` | 名称/价快照(旧) |
| `vendor/artifacts/tushare_stock_basic.parquet` | 126 | `F8CBFD3F4EF93265` | 代码到名称/行业 |
| `vendor/artifacts/v4_ranking_latest.parquet` | 171.6 | `1A464B6BE03214C9` | v4 排名输出 |
| `vendor/knowledge/analyst_playbook.md` | 30.1 | `3A0D43726908C7E7` | L4 九视角 |
| `vendor/knowledge/pitfalls.md` | 52.3 | `2B133D8D98559090` | L5 排除+L3 共振 |
| `vendor/knowledge/rating_system.md` | 18.2 | `6DA8E5DF4E2D7FF2` | L5 五维+护盾 |
| `vendor/sentiment/volume_regime.py` | 2.1 | `D78410E2D036EF11` | L3 量能状态评分器(自包含 pandas) |
| `vendor/v4_ranking.py` | 23.3 | `32C04811C7284BC5` | code(L1+L5 原版) |

备注:计算留外部(py3.13 无 qlib);L2 主线读月度面板;L3 vol_regime 自包含 pandas,从引擎面板取 close/turnover 逐股算。cn_data 不复制。
