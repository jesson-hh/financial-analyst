# Data Ingestion

`financial-analyst ingest` converts your CSV (or other future formats) into the
Qlib binary layout that `QlibBinaryLoader` reads.  This is the primary onboarding
path for users who do not have a pre-built Qlib data directory.

## Quick start

1. Edit `config/data_sources.yaml`:

   ```yaml
   sources:
     - name: my_data
       type: csv
       path: /path/to/your/*.csv
       code_col: ts_code
       date_col: trade_date
       date_format: "%Y%m%d"           # optional — omit for pandas auto-infer
       ohlcv_map:
         open: open
         high: high
         low: low
         close: close
         vol: volume                    # our 'vol' <- your 'volume' column
         amount: amount
       target: ~/.financial-analyst/data/my_data
   ```

2. Inspect (no writes):

   ```bash
   financial-analyst ingest --source my_data --dry-run
   ```

3. Convert:

   ```bash
   financial-analyst ingest --source my_data
   ```

4. Point the loader at the new data:

   ```yaml
   # config/loaders.yaml
   default: qlib_binary

   loaders:
     qlib_binary:
       provider_uri:
         day: ~/.financial-analyst/data/my_data
   ```

5. Run a report — uses your data now:

   ```bash
   financial-analyst report SH600519
   ```

## CSV Layouts Supported

### Long format (one row per code-date)

```csv
ts_code,trade_date,open,high,low,close,volume,amount
SH600519,20260515,1700,1720,1690,1715,10000,17150000
SH600519,20260516,1710,1730,1700,1725,12000,20700000
SZ000858,20260515,150,152,148,151,500000,75500000
```

Config:

```yaml
code_col: ts_code
date_col: trade_date
per_code_filenames: false
```

### Per-file format (one CSV per code, filename = code)

```
data/
  SH600519.csv
  SZ000858.csv
```

Each file:

```csv
trade_date,open,high,low,close,volume,amount
20260515,1700,1720,1690,1715,10000,17150000
```

Config:

```yaml
code_col: null          # not needed
per_code_filenames: true
date_col: trade_date
```

## Field Mapping

Our internal field names are `open / high / low / close / vol / amount`.  If your
CSV uses different column names, map them in `ohlcv_map`:

```yaml
ohlcv_map:
  open: opening_price
  close: closing_price
  vol: volume_traded
```

Only the fields you provide get written as `.day.bin` files.  Missing fields are
silently skipped — the loader will return `NaN` for those columns.

## Glob Patterns

`path` supports shell-style globs.  Multiple files are concatenated before writing:

```yaml
path: G:/data/part*.csv        # matches part1.csv, part2.csv, …
path: G:/data/**/*.csv         # recursive (Python glob ** semantics)
```

## Qlib Binary Layout Written

```
<target>/
├── calendars/day.txt           # sorted unique trade dates, YYYY-MM-DD
├── instruments/all.txt         # CODE<TAB>start_date<TAB>end_date
└── features/<code_lower>/<field>.day.bin
```

The binary format matches what `QlibBinaryLoader._read_bin()` expects:

- 4-byte header: `float32` encoding of the integer `start_index` (position in
  the calendar where this stock's data begins)
- Body: `float32` array, one value per trading day from `start_index` onward,
  `NaN` for days where no data was provided

## Reserved Sources (v0.4)

`type: akshare` and `type: yfinance` are stubbed but not yet implemented.  They
will support automatic download for A-share and international markets respectively.
Using them now raises `NotImplementedError`.

## Python API

You can also call the ingester directly from Python:

```python
from pathlib import Path
from financial_analyst.data.ingest import CsvIngester

ing = CsvIngester(
    path_glob="G:/data/*.csv",
    code_col="ts_code",
    date_col="trade_date",
    date_format="%Y%m%d",
)

# Inspect without writing
print(ing.discover())

# Convert
result = ing.convert(target_root=Path("~/.financial-analyst/data/my_data").expanduser())
print(result)
# IngestResult(instruments=42, dates=1200, fields=6, target=...)
```
