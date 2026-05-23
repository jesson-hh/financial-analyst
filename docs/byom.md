# Bring Your Own Models (BYOM)

`financial-analyst` is designed as a **framework**, not a fixed product. The 14 built-in sub-agents (Tier 1-4, see [`architecture/14_agents.md`](architecture/14_agents.md)) + LGB momentum model + Tushare loader are reference implementations. Anywhere you see a `Base*` ABC, you can plug in your own implementation.

## Five extension points

| Plug-in type | ABC | Built-in default | Registry |
|---|---|---|---|
| Data loader | `BaseLoader` | `TushareLoader`, `QlibBinaryLoader` | `config/loaders.yaml` |
| Quant model | `BaseModel` | `LGBMomentumModel` | `ModelRegistry.register()` |
| Sub-agent | `SubAgent` | 13 default agents | `SubAgentRegistry.register()` |
| News collector | `BaseNewsCollector` | (none — drop-zone only) | user-instantiated |
| F10 collector | `BaseF10Collector` | (none — drop-zone only) | user-instantiated |
| Knowledge base | `KnowledgeBase` | `LocalMarkdownKB` | user-instantiated |
| Data ingester | `BaseIngester` | `CsvIngester` | `config/data_sources.yaml` |

## Plugin discovery

Create `config/plugins.yaml`:

```yaml
load_at_startup:
  - G:/my_private_code/my_fm_model.py
  - ~/quant_lab/custom_loaders.py
```

Each listed Python file is exec'd at startup. Use it for top-level `ModelRegistry.register()` calls.

## Example: register your own FM cluster model

Create `G:/my_private_code/my_fm_model.py`:

```python
import torch
from financial_analyst.models import BaseModel, ModelRegistry


class MyFMCluster(BaseModel):
    def __init__(self):
        self.model = torch.load("G:/my_ckpts/fm_W10.pt", map_location="cpu")
        self.model.eval()

    def predict(self, code, asof):
        features = self._build(code, asof)
        with torch.no_grad():
            out = self.model(features)
        return {
            "score": float(out["score"]),
            "rank_pct": float(out["rank_pct"]),
            "cluster": int(out["cluster"]),
        }

    def metadata(self):
        return {"name": "my_fm", "version": "W10", "n_clusters": 6}

    def _build(self, code, asof):
        # your private feature engineering
        ...


ModelRegistry.register("my_fm", MyFMCluster)
```

Add to `config/plugins.yaml`:

```yaml
load_at_startup:
  - G:/my_private_code/my_fm_model.py
```

Run `financial-analyst models list`:

```
2 registered model(s):
  lgb_momentum             {'name': 'lgb_momentum', 'version': '0.1', 'horizon_days': 5}
  my_fm                    {'name': 'my_fm', 'version': 'W10', 'n_clusters': 6}
```

Now every `financial-analyst report SH600519` automatically calls both models. `quant-analyst` sees their consensus.

## Example: news collector (Tushare)

See `examples/custom_news_collector.py` for a Tushare-backed `BaseNewsCollector` skeleton. Adapt for your data source (RSS / web scrape / proprietary feeds).

Once your collector is implemented, call `collector.collect("SH600519", days=7)` from a cron job or wrapper script. It writes to `news/<code>/` drop-zone — `news-reader` sub-agent automatically picks up new files on next report.

## Example: F10 collector (pytdx)

See `examples/custom_f10_collector.py`. For a full reference implementation pulling LHB / 公司大事 / 大宗交易, see [G:\stocks/scripts/tdx_f10_collector.py](https://github.com/jesson-hh/) (private).

## Inspect what's registered

```bash
financial-analyst models list       # quant models
financial-analyst loaders list      # data loaders
financial-analyst agents list       # sub-agents
financial-analyst collectors list   # news/F10 collector interfaces
```

## Why this design

- **No proprietary checkpoints in the repo.** Users with private models keep them outside the open-source codebase.
- **No vendor lock.** Switch from Tushare to your own data source by writing one `BaseLoader` subclass.
- **Composable.** Each model registered participates in `consensus_rank_pct`. Add 3 models, get 3-way consensus.
- **Safe defaults.** If a user plug-in fails to load, the CLI prints a warning but still works with built-ins only.
