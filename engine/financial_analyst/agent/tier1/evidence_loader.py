"""Evidence loader — zero-LLM node that reads the platform evidence pack
(十五数据面: quote_live/company_facts/announcements/expectations/valuation/
research_views/fundflow/board_eco/sentiment/kuaixun/chain/quant/mainline/macro/
holding) built by guanlan_v2 *before* the report swarm is
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
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from financial_analyst.agent.base import SubAgent
from financial_analyst.data.pit import hong_kong_today, timestamp_not_after_asof
from financial_analyst.data.trusted_receipts import (
    REPORT_MANIFEST_DOMAIN,
    REPORT_SECTION_DOMAIN,
    verify_payload,
)


class EvidenceLoaderOutput(BaseModel):
    ok: bool
    schema_version: str = ""
    manifest_schema_version: str = ""
    code: str = ""
    asof: str = ""
    mode: str = ""
    generated_at: str = ""
    cache_key: str = ""
    manifest_digest: str = ""
    sections: dict = Field(default_factory=dict)
    receipts: dict = Field(default_factory=dict)
    unavailable_sources: list = Field(default_factory=list)
    errors: dict = Field(default_factory=dict)
    note: str = ""


_PACK_SCHEMA = "company-research-evidence@2"
_MANIFEST_SCHEMA = "report-evidence-manifest/v1"


def _normalise_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    try:
        from financial_analyst.buddy.tools import normalize_code
        return normalize_code(raw)
    except Exception:
        return raw


def _normalise_asof(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("asof_date must be a valid YYYY-MM-DD date") from exc
    if parsed > hong_kong_today():
        raise ValueError(f"asof_date {raw} is in the future")
    return parsed.isoformat()


def _digest(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _manifest_cache_key(code: str, asof: str, mode: str) -> str:
    return _digest({
        "source": "platform-evidence",
        "schema_version": _PACK_SCHEMA,
        "code": code,
        "asof": asof,
        "mode": mode,
    })


def _date_not_after(value: Any, requested_asof: str) -> bool:
    return timestamp_not_after_asof(value, requested_asof)


def _receipt_auth_ok(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    unsigned = dict(receipt)
    auth = unsigned.pop("auth", None)
    return verify_payload(REPORT_SECTION_DOMAIN, unsigned, auth)


def _manifest_auth_ok(pack: Any) -> bool:
    if not isinstance(pack, dict):
        return False
    unsigned = dict(pack)
    unsigned.pop("manifest_digest", None)
    auth = unsigned.pop("manifest_auth", None)
    return verify_payload(REPORT_MANIFEST_DOMAIN, unsigned, auth)


def _receipt_reason(
    *, source: str, section: Any, receipt: Any, requested_asof: str,
) -> str:
    """Return empty only for a source-bound receipt of the exact section body."""
    if not isinstance(receipt, dict):
        return "snapshot receipt missing"
    if receipt.get("source") != source:
        return "snapshot receipt source mismatch"
    if not _receipt_auth_ok(receipt):
        return "snapshot receipt authentication failed or trusted key unavailable"
    if receipt.get("status") != "available":
        return str(receipt.get("reason") or "source marked unavailable")
    if receipt.get("requested_asof") != requested_asof:
        return "snapshot receipt requested_asof mismatch"
    if receipt.get("point_in_time") is not True or receipt.get("historical_version_proven") is not True:
        return "historical acquisition version is not proven"
    if not _date_not_after(receipt.get("data_asof"), requested_asof):
        return "snapshot receipt data_asof missing, invalid, or after requested asof"
    pulled_at = receipt.get("pulled_at")
    version = str(receipt.get("snapshot_version") or "").strip()
    immutable = receipt.get("snapshot_immutable") is True
    snapshot_asof = receipt.get("snapshot_asof")
    pulled_present = pulled_at not in (None, "")
    pulled_proof = pulled_present and _date_not_after(pulled_at, requested_asof)
    version_proof = bool(version and immutable and _date_not_after(snapshot_asof, requested_asof))
    if not (pulled_proof if pulled_present else version_proof):
        return "receipt lacks pulled_at or immutable snapshot version at/before requested asof"
    if not receipt.get("cache_key") or not receipt.get("content_sha256"):
        return "snapshot receipt cache/content identity missing"
    if receipt.get("content_sha256") != _digest(section):
        return "snapshot receipt content digest mismatch"
    return ""


def _unavailable_receipt_reason(
    *, source: str, receipt: Any, requested_asof: str,
) -> str:
    """Require missing historical content to be explicitly receipted as unavailable."""
    if not isinstance(receipt, dict):
        return "snapshot receipt missing for unavailable section"
    if receipt.get("source") != source:
        return "snapshot receipt source mismatch"
    if not _receipt_auth_ok(receipt):
        return "snapshot receipt authentication failed or trusted key unavailable"
    if receipt.get("status") != "unavailable":
        return "historical section content missing despite available receipt"
    if receipt.get("requested_asof") != requested_asof:
        return "snapshot receipt requested_asof mismatch"
    return ""


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

        requested_code = _normalise_code(inputs.get("code")) if inputs.get("code") else ""
        requested_asof = _normalise_asof(inputs.get("asof_date"))
        pack_code = _normalise_code(pack.get("code")) if pack.get("code") else ""
        pack_asof = _normalise_asof(pack.get("asof") or pack.get("cutoff_as_of"))
        pack_mode = str(pack.get("mode") or "")
        historical = bool(
            (requested_asof and requested_asof < hong_kong_today().isoformat())
            or pack_mode == "historical"
        )

        if historical and (not requested_code or not requested_asof):
            raise ValueError("historical evidence requires requested code and asof_date")
        if requested_code and pack_code and requested_code != pack_code:
            raise ValueError(
                f"evidence pack code mismatch: requested={requested_code}, pack={pack_code}"
            )
        if requested_asof and pack_asof and requested_asof != pack_asof:
            raise ValueError(
                f"evidence pack asof mismatch: requested={requested_asof}, pack={pack_asof}"
            )

        if historical:
            required = {
                "schema_version": pack.get("schema_version"),
                "manifest_schema_version": pack.get("manifest_schema_version"),
                "code": pack_code,
                "asof": pack_asof,
                "mode": pack_mode,
                "cache_key": pack.get("cache_key"),
                "manifest_digest": pack.get("manifest_digest"),
            }
            missing = [name for name, value in required.items() if value in (None, "")]
            if missing:
                raise ValueError(
                    "historical evidence manifest identity missing: " + ", ".join(missing)
                )
            if pack.get("schema_version") != _PACK_SCHEMA:
                raise ValueError("historical evidence pack schema mismatch")
            if pack.get("manifest_schema_version") != _MANIFEST_SCHEMA:
                raise ValueError("historical evidence manifest schema mismatch")
            if pack_mode != "historical":
                raise ValueError("historical evidence manifest mode mismatch")
            expected_key = _manifest_cache_key(pack_code, pack_asof, pack_mode)
            if pack.get("cache_key") != expected_key:
                raise ValueError("evidence manifest cache_key mismatch")
            unsigned = dict(pack)
            expected_digest = unsigned.pop("manifest_digest")
            if _digest(unsigned) != expected_digest:
                raise ValueError("evidence manifest digest mismatch")
            if not _manifest_auth_ok(pack):
                raise ValueError(
                    "evidence manifest authentication failed or trusted key unavailable"
                )

        sections = dict(pack.get("sections") or {})
        receipts = dict(pack.get("receipts") or {})
        unavailable_sources = list(pack.get("unavailable_sources") or [])
        errors = dict(pack.get("errors") or {})

        if historical:
            for source in sorted(set(sections) | set(receipts), key=str):
                section = sections.get(source)
                receipt = receipts.get(source)
                if section is None:
                    reason = _unavailable_receipt_reason(
                        source=source, receipt=receipt,
                        requested_asof=requested_asof,
                    )
                else:
                    reason = _receipt_reason(
                        source=source,
                        section=section,
                        receipt=receipt,
                        requested_asof=requested_asof,
                    )
                if not reason:
                    continue
                sections[source] = None
                replacement = dict(receipt or {})
                replacement.update({
                    "source": source,
                    "status": "unavailable",
                    "requested_asof": requested_asof,
                    "data_asof": None,
                    "pulled_at": None,
                    "snapshot_version": None,
                    "snapshot_asof": None,
                    "snapshot_immutable": False,
                    "content_sha256": None,
                    "cache_key": _digest({
                        "source": source, "asof": requested_asof,
                        "status": "unavailable", "reason": reason,
                    }),
                    "point_in_time": False,
                    "historical_version_proven": False,
                    "proof": None,
                    "reason": reason,
                })
                receipts[source] = replacement
                errors.setdefault(source, reason)
                if not any(
                    isinstance(item, dict) and item.get("source") == source
                    for item in unavailable_sources
                ):
                    unavailable_sources.append({"source": source, "reason": reason})

        return {
            "ok": True,
            "schema_version": pack.get("schema_version", ""),
            "manifest_schema_version": pack.get("manifest_schema_version", ""),
            "code": pack_code,
            "asof": pack_asof or requested_asof or "",
            "mode": pack_mode or ("historical" if historical else "current"),
            "generated_at": pack.get("generated_at", ""),
            "cache_key": pack.get("cache_key", ""),
            "manifest_digest": pack.get("manifest_digest", ""),
            "sections": sections,
            "receipts": receipts,
            "unavailable_sources": unavailable_sources,
            "errors": errors,
            "note": "",
        }
