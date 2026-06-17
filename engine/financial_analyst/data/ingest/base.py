from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict


class IngestResult:
    """Summary of an ingest run."""

    def __init__(self, n_instruments: int, n_dates: int, n_fields: int, target_root: Path):
        self.n_instruments = n_instruments
        self.n_dates = n_dates
        self.n_fields = n_fields
        self.target_root = target_root

    def __repr__(self) -> str:
        return (
            f"IngestResult(instruments={self.n_instruments}, dates={self.n_dates}, "
            f"fields={self.n_fields}, target={self.target_root})"
        )


class BaseIngester(ABC):
    """Convert any data source into the Qlib binary layout used by QlibBinaryLoader."""

    @abstractmethod
    def discover(self) -> Dict[str, Any]:
        """Inspect the source and return a summary dict (file count, date range, codes found, etc).

        Used by the CLI to show the user what will be ingested before writing.
        """

    @abstractmethod
    def convert(self, target_root: Path) -> IngestResult:
        """Read the source and write Qlib binary files under target_root.

        Returns IngestResult with counts.
        """
