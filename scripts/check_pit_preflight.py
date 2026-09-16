"""Run the strict-PIT deployment preflight from a source checkout.

This wrapper makes the check independent of whichever same-named
``financial_analyst`` package is installed in the active environment.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENGINE_ROOT = _REPO_ROOT / "engine"
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from financial_analyst.data.pit_preflight import deployment_preflight  # noqa: E402


def main() -> int:
    result = deployment_preflight()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
