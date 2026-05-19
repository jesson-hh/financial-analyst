# Local News DB (v1.1)

`financial-analyst` ships a local SQLite news DB at `~/.financial-analyst/data/news.sqlite`. Populated from OpenCLI's eastmoney/sinafinance collectors. Queried automatically by `news-reader` / `f10-reader` sub-agents when generating reports.

## Setup

```bash
# Install OpenCLI (Node.js >= 21)
npm install -g @jackwener/opencli
```

## Collect

```bash
# Daily — collect latest 200 items of 快讯 + 龙虎榜 (default)
financial-analyst news-collect

# Specific sources
financial-analyst news-collect --sources kuaixun,longhu,sinafinance --limit 500

# Stock-specific (十大流通股东)
financial-analyst news-collect --sources holders --code SH600519
```

Recommended: run daily via cron / scheduled task at market close (~16:00).

## Query

```bash
# Last 7 days for a stock
financial-analyst news-query SH600519

# All stocks
financial-analyst news-query all --days 1

# Full-text search
financial-analyst news-query all --fts "茅台业绩"
```

## Stats

```bash
financial-analyst news-stats
# Output:
# Local news DB stats:
#   news: 12453
#   lhb: 847
#   holders: 1893
```

## Schema

| Table | Purpose | PK |
|---|---|---|
| `news` | 7x24 快讯 + 财经新闻 | id (source::ts::title) |
| `news_fts` | FTS5 over news.title/content | (virtual) |
| `lhb` | 龙虎榜 daily entries | (trade_date, code, reason) |
| `holders` | 十大流通股东 quarterly | (code, report_date, rank) |

## How sub-agents use it

When you run `financial-analyst report SH600519`:
- `news-reader` checks `news/SH600519/*.txt` drop-zone first
- If empty or sparse (< 5 files), augments with `NewsDB.query_news(code='SH600519', since_days=14)`
- Same for `f10-reader` + `NewsDB.query_lhb` / `query_holders`

No changes needed in sub-agent calls — augmentation is transparent.

## Privacy

Everything is local. No data sent anywhere. The SQLite file lives in your home dir under `~/.financial-analyst/data/`.

## Windows encoding gotcha (fixed in v1.2.1)

On v1.1.0 / v1.2.0, Windows users on a Chinese (GBK / cp936) console saw
mojibake (`���` etc) for all Chinese content in NewsDB. This was because
`subprocess` with `shell=True` ran the npm-installed `opencli.CMD` through
`cmd.exe`, which transcoded node's utf-8 stdout through the active console
code page before handing it back to Python.

v1.2.1 bypasses `cmd.exe` by calling `node <main.js>` directly, so utf-8
reaches Python untouched.

If you upgraded from an affected version, rebuild your NewsDB:
```bash
python -c "from financial_analyst.data.news_db import NewsDB; \
db=NewsDB(); c=db.conn; [c.execute(f'DELETE FROM {t}') for t in \
['news','lhb','holders','social_posts','hot_stocks','earnings_dates']]; \
c.commit(); db.close()"
financial-analyst news-collect --sources kuaixun,longhu,sinafinance --limit 500
```

Linux / macOS users were never affected.
