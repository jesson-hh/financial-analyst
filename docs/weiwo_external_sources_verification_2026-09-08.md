# 帷幄外部产业数据接线核验（2026-09-08）

本次改动只负责 `strategic_live.py` 与新增 `official_futures_live.py` 的即时接口查询。没有访问或修改 `G:\stocks`，不依赖股票数据库，不建立持久行情库，不使用任何付费凭据。结果仍由原有工作空间工具保存为本公司的私有资产。

## 台系月营收

已接官方 OpenAPI → 官方 MOPS CSV → 单公司 MOPS API 的回退链。OpenAPI 出错、返回 HTML 或不符结构时尝试同市场 CSV；返回行保留 `source_url`、`source_format`。失败主源保留 HTTP 状态和警告，因此“备源成功”不会掩盖主源故障。历史月份必须吻合请求月份。

一手来源：

- [政府资料开放平台：上柜公司每月营业收入汇总表](https://data.gov.tw/dataset/56510)，列明资料字段、每月更新、免费开放。
- [TPEx OpenAPI 目录](https://www.tpex.org.tw/openapi/)。
- [TPEx OTC 月营收 API](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O)。
- [MOPS OTC CSV](https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv)，另有同目录 `t187ap05_L.csv`（上市）、`t187ap05_R.csv`（兴柜）。

实际查询（香港时间 09:18）：台燿 `6274`、博智 `8155`、台积电 `2330` 均从官方 API 成功返回；资料月份均为 **2026-07**，营收分别为 5,908,074、557,144、467,580,548 千元新台币。该时点的最新汇总表不是 8 月营收，不把当天查询时间冒充数据月份。OTC CSV 与兴柜 CSV 实测 HTTP 200，分别 165,768、68,085 字节。故障切换另用可复现模拟检验，未人为干扰官方服务。

## 国内商品期货

当前每个请求代码返回两层独立观测：新浪最新可得报价在主行；交易所已结算日数据在 `official_daily`，同时提供 `official_daily_rows`。新浪缺失时，主行可回退为 `cn_commodity_futures_daily`，明确 `official_daily_not_realtime` 并标为 `partial`，不补造实时最新价。

| 官方源 | 已验证通路 | 本次实测 |
|---|---|---|
| 上期所 SHFE | `https://www.shfe.com.cn/data/tradedata/future/dailydata/kxYYYYMMDD.dat` | 2026-09-07 HTTP 200，122,809 字节；RB/CU 已返回结算、量、OI |
| 上海国际能源 INE | `https://www.ine.cn/data/tradedata/future/dailydata/kxYYYYMMDD.dat` | 2026-09-07 HTTP 200，27,546 字节；SC 已返回 |
| 郑商所 CZCE | `https://www.czce.com.cn/cn/DFSStaticFiles/Future/YYYY/YYYYMMDD/FutureDataDaily.txt` | 2026-09-07 HTTP 200，37,944 字节；TA 已返回 |
| 广期所 GFEX | 官网 `POST http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList`，固定日期及 `trade_type=0` | 2026-09-07 HTTP 200，17,417 字节；LC/SI/PS 已返回。**HTTP 未加密，保留黄色** |
| 大商所 DCE | 官方站及原日行情入口探针 | HTTPS 连接 EOF；HTTP 超时。官方适配未接通，`coverage=not_connected`；新浪 M 等仍可查询 |

一手页面：

- [上期所日周数据](https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/?query_date=20250903&query_options=1&query_params=kx&query_product_code=ao_f)：说明交易期数显示后才是结算后数据，量/OI 单位是“手”、单边计算。
- [上海国际能源交易中心产品页](https://www.ine.cn/products/futures/metal/)：区分延时行情与日交易快讯，含报价与数量单位说明。
- [郑商所实际日行情原文](https://www.czce.com.cn/cn/DFSStaticFiles/Future/2026/20260907/FutureDataDaily.txt)。
- [广期所日行情页面](http://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml)与[官方接口调用脚本](http://www.gfex.com.cn/gfex/xhtml/js/allApi.js)：核对了上述 POST 路径和 `trade_date`、`trade_type`、`variety` 参数。

额外核验及修正：

- 上期所/能源中心当天日文件盘中也会返回 200。09-08 09:14 的 `o_year_num`、`o_trade_day`、`o_total_num` 为空，收盘与结算价亦为空；09-07 文件交易期数齐全并含结算值。实现拒收未结算文件并查最近已结算日，尝试记录保留 `not_yet_settled`。广期所当日文件仅返回“总计”行时也如此处理。
- 连续代码如 `RB0` 从官方同品种有效合约中选择正持仓量最大的合约，同时保存 `actual_contract` 和选择规则；没有有效 OI 就不伪造主力。**该规则不承诺等于新浪连续合约选择。**
- 郑商所三位交割年月按报告日期解释；`TA2701` 可匹配 2026 日报中的 `TA701`，`TA1701` / `TA3701` 不会被截位后误匹配。
- 修复了原新浪解析中成交量与持仓量颠倒的问题。由[新浪报价页](https://finance.sina.com.cn/futures/quotes/RB0.shtml)、[新浪 stockhq.js](https://stock2.finance.sina.com.cn/futures/page2012/js/stockhq.js)与[新浪显示模块](https://n.sinaimg.cn/finance/futures/moudler20200421.js)核对：前置代码后 `amount=arr[14]`、`volume=arr[15]`，故原始字段应为持仓 index 13、成交量 index 14；盘中官方 OI/量也与这一映射对应。
- 保留 HTTP 403/429/格式异常而不继续换日期绕过访问拒绝；每所只查有限回看窗口。广期所 HTTPS 证书主机名不匹配，未关闭证书校验；其公开 HTTP 路径已按官网实际用法接入并显式标记 `transport_security=unencrypted_http`。

09:23 广期所实测：`LC0 → LC2701`，结算 141,580，成交 147,974 手，OI 394,996 手；`SI0 → SI2611`，结算 8,735，成交 196,202 手，OI 314,419 手；`PS0 → PS2611`，结算 37,665，成交 77,260 手，OI 115,831 手。全部为 **09-07 日数据**。新浪当日行情和它们分别保存日期，不做跨时点覆盖。

## 验证与接入说明

定向回归：`tests/test_strategic_live.py`、`tests/test_official_futures_live.py`，29 项通过。覆盖 CSV 故障切换/HTML 拒收、官方盘中未结算回退、403 不重试、合约选择与郑商所年份、量/OI 字段含义、空实时源降级和官方 HTTP 标记。

原 `query_cn_commodity_futures(symbols, limit)`、`query_tw_monthly_revenue(company_ids, year, month, limit)` 签名保持可兼容，原工作空间保存路径无需切换。工作空间摘要及工具说明应同时展示“最新报价 + 已结算日数据”、`actual_contract`、`coverage`、来源与日期，广期所 HTTP 与大商所缺口继续黄色。当前新增日数据是按需查询的单日快照，尚不是仓单序列、历史期限曲线或交易所实时行情授权。
