from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd


class ParquetCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int = 86400):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _key(self, namespace: str, params: Dict[str, Any]) -> Path:
        param_str = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        ns_dir = self.cache_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{digest}.parquet"

    def get(self, namespace: str, params: Dict[str, Any]) -> Optional[pd.DataFrame]:
        path = self._key(namespace, params)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.ttl_seconds:
            return None
        return pd.read_parquet(path)

    def set(self, namespace: str, params: Dict[str, Any], df: pd.DataFrame) -> None:
        path = self._key(namespace, params)
        df.to_parquet(path, index=False)
