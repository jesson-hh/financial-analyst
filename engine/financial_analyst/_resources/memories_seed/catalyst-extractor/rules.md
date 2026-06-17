# catalyst-extractor — 提取规则

## 催化类型判定优先级

1. **earnings** 优先级最高 — 业绩预增/预减/暴雷直接定方向, confidence=high
2. **policy** — 行业政策 / 监管表态 / 财政补贴公告, 注意区分"利好出尽"
3. **product** — 新产品发布 / 大单中标 / 技术突破, 注意虚假宣传
4. **M&A** — 重组停牌前后均有信号, 注意"未达 5% 不披露"
5. **macro** — Fed/CPI/原油 等宏观联动, 用于解释跟随式涨跌
6. **rumor** — 雪球热议 / 论坛传闻, confidence 必 low
7. **technical** — 涨/跌幅大但无新闻 → 超跌反弹 / 突破
8. **none** — 既无新闻也无技术信号, 大概率是板块情绪带动

## 经验规则

- **3 日内 ≥2 条同向催化** → confidence high; 单条不一致 → medium
- **公告与股价反向** (利好但跌, 利空但涨) → 注意"利好出尽"或主力反向出货
- **新闻发布 ≥ 24h 才涨** → 大概率主力提前布局, 信号偏好
- **盘中突涨 + 24h 内无新闻** → 多半是游资接力 (rumor 或 technical)

## 输出约束

- 每只股最多引用 3 条 cited_news_titles
- summary 不超过 60 字
- direction 必填 (bullish / bearish / neutral)
- 没新闻明确说 "near-term news_db cache empty"
