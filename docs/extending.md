# Extending Financial Analyst

## Adding a Model (e.g. FM cluster)

1. Implement `BaseModel`:

```python
# src/financial_analyst/models/fm.py
from financial_analyst.models.base import BaseModel

class FMClusterModel(BaseModel):
    def predict(self, code, asof):
        # load checkpoint, return {"score": ..., "rank_pct": ..., "cluster": ...}
        ...
    def metadata(self):
        return {"name": "fm_cluster", "version": "0.1", "horizon_days": 10}
```

2. Register:

```python
# src/financial_analyst/models/__init__.py
from financial_analyst.models.fm import FMClusterModel
ModelRegistry.register("fm_cluster", FMClusterModel)
```

3. Enable in `config/models.yaml`. `model-predictor` agent will automatically include it.

## Adding a Sub-Agent (e.g. chain-analyst)

1. Define output schema and agent class:

```python
# src/financial_analyst/agent/tier2/chain_analyst.py
from financial_analyst.agent.base import SubAgent
from pydantic import BaseModel

class ChainOutput(BaseModel):
    upstream_health: int
    downstream_demand: int
    chain_score: int

class ChainAnalyst(SubAgent[ChainOutput]):
    NAME = "chain-analyst"
    OUTPUT_SCHEMA = ChainOutput
    async def _execute(self, inputs):
        # call LLM with knowledge base context
        ...
```

2. Register and add to preset:

```python
SubAgentRegistry.register("chain-analyst", ChainAnalyst)
```

```yaml
# config/swarm/stock-deep-dive-v2.yaml
agents:
  - name: chain-analyst
    deps: [quote-fetcher]
    input_keys: [quote-fetcher]
  # ... rest unchanged
```

3. Use: `financial-analyst report SH600519 --preset stock-deep-dive-v2`

## Adding a Loader

Same pattern: implement `BaseLoader`, register in `config/loaders.yaml`.

## Adding a Knowledge Base

Implement `KnowledgeBase`, inject into agent constructor or load from config.
