# Known Game-Capital Traders (R21-R26)

These are Chinese A-share game-capital traders ("游资") with strong identity signals on LHB. Tag any matching seat name with `trader_tag` in output.

## Tier 1 (national)
- 赵老哥 (Zhao Lao Ge) — anchor seats: 银河证券绍兴 / 中信建投上海高桥 / 中信建投杭州延安路
- 章建平 (Zhang Jian Ping)
- 拉萨游资 (Lhasa) — 拉萨东环路 / 拉萨团结路 / 拉萨金珠西路
- 宁波桑田路 (Ningbo Sangtian Lu)
- 上海超短帮

## Tier 2 (regional)
- 孙哥
- 欢乐海 — 欢乐海岸席位
- 玉兰路游资
- 方新侠
- 炒股养家
- 量子
- 葛卫东
- 徐翔系
- 北京帮
- 深圳帮
- 江浙帮

## Tier 3 (boutique)
- 益田路荣超商务中心
- 中泰齐河晏北路
- 国泰君安东方路
- 华泰证券上海武定路
- 平安证券深圳金田路

## Output rules
- When LHB seat name matches anchor pattern, set `trader_tag` = the trader's canonical name
- Aggregate same-trader buy/sell separately
- If unknown, leave `trader_tag` as null
