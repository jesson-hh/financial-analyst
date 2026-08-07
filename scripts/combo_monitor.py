# -*- coding: utf-8 -*-
"""流派组合月度失效监控(E4 任务书)—— 对 factorlib mined 里的组合 draft 重跑长窗复验。

用法(仓根手动跑,或由调度器月度触发):
    python scripts/combo_monitor.py            # 监控默认9个入库组合
    python scripts/combo_monitor.py --all      # 监控 mined 里全部「组合·」前缀因子

判据(docs/research/2026-08-07-schools-cards-factors-minute.md):
- 每次记录 rank_ic / OOS段ic / verdict / Sharpe 到 var/combo_monitor.jsonl(追加,含时间戳);
- 连续 3 次 OOS 段 rank_ic < 0.01 → 打印「疑似失效」并在记录里 flag,提交人审降级
  (本脚本只记录不降级——降级动作走 factorlib 人审)。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:9999"
REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "var" / "combo_monitor.jsonl"
MINED = REPO / "guanlan_v2" / "factorlib" / "mined"

DEFAULT_NAMES = [
    "组合·A股短期反转", "组合·反转加缩量过滤", "组合·量价相关反向", "组合·小市值警示版",
    "组合·IBD相对强度RS·反向", "组合·温斯坦二期趋势·反向", "组合·聪明钱VWAP偏离·反向",
    "组合·行业中性动量·反向", "组合·Clenow回归动量·反向",
]


def post(path: str, body: dict, timeout: int = 560) -> dict:
    req = urllib.request.Request(BASE + path, json.dumps(body).encode("utf-8"),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def load_combos(want_all: bool) -> list[dict]:
    out = []
    for f in sorted(MINED.glob("*.json")):
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in items if isinstance(items, list) else [items]:
            name = str(it.get("name") or "")
            if want_all and name.startswith("组合·") or name in DEFAULT_NAMES:
                out.append({"name": name, "expr": it.get("expr")})
    seen, uniq = set(), []
    for c in out:
        if c["name"] not in seen and c.get("expr"):
            seen.add(c["name"])
            uniq.append(c)
    return uniq


def recent_flags(name: str, n: int = 2) -> int:
    """已有记录里该组合最近 n 次 OOS ic 低于阈值的次数。"""
    if not OUT.exists():
        return 0
    rows = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [r for r in rows if r.get("name") == name][-n:]
    return sum(1 for r in rows if (r.get("oos_ic") is not None and r["oos_ic"] < 0.01))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    combos = load_combos(args.all)
    if not combos:
        print("no combos found in mined/", file=sys.stderr)
        return 1
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = OUT.open("a", encoding="utf-8")
    bad = []
    for c in combos:
        try:
            r = post("/factor/report2", {
                "expr_or_name": c["expr"], "universe": "csi300_active",
                "freq": "month", "start": "2020-01-01", "oos_frac": 0.3,
            })
            oos = (r.get("oos") or {})
            row = {
                "ts": ts, "name": c["name"],
                "rank_ic": (r.get("ic") or {}).get("rank_ic_mean"),
                "oos_ic": (oos.get("oos") or {}).get("rank_ic"),
                "oos_verdict": oos.get("verdict"),
                "sharpe": (r.get("portfolio") or {}).get("sharpe"),
            }
            weak_now = row["oos_ic"] is not None and row["oos_ic"] < 0.01
            row["suspect_decay"] = bool(weak_now and recent_flags(c["name"]) >= 2)
            if row["suspect_decay"]:
                bad.append(c["name"])
        except Exception as exc:  # noqa: BLE001
            row = {"ts": ts, "name": c["name"], "error": f"{type(exc).__name__}: {exc}"[:200]}
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        print(f"{c['name']}: ic={row.get('rank_ic')} oos={row.get('oos_ic')} "
              f"{'⚠疑似失效' if row.get('suspect_decay') else ''}", flush=True)
    out.close()
    if bad:
        print("\n疑似失效(连续3次OOS ic<0.01),请人审降级:", *bad, sep="\n  - ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
