"""U4 smoke:落盘读取(无 LLM)。直连运行中的 9999。
先写一条合成 test 记录进 var/seats_decisions.jsonl,再 GET /seats/decisions 断言读回 + 过滤 + 逆序,
最后清掉自己写的 kind==test 行(不污染真历史)。"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # G:/guanlan-v2
LOG = ROOT / "var" / "seats_decisions.jsonl"
BASE = "http://127.0.0.1:9999"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# 1) 直接追加一条合成 test 记录(模拟 _persist_decision 的产物)
LOG.parent.mkdir(parents=True, exist_ok=True)
marker = "SMOKE_MARK_U4"
rec = {"id": "test_smoke_1", "ts": "2026-06-09T00:00:00", "kind": "test",
       "code": "999999.SZ", "name": marker, "direction": "观望"}
with LOG.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

try:
    # 2) GET 读回(kind=test 过滤)
    j = get("/seats/decisions?kind=test&limit=10")
    assert j.get("ok") is True, j
    assert any(d.get("name") == marker for d in j.get("decisions", [])), "没读回合成记录"
    # 3) 逆序:最新在前
    assert j["decisions"][0]["id"] == "test_smoke_1", "非逆序/未取到最新"
    # 4) code 不存在 → 空
    j2 = get("/seats/decisions?code=ZZZZ")
    assert j2.get("ok") is True and j2.get("decisions") == [], j2
    print("U4 smoke PASS")
finally:
    # 清掉本 smoke 写的 kind==test 行(保持真历史干净)
    if LOG.exists():
        kept = []
        for ln in LOG.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("kind") == "test":
                    continue
            except Exception:
                pass
            kept.append(ln)
        LOG.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
