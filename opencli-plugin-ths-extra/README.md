# opencli-plugin-ths-extra

Extra 同花顺 (10jqka) commands beyond what opencli's built-in `ths`
adapter ships with. Ships with `financial-analyst` v1.7.2+.

## Install

```powershell
opencli plugin install file://G:\financial-analyst\opencli-plugin-ths-extra
opencli list | findstr ths-extra
```

Uninstall: `opencli plugin uninstall ths-extra`.

## Commands

| Command | Description | Output columns |
|---------|-------------|----------------|
| `ths-extra iwencai <question>` | 问财自然语言选股 | columns (pipe-joined, often empty if iwencai uses div headers), cells (pipe-joined row data) |
| `ths-extra fund-flow` | 个股资金流主力净流入排行 (`/funds/ggzjl/`) | code, name, price, change_pct, turnover_pct, inflow, outflow, main_net, total_amount |
| `ths-extra concept-board --mode new` | 新概念发布表 (`/gn/`) | board_code, board_name, release_date, num_stocks, change_pct |
| `ths-extra concept-board --mode rank` | 概念板块涨幅榜 (URL TBD, may return empty until URL re-verified) | same |

## Sample

```powershell
opencli ths-extra iwencai "PE 最低的 20 只白酒" -f json
opencli ths-extra fund-flow --page_no 1 --limit 20 -f json
opencli ths-extra concept-board --mode new --limit 30 -f json
```

## Requirements

- opencli >= 1.7.22
- Chrome session with valid 10jqka.com.cn cookie (set up via the
  opencli Chrome extension — same flow as xueqiu).

## Development

```powershell
# Symlink install (edits reflect on next opencli call)
opencli plugin install file://G:\financial-analyst\opencli-plugin-ths-extra

# After editing a .js file
opencli plugin update ths-extra   # reload
opencli ths-extra <cmd> --help
```

## DOM-selector debugging

The four commands DOM-scrape result tables; 10jqka changes CSS class
names every few months. If a command returns `[]`, run with
`--verbose --trace on` and inspect the trace artifact to see which
selector failed. Then update the candidate list at the top of the
relevant `.js` file.

## License

Apache-2.0 (same as parent `financial-analyst`).
