"""One-off real-data preflight; does not save a live data-screen/workflow run."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-root", type=Path,
        default=Path(os.environ.get("GUANLAN_LIVE_ROOT", str(ROOT))),
        help="Guanlan checkout providing engine/config (or GUANLAN_LIVE_ROOT)",
    )
    args = parser.parse_args()
    live = args.live_root.expanduser().resolve()
    if not (live / "engine" / "financial_analyst").is_dir():
        parser.error(f"live root is missing vendored engine: {live}")
    sys.path.insert(0, str(live))
    sys.path.insert(0, str(live / "engine"))
    import financial_analyst
    from financial_analyst.data.loader_factory import get_default_loader

    spec = importlib.util.spec_from_file_location("trend_preview_screen", ROOT / "guanlan_v2/screen/data_screen.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    graph = json.loads((ROOT / "var/trend_pullback_20260912/graphs/01_strict_pullback_screen.json").read_text(encoding="utf-8"))
    loader = get_default_loader(live / "config/loaders.yaml")
    report = {"kind": "nonpersistent_real_data_preflight_not_workflow_completion",
              "created_at": datetime.now().astimezone().isoformat(),
              "engine": financial_analyst.__file__, "screen_module": str(Path(module.__file__).resolve()),
              "persist": False, "branches": []}
    for node in graph["nodes"]:
        if node["type"] != "data_screen":
            continue
        request = dict(universe="csi800", freq="day", **node["params"])
        result = module.run_data_screen(request, loader=loader, persist=False)
        report["branches"].append({"node_id": node["id"], "request": request, "result": result})
        (ROOT / "var/trend_pullback_20260912/screen_preview.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"branch": node["id"], "ok": result.get("ok"), "as_of": result.get("as_of"),
                          "n_loaded": result.get("n_loaded"), "n_matched": result.get("n_matched"),
                          "reason": result.get("reason")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
