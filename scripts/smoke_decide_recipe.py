"""U2 smoke:decide 接配方 + 落盘(走真 LLM 快模式,十几秒内)。直连 9999。
断言:返回体回显 recipe_factors + 落盘新增一行含配方因子/strategy_name/kind=decide。
末尾清掉本 smoke 写的 strategy_id==strat_smoke 行(不污染真历史)。"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "var" / "seats_decisions.jsonl"
BASE = "http://127.0.0.1:9999"


def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


n0 = len(LOG.read_text(encoding="utf-8").splitlines()) if LOG.exists() else 0
body = {"code": "600519.SH", "name": "贵州茅台", "date": "2026-06-06",
        "seat_cn": "测试席", "creed": "测试信条", "mode": "fast",
        "strategy_id": "strat_smoke", "strategy_name": "smoke策略",
        "cards": [{"name": "smoke卡", "insight": "测试洞见"}],
        "recipe_factors": [{"name": "测试因子A", "ic": "0.05", "expr": "rank(x)"}],
        "research": ["测试研报 · smoke"]}
try:
    j = post("/seats/decide", body)
    assert j.get("ok") is True, j
    # 1) 返回体回显 recipe_factors(证明配方进了后端)
    assert any(f.get("name") == "测试因子A" for f in (j.get("recipe_factors") or [])), ("无 recipe_factors 回显", j)
    # 2) 落盘 +1 行,且新行含配方因子 + kind=decide + strategy_name
    n1 = len(LOG.read_text(encoding="utf-8").splitlines())
    assert n1 == n0 + 1, ("落盘行数未 +1", n0, n1)
    last = json.loads(LOG.read_text(encoding="utf-8").splitlines()[-1])
    assert last["kind"] == "decide", last
    assert any(f.get("name") == "测试因子A" for f in (last.get("recipe_factors") or [])), last
    assert last.get("strategy_name") == "smoke策略", last
    print("U2 smoke PASS ·", j.get("direction"), "· model", j.get("model_name"))
finally:
    # 清掉本 smoke 写的 strategy_id==strat_smoke 行
    if LOG.exists():
        kept = []
        for ln in LOG.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("strategy_id") == "strat_smoke":
                    continue
            except Exception:
                pass
            kept.append(ln)
        LOG.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
