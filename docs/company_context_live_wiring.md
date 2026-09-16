# 公司情绪、股东披露与自有持仓接线

2026-09-08 完成实时补充。入口仍为公司证据包及工作空间研究准备工具：
`build_evidence_pack` 的 `company_facts`、`sentiment`、`holding`；新增查询不经过
stocks 导入器、不启动 Shell/CLI、不写共享数据库或持久缓存。证据包及其工作空间
附件沿用调用方已有的私有归档路径。

| 字段 | 内容与来源 | 缺失含义 |
|---|---|---|
| `company_facts.ownership_live` | 东财股东研究实时页面接口：十大股东、十大流通股东、股东户数 | `empty` 为当前查询无结果；`unavailable` 为接口或结构失败 |
| `sentiment.news_evidence` | 东财新闻搜索，代码精确命中的有界第一页原文 | 原文可供 Agent 判读；不是已生成的情绪分数 |
| `sentiment.tag/read` | 当日已有的共享情绪判读 | `null` + `judgment_status=not_run` 表示尚未判读，不能写成中性 |
| `sentiment.market_context` | 当日已有市场情绪 | 仅市场背景，不代表该发行人情绪或事实 |
| `holding` | 用户自有持仓台账 | `not_configured` 为未开账；`not_applicable` 为已查但未持有；异常回放为 `unavailable` |

股东接口是二手数据展示，按报告期披露而非盘中持仓。分别保留 `report_date`、
`retrieved_at`、源链接、总股本/流通股本比例口径；本接口缺公告发布日期，因此
`disclosure_date=null`、`point_in_time=false`。不能用报告期假装当时已公开，也不
把当前接口加入严格历史研究。既有本地 F10 与实时披露并存；不同报告期不会静默覆盖。

新闻保留标题、摘要、`publish_ts`、媒体和链接；代码仅证明文章提及该公司，
可能仍是多公司市场汇总。`issuer_fact=false`，必须由 Agent 判断关联及情绪。
只查询有界搜索页，不承诺全新闻覆盖，也不把获取时间作为新闻发布时间。

Evidence Hub 现在透传这些状态。无数据、未配置、未持有不会得到可用证据 ID；
未持有不作为公司数据接口故障。公司情绪始终是 `context`，股东披露经
`company_facts` 保留为二手发行人披露资料。

## 实测

2026-09-08 09:19 +08，以下四个直接查询合计约 0.76 秒：

- `query_shareholder_disclosures("SH600519")`、`("SH600667")` 均 `ok`：报告期
  2026-06-30，分别取得十大股东 10 条、十大流通股东 10 条、股东户数 8 期。
- `query_company_news(code, limit=10)` 两票分别 10 条代码精确命中原文，页内最新时间
  2026-09-07 17:44；保持 `judgment_status=not_run`。
- SH600667 的三个实际 evidence section → Hub 路径已通过：ownership `ok`，
  sentiment `partial` / `tag=null` / 10 条原文，自有持仓 `not_applicable` / `held=false`，
  该持仓节点没有生成证据 ID。

可核对的源页面：[东财股东研究](https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/Index?type=web&code=SH600667)、
[东财公司新闻搜索](https://so.eastmoney.com/news/s?keyword=600667)。

回归覆盖精确主体拒绝、不同报告期不混合、缺公告日、非法响应/超时/空结果区分、
历史研究不拉当前股东接口、新闻不生成情绪、市场背景不填个股 tag、自有持仓状态
与 Hub 证据资格；测试使用临时输出根。
