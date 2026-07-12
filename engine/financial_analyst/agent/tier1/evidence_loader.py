"""Evidence loader — zero-LLM node that reads the platform evidence pack
(十大数据面: quote_live/fundflow/board_eco/sentiment/kuaixun/chain/quant/
mainline/macro/holding) built by guanlan_v2 *before* the report swarm is
spawned, and passes it through as-is for downstream segments to cite.

No LLM call, no network I/O — mirrors overseas_market_scanner's zero-LLM
DI-collector structure but reads a pre-built JSON file instead of pulling
a live collector.

Priority for locating the pack path (highest wins), mirroring the ctor >
env > default chain used by mainline_classifier.py:21-24,63-65:
  1. Explicit ``pack_path`` passed to the constructor (test / caller override)
  2. ``$FA_EVIDENCE_PACK`` env var (guanlan_v2 sets this before spawning the
     report subprocess — see guanlan_v2/reports/evidence.py)
  3. ``None`` — no evidence pack configured

Never fabricates numbers: if the pack path is unset or the file is missing,
``_execute`` raises so the node comes back ``ok=False`` with an explicit
"平台证据缺失" error — downstream report segments then degrade honestly
instead of silently citing an empty pack that could be mistaken for
"checked, nothing found". A malformed (non-JSON) pack is likewise left to
raise naturally (``json.JSONDecodeError``) rather than being swallowed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from financial_analyst.agent.base import SubAgent


class EvidenceLoaderOutput(BaseModel):
    ok: bool
    generated_at: str = ""
    sections: dict = {}
    errors: dict = {}
    note: str = ""


class EvidenceLoader(SubAgent[EvidenceLoaderOutput]):
    """Zero-LLM node — reads FA_EVIDENCE_PACK JSON, passes sections through as-is."""

    NAME = "evidence-loader"
    OUTPUT_SCHEMA = EvidenceLoaderOutput

    def __init__(self, memory_root, pack_path: Optional[str] = None):
        super().__init__(memory_root=memory_root)
        self._pack_path = pack_path or os.environ.get("FA_EVIDENCE_PACK")

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not self._pack_path:
            raise FileNotFoundError(
                "FA_EVIDENCE_PACK 未设置或文件不存在——平台证据缺失,下游段将降级"
            )
        path = Path(self._pack_path)
        if not path.exists():
            raise FileNotFoundError(
                "FA_EVIDENCE_PACK 未设置或文件不存在——平台证据缺失,下游段将降级"
            )

        pack = json.loads(path.read_text(encoding="utf-8"))

        return {
            "ok": True,
            "generated_at": pack.get("generated_at", ""),
            "sections": pack.get("sections") or {},
            "errors": pack.get("errors") or {},
            "note": "",
        }
