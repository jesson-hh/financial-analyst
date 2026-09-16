# 个股研究的行业资讯人审闭环

NewsRadar 在个股空间里只负责给出“同一行业赛道的候选资讯”，不会把标题命中当成公司事实。原始快照保持不可变；人工判断另存到当前工作空间的私有 hash-chain 账本。

使用顺序：

1. 调用 `ww_workspace_newsradar`，得到 `wa_*` 快照。
2. 调用 `ww_workspace_industry_review_list(snapshot_asset_id=...)`，查看候选和当前 `revision`。
3. 用户确认后调用 `ww_workspace_industry_review`，提交 `related`、`unrelated` 或 `uncertain`，并回填刚读取的 `expected_revision` 与唯一 `idempotency_key`。这是写操作，Console/Codex 均要求逐次确认；公共 MCP 仍服从既有写门。
4. 重新运行 `ww_workspace_company_research_prepare`。只有 `related` 行进入本次 `engine_evidence_pack.json`、`evidence.json` 和 `events.json`；其余状态不会出现在报告可引用载荷中。

边界：

- `related` 只表示“用户认为与本公司研究相关的行业背景”，始终是 `research_scope=industry_context`、`issuer_fact=false`，不能关闭发行人证据门。
- `unreviewed`、`uncertain`、`unrelated` 与无效候选一律不得进入公司报告。
- 账本绑定 workspace、公司代码、原始快照、candidate id 和候选内容 digest；跨空间、跨公司、修改后的候选不能复用旧决定。
- 写入使用 `expected_revision` 做 compare-and-swap，并以 `idempotency_key` 去重。账本每条记录含前序 hash；损坏时读取和报告均 fail closed。
- 公司准备包冻结当时的账本 revision/head。报告生成期间若审核状态变化，finalizer 会拒绝旧包，要求重新准备，避免已经改为 unrelated 的资讯仍被引用。
- 原始外部资讯即使被判定 related，仍是不可信数据；该决定不验证新闻内容真假。
