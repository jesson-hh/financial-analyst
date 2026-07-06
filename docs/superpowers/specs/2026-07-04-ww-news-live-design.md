# ww_news_live:agent 实时新闻拉取(现拉秒回,零新采集)设计

- 日期:2026-07-04
- 缘起:经验回滚总结需要 agent 拉实时新闻。用户三轮把设计推到最简:①别在观澜重造采集(不美观);②别依赖 stocks parquet 陈旧度;③agent 不该等管线 —— **调用时现拉秒级 API 就是实时**。
- 红线:准确不幻觉;失败诚实降级;回测 `mode=pit`(PIT 无前视)完全不碰。

## 1. 一句话

`ww_news_live(code, limit)` = 调用时现拉**观澜引擎已有**的两路秒级实时源(个股新闻 + 快讯),合并归一秒回;stocks 富 parquet(公告/政策)在场就顺带读、标截止期;`/seats/news?mode=live` 同装配器,落子图 live 泳道顺带升级。

## 2. 已定决策(用户逐轮确认)

1. **零新采集代码**:复用引擎现成生产函数 `news_pulse.fetch_stock_news(code)`(akshare 东财个股新闻,2~5s)与 `fetch_kuaixun()`(东财 7×24 快讯)——与 stocks 侧 `collect_stock_news_akshare` 调的是**同一公开 API**,不算重复功能;反而"子进程调 stocks"要在 stocks 新写 CLI 壳,代码更多,弃。
2. **不触发、不等待、不调度**:曾议的"子进程跑 news_pipeline / 定时刷新 / 按需等 1-3 分钟"全部否决。实时性由现拉保证。
3. **stocks 富 parquet = 可选加菜**:`G:\stocks\_news_staging\normalized\news_events.parquet` 存在就纯文件读(`pd.read_parquet`,不 import stocks 代码),按票过滤公告/政策条目并**诚实标"富层截至 X"**;不存在/读失败 → 该路静默空。其刷新归 stocks 侧,观澜不管。
4. **工具面 = 新增 `ww_news_live`**(语义干净,不混 `ww_news_search` 情绪搜索);帷幄工具四点同步(specs + CONSOLE_ALLOWED + _SYSTEM_PROMPT + 守护计数)。

## 3. 架构与改动面(共三处)

| 层 | 文件 | 改动 |
|---|---|---|
| 装配器 | `guanlan_v2/seats/news_marks.py::_assemble_live` | 从"仅 kuaixun headlines"升级为三路合并:①`fetch_stock_news(code)` ②`fetch_kuaixun()` 按票/名过滤 ③可选 parquet 公告/政策;归一现有 item 形状,标题去重,`ts` 降序截 `limit` |
| 帷幄工具 | `guanlan_v2/console/tools.py` | `news_live_impl(code, limit=20)` 薄壳调装配器;注册 `ww_news_live`(四点同步,计数 +1) |
| 测试 | `tests/test_news_marks.py`(扩) | 注入桩验三路合并/去重/降级;既有 live 两测不回归 |

**接口(公有契约)**:
- `assemble_news_marks(code, mode="live", limit=20, *, provider=None, stock_news_fn=None, kuaixun_fn=None, parquet_path=None)`(测试注入缝;`provider` 兼容保留)
- item 形状不变:`{ts, date, title, source, code, level, body_head}`;level:个股新闻/快讯命中本票→`stock`,快讯宏观→`macro`,parquet 公告→`event`、政策→`policy`
- 响应新增 `freshness: {pulled_at, rich_asof(parquet publish_ts 最大值|null), rich_available: bool}`
- `ww_news_live` 返回:`{ok, code, items[≤limit], freshness, note}`

## 4. 数据流

```
ww_news_live(code) / GET /seats/news?mode=live&code=
 → _assemble_live:
    ①fetch_stock_news(code)        # akshare 东财个股新闻, 现拉
    ②fetch_kuaixun() 本票/名过滤    # 东财7×24快讯, 现拉(90s进程内缓存)
    ③news_events.parquet 若在      # 公告/政策, 按 stock_codes 含本票或 is_policy, 近3日窗
 → 标题去重(①②同源重叠) → ts 降序 → limit → items + freshness
```

- 快讯按票过滤复用 `KuaixunNewsProvider` 现有 stocks 字段/名称匹配逻辑(名称经 `stock_basic.get_basic` 取,失败只按 code)。
- parquet 条目 `ts` 用 `publish_ts`;快讯当日无完整时戳时保留其时分或空(空→前端落最右 bar,既有行为)。

## 5. 诚实降级

| 情形 | 行为 |
|---|---|
| akshare 个股新闻失败/超时 | 该路空,note 记"个股新闻源不可用" |
| kuaixun 失败 | 该路空(既有 KuaixunNewsProvider 语义) |
| parquet 缺/读错 | `rich_available:false`,不读不编 |
| 三路全空 | `items:[]` + note,`ok:True` |
| 网络阻塞 | 各路走 `asyncio.to_thread`/现有线程封装,超时护 9999 loop |

## 6. 测试(注入桩,离线)

1. 三路合并:桩 ①返 2 条 ②返 2 条(1 条与①同标题)③parquet 临时文件 2 条 → items=5,去重生效,level 正确。
2. 排序+limit:ts 降序、截断。
3. 单路失败降级:①抛异常 → 其余两路仍出,ok:True。
4. parquet 缺席:`rich_available:false`,items 仅①②。
5. 既有两条 live 测(headlines 桩/失败空)不回归。
6. 真机烟测(有网):`ww_news_live("SZ000630")` 返真条目、`pulled_at` 当刻。

## 7. 验收

- 帷幄里 agent 调 `ww_news_live` 秒级返回当下新闻(真机);
- 落子图 live 泳道条目变富(快讯+个股新闻);
- 全量 pytest 绿;`mode=pit` 行为逐位不变(既有 8 测为证)。
