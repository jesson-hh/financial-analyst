# -*- coding: utf-8 -*-
"""「发布 LSTM 为 DL 源」子进程入口(workflow /model/publish_dl 起):
读 spec.json → train_and_predict(写 var/dl_pred_lstm.parquet) → regen(折进 v4)。
打 [lstm_publish] 阶段标记供端点状态机解析。镜像 strategy/compute/model_workflow。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from guanlan_v2.strategy.compute.lstm_predict import train_and_predict
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date

_REPO = Path(__file__).resolve().parents[3]


def run(spec: dict) -> int:
    provider = spec.get("provider") or DEFAULT_PROVIDER
    date = spec.get("date") or _latest_trade_date(provider)
    p = dict(spec.get("params") or {})
    print(f"[lstm_publish] 阶段1 训练 LSTM · date {date}", flush=True)
    res = train_and_predict(
        provider=provider, eval_date=date, universe=(spec.get("universe") or "csi800"),
        seq_len=int(p.get("seq_len", 10)), hidden=int(p.get("hidden", 32)),
        layers=int(p.get("layers", 1)), lr=float(p.get("lr", 1e-3)),
        epochs=int(p.get("epochs", 40)), horizon=int(p.get("horizon", 5)))
    print(f"[lstm_publish] 训练完 {res}", flush=True)
    print(f"[lstm_publish] 阶段2 regen 折进 v4 · {date}", flush=True)
    rc = subprocess.call([sys.executable, "-m", "guanlan_v2.strategy.compute.regen", date],
                         cwd=str(_REPO))
    print(f"[lstm_publish] regen exit {rc}", flush=True)
    return int(rc)


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")) if len(sys.argv) > 1 else {}
    sys.exit(run(spec))


if __name__ == "__main__":
    main()
