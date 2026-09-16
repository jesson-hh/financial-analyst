"""Emit the local Windows Time deployment attestation as value-free JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guanlan_v2.pit_time_attestation import probe_windows_time  # noqa: E402


def main() -> int:
    result = probe_windows_time()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
