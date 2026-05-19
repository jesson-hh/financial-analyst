# Xueqiu (雪球) Setup for v1.2

xueqiu commands in OpenCLI are **cookie-mode** — they reuse your Chrome's xueqiu.com login. Setup steps:

## 1. Install OpenCLI Chrome extension

The extension lets opencli drive your real Chrome session (cookies, JS execution, login state).

**Option A — Chrome Web Store (recommended):**
Install **OpenCLI** from the [Chrome Web Store](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk).

**Option B — Manual install:**
1. Download `opencli-extension-v{version}.zip` from [GitHub Releases](https://github.com/jackwener/opencli/releases).
2. Unzip it, open `chrome://extensions` in Chrome, and enable **Developer mode**.
3. Click **Load unpacked** and select the unzipped folder.

Sanity-check with `opencli doctor` (the OpenCLI binary's own doctor, not `financial-analyst doctor`) before moving on — it should confirm the bridge is connected.

## 2. Log into xueqiu.com in Chrome

Open https://xueqiu.com and sign in (Chinese phone / WeChat / email — anything works).

## 3. Verify with doctor

```bash
financial-analyst doctor
```

Expected output (relevant lines):
```
OpenCLI:
  v opencli on PATH: /usr/local/bin/opencli
  v opencli eastmoney kuaixun: working
  v opencli xueqiu hot-stock: working (cookie OK)
```

If "Chrome ext not installed / not logged in" instead — re-do steps 1-2.

## 4. Collect

```bash
# Retail sentiment for one stock (recommended daily)
financial-analyst news-collect --sources xueqiu-comments --code SH600519 --limit 50

# Heat ranking across all stocks (daily snapshot)
financial-analyst news-collect --sources xueqiu-hot --limit 50

# Upcoming earnings dates for a stock
financial-analyst news-collect --sources xueqiu-earnings --code SH600519
```

## 5. Multi-source one-shot

Combine public + cookie-mode in one call:

```bash
financial-analyst news-collect \
  --sources kuaixun,longhu,xueqiu-hot,sinafinance \
  --limit 200

# Then per-stock:
for code in SH600519 SZ000858 SH601318; do
  financial-analyst news-collect --sources xueqiu-comments,xueqiu-earnings --code $code
done
```

## What goes where in NewsDB

| OpenCLI source | NewsDB table | Used by |
|---|---|---|
| `xueqiu_comments` | `social_posts` | whale-analyst (retail sentiment) |
| `xueqiu_hot_stock` | `hot_stocks` | morning-brief (cross-validate hot) |
| `xueqiu_earnings` | `earnings_dates` | report-writer (catalyst calendar) |

## How whale-analyst uses social_posts

Without xueqiu, whale-analyst sees only OBV / VR / MFI / board_score / vol_regime — all quantitative.

With xueqiu, every report on a stock additionally pulls last-7-day discussion from NewsDB:
- Total likes + comments (engagement proxy)
- Top 5 posts by engagement (sample content)

This goes into whale-analyst's prompt as a "retail sentiment" section. The LLM can then say e.g. "散户讨论高涨, 5/15 一条点赞 800 的帖子说...".

## Limitations

- **Rate limits**: xueqiu may throttle if you scrape too fast. The doctor command pulls only `--limit 1` for the smoke test.
- **Cookie expiry**: Chrome occasionally rotates cookies. Re-login if `doctor` fails.
- **Profile selection**: if you have multiple Chrome profiles, run `opencli profile use <name>` first.
- **Windows quirks**: ensure no Chrome instances are crashed — `opencli doctor` may help.
