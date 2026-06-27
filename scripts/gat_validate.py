# -*- coding: utf-8 -*-
"""GAT 源 CPCV 闸薄壳:对 var/dl_pred_gat.parquet 跑 cpcv.validate_dl_source 并打印结果(主 env 即可,无需 GPU)。

跑法:  python scripts/gat_validate.py            # 默认 var/dl_pred_gat.parquet
       python scripts/gat_validate.py <path>     # 指定预测表
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "engine"))

from guanlan_v2.strategy.compute.cpcv import validate_dl_source   # noqa: E402

OUT = str(_REPO / "var" / "dl_pred_gat.parquet")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else OUT
    res = validate_dl_source(path)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
