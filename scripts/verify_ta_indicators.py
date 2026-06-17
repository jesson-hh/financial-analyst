# scripts/verify_ta_indicators.py
# 把 ta_indicators.json 逐条 POST /factor/report,断言真实面板上能算出 KPI(status=ok)。
# 这是"已验证"的门禁:status≠ok 的条目不该留在库里(剔除或修正后重跑)。
# 需 9999 后端在跑(走在仓 engine)。退出码:有任何 bad → 1,全 ok → 0。
# 用法: & G:/financial-analyst/.venv/Scripts/python.exe scripts/verify_ta_indicators.py
import json
import os
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_TA_JSON = _REPO / "guanlan_v2" / "factorlib" / "base" / "ta_indicators.json"
_BASE = os.environ.get("GUANLAN_BASE", "http://127.0.0.1:9999")
_UNIVERSE = os.environ.get("GUANLAN_UNIVERSE", "csi_fast")


def _report(expr: str) -> dict:
    body = json.dumps({"expr_or_name": expr, "universe": _UNIVERSE}).encode("utf-8")
    req = urllib.request.Request(_BASE + "/factor/report", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    entries = json.loads(_TA_JSON.read_text(encoding="utf-8"))
    ok = bad = 0
    print(f"verify {len(entries)} TA factors via {_BASE}/factor/report (universe={_UNIVERSE})\n")
    for e in entries:
        try:
            d = _report(e["expr"])
            status = d.get("status")
            ic = (d.get("ic") or {}).get("ic_mean")
            cov = (d.get("characteristics") or {}).get("coverage")
            err = d.get("error") or ""
        except Exception as ex:  # noqa: BLE001
            status, ic, cov, err = "EXC", None, None, f"{type(ex).__name__}: {ex}"
        if status == "ok":
            ok += 1
            flag = "ok "
        else:
            bad += 1
            flag = "BAD"
        ic_s = f"{ic:+.4f}" if isinstance(ic, (int, float)) else "  -   "
        cov_s = f"{cov:.2f}" if isinstance(cov, (int, float)) else " -  "
        print(f"  [{flag}] {e['name']:<24} ic={ic_s} cov={cov_s} {str(err)[:60]}")
    print(f"\nledger: {ok} ok / {bad} bad / {len(entries)} total")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
