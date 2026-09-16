"""Historical rule selection -> immutable evidence -> daily research ledger.

Separate from factor-vector backtests: no learned factor, quote patching, broker,
or promotion side effects. Artifacts are namespace checked and hash verified.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from datetime import date
from pathlib import Path

_ARTIFACT_SCHEMA = "guanlan.screen_history.v1"
_MAX_ROWS = 2_000_000


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _artifact_calendar(calendar, signal_valid):
    import numpy as np
    import pandas as pd
    dates = pd.DatetimeIndex(calendar)
    valid = list(signal_valid)
    if (len(dates) < 2 or dates.hasnans or dates.tz is not None or dates.has_duplicates
            or not dates.is_monotonic_increasing or not dates.equals(dates.normalize())):
        raise ValueError("历史筛选产物交易日历必须是至少2个严格递增的日频日期")
    if len(valid) != len(dates) or any(not isinstance(x, (bool, np.bool_)) for x in valid):
        raise ValueError("历史筛选产物 signal_valid 必须逐日对应且仅含布尔值")
    return dates, [bool(x) for x in valid]


def _artifact_frames(panel, signals):
    import pandas as pd
    from guanlan_v2.workflow.daily_simulator import _daily_index
    panel, signals = _daily_index(panel, "panel"), _daily_index(signals, "signals")
    if len(panel) > _MAX_ROWS or len(signals) > _MAX_ROWS:
        raise ValueError("历史筛选产物超过200万行上限")
    if not {"open", "close"}.issubset(panel.columns) or set(signals.columns) != {"eligible", "score"}:
        raise ValueError("历史筛选产物行情/信号字段不完整")
    if (not pd.api.types.is_bool_dtype(signals["eligible"].dtype)
            or signals["eligible"].isna().any()
            or not pd.api.types.is_numeric_dtype(signals["score"].dtype)):
        raise ValueError("历史筛选产物 eligible 必须为布尔值，score 必须为数值/缺失值")
    return panel, signals


class HistoryArtifactStore:
    def __init__(self, root=None):
        from guanlan_v2.workflow.store import _default_root
        self.root = Path(root) if root is not None else _default_root() / "_history"

    def _folder(self, owner, *, create=False):
        from guanlan_v2.workflow.store import _workspace_id
        folder = self.root / (_workspace_id(owner) or "global")
        for path in (folder, self.root, *self.root.parents):
            if _is_link(path):
                raise ValueError("历史筛选产物目录不可是链接")
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save(self, panel, signals, *, calendar, signal_valid, evidence, owner=None):
        from guanlan_v2.workflow.store import _workspace_id
        owner = _workspace_id(owner)
        calendar, signal_valid = _artifact_calendar(calendar, signal_valid)
        panel, signals = _artifact_frames(panel, signals)
        # Fail before filesystem mutation on non-JSON provenance such as NaN.
        _json(evidence)
        folder = self._folder(owner, create=True)
        with tempfile.TemporaryDirectory(prefix="building-", dir=folder) as tmp:
            temp = Path(tmp)
            panel.to_parquet(temp / "panel.parquet")
            signals.to_parquet(temp / "signals.parquet")
            meta = {"schema": _ARTIFACT_SCHEMA, "owner": owner,
                    "files": {n: _hash(temp / n) for n in ("panel.parquet", "signals.parquet")},
                    "calendar": [str(x.date()) for x in calendar],
                    "signal_valid": signal_valid, "evidence": evidence}
            encoded = _json(meta)
            if len(encoded.encode("utf-8")) > 16_000_000:
                raise ValueError("历史筛选清单超过16 MB上限")
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            artifact_id = "hs_" + digest
            (temp / "meta.json").write_text(encoded, encoding="utf-8")
            destination = folder / artifact_id
            if _is_link(destination):
                raise ValueError("历史筛选产物不可是链接")
            if not destination.exists():
                temp.rename(destination)
        handle = {"id": artifact_id, "digest": digest, "schema": meta["schema"]}
        self.load(handle, owner=owner)  # Verify before handing the reference downstream.
        return handle

    def load(self, handle, *, owner=None):
        import pandas as pd
        from guanlan_v2.workflow.store import _workspace_id
        owner = _workspace_id(owner)
        if not isinstance(handle, dict) or not re.fullmatch(r"hs_[0-9a-f]{64}", str(handle.get("id", ""))):
            raise ValueError("历史筛选产物 ID 非法")
        if handle.get("schema") != _ARTIFACT_SCHEMA:
            raise ValueError("历史筛选产物 handle schema 不支持")
        folder = self._folder(owner) / handle["id"]
        if _is_link(folder):
            raise ValueError("历史筛选产物不可是链接")
        meta_path = folder / "meta.json"
        if _is_link(meta_path) or meta_path.stat().st_size > 16_000_000:
            raise ValueError("历史筛选清单非法")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (not isinstance(meta, dict) or not isinstance(meta.get("files"), dict)
                or set(meta["files"]) != {"panel.parquet", "signals.parquet"}
                or not isinstance(meta.get("evidence"), dict)):
            raise ValueError("历史筛选产物清单结构不完整")
        digest = hashlib.sha256(_json(meta).encode()).hexdigest()
        if (meta.get("owner") != owner or meta.get("schema") != _ARTIFACT_SCHEMA
                or handle.get("digest") != digest or handle["id"] != "hs_" + digest):
            raise ValueError("历史筛选产物命名空间或摘要不一致")
        for name in ("panel.parquet", "signals.parquet"):
            path = folder / name
            if _is_link(path) or path.stat().st_size > 500_000_000 or _hash(path) != meta["files"].get(name):
                raise ValueError("历史筛选数据文件被修改或超出上限")
            # Enforce decoded row/column bounds before pandas allocates frames.
            import pyarrow.parquet as pq
            footer = pq.ParquetFile(path).metadata
            if footer.num_rows > _MAX_ROWS or footer.num_columns > 64:
                raise ValueError("历史筛选数据文件解码规模超过上限")
        _artifact_calendar(meta.get("calendar", []), meta.get("signal_valid", []))
        panel, signals = _artifact_frames(pd.read_parquet(folder / "panel.parquet"),
                                          pd.read_parquet(folder / "signals.parquet"))
        return panel, signals, meta


def _date(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD")
    return value


def validate_history_params(params, context):
    if not isinstance(context, dict):
        raise ValueError("历史筛选 source context 必须是对象")
    if not isinstance(params, dict) or set(params) - {"branches", "ranking", "evaluation_start", "label"}:
        raise ValueError("历史逐日筛选参数非法；支持 branches/ranking/evaluation_start/label")
    branches = params.get("branches")
    if not isinstance(branches, list) or not 1 <= len(branches) <= 8:
        raise ValueError("历史逐日筛选需要1至8个 branches，分支间为 OR")
    start, end = _date(context.get("start"), "source.start"), _date(context.get("end"), "source.end")
    evaluation_start = _date(params.get("evaluation_start", start), "evaluation_start")
    if not start <= evaluation_start <= end or (date.fromisoformat(end) - date.fromisoformat(start)).days > 3660:
        raise ValueError("历史区间顺序错误或超过10年上限")
    if context.get("research_freq") not in (None, "", "day"):
        raise ValueError("历史策略回放仅支持日线研究")
    universe = str(context.get("universe") or "").strip()
    codes = context.get("codes") or []
    if (not universe or universe == "自动") and not codes:
        raise ValueError("历史筛选必须显式选择股票池或代码")
    if universe in {"all_a", "all"} and not codes:
        raise ValueError("历史回放先使用具名指数或指定代码，不隐式扩展全A")
    from guanlan_v2.screen.data_screen import compile_data_screen_spec
    requests, compiled = [], []
    for branch in branches:
        if not isinstance(branch, dict):
            raise ValueError("分支必须是受控筛选请求对象")
        if branch.get("universe") not in (None, universe) or branch.get("codes", codes) != codes:
            raise ValueError("分支股票池/代码与 source 不一致")
        if (branch.get("freq", "day") != "day" or branch.get("as_of", end) != end
                or branch.get("end", end) != end):
            raise ValueError("分支频率/截止日与 source 不一致，禁止静默覆盖")
        if branch.get("score") is not None:
            raise ValueError("历史筛选使用固定 ranking，分支不得另设 score")
        request = {**branch, "universe": universe, "codes": codes, "freq": "day", "as_of": end}
        spec = compile_data_screen_spec(request)
        if spec.get("score") is not None:
            raise ValueError("历史筛选固定 ranking 不兼容预设隐含 score；请使用明确 conditions")
        if compiled and spec["filters"] != compiled[0]["filters"]:
            raise ValueError("历史筛选 OR 分支 filters 必须一致")
        compiled.append(spec)
        requests.append(request)
    if params.get("ranking", "nearest_ma10_ma20") != "nearest_ma10_ma20":
        raise ValueError("当前历史排序仅支持 nearest_ma10_ma20")
    return requests, start, end, evaluation_start


def run_history_screen(params, context, *, owner=None, loader=None, store=None, loaded=None):
    import pandas as pd
    from guanlan_v2.screen.data_screen import compile_data_screen_spec, resolve_data_screen_universe, _load_light_panel
    from guanlan_v2.screen.historical_screen import evaluate_historical_screen
    requests, start, end, evaluation_start = validate_history_params(params, context)
    if loaded is None:
        spec = compile_data_screen_spec(requests[0])
        codes, universe_meta, metadata, metadata_provenance = resolve_data_screen_universe(spec)
        if len(codes) > 1200:
            raise ValueError("历史回放股票池上限1200只")
        if loader is None:
            from financial_analyst.data.loader_factory import get_default_loader
            loader = get_default_loader()
        if not callable(getattr(loader, "_load_calendar", None)):
            raise ValueError("当前行情源未提供独立交易日历，不能用有行情日期推断空仓日")
        calendar = pd.DatetimeIndex(loader._load_calendar("day"))
        calendar = calendar[(calendar >= start) & (calendar <= end)]
        if len(calendar) * len(codes) > 2_000_000:
            raise ValueError("历史回放最多200万个日期×股票单元")
        panel, skipped, units = _load_light_panel(loader, codes, start, end, "day")
        load_meta = {"loader": type(loader).__name__, "start": start, "end": end,
                     "n_requested": len(codes), "n_loaded": panel.n_codes(),
                     "n_skipped": len(skipped), "skipped_sample": [list(x) for x in skipped[:20]],
                     "calendar_source": "loader independent day calendar", **units}
        loaded = (panel, calendar, universe_meta, metadata, metadata_provenance, load_meta)
    panel, calendar, universe_meta, metadata, metadata_provenance, load_meta = loaded
    # Injected/reused snapshots must obey the same source interval and universe
    # as the real loader. Otherwise extra pre-start bars change warmup results.
    from guanlan_v2.workflow.daily_simulator import _daily_index
    frame = _daily_index(panel.df if hasattr(panel, "df") else panel, "panel")
    calendar = pd.DatetimeIndex(calendar)
    calendar = calendar[(calendar >= start) & (calendar <= end)]
    frame = frame.loc[(frame.index.get_level_values(0) >= start) & (frame.index.get_level_values(0) <= end)].copy()
    spec = compile_data_screen_spec(requests[0])
    if (universe_meta or {}).get("id") not in (None, spec["universe"]):
        raise ValueError("加载的股票池与 source universe 不一致")
    present_codes = set(frame.index.get_level_values(1))
    if spec["codes"]:
        if present_codes - set(spec["codes"]):
            raise ValueError("加载的股票代码超出 source.codes")
    if max(len(present_codes), int((universe_meta or {}).get("n_resolved", len(present_codes)))) > 1200:
        raise ValueError("历史回放股票池上限1200只")
    panel = frame
    from guanlan_v2.workflow.history_price_quality import require_history_price_quality
    price_quality = require_history_price_quality(
        panel, calendar, adjustment_provenance=(load_meta or {}).get("price_adjustment"))
    result = evaluate_historical_screen(panel, requests, calendar=calendar, start=start, end=end,
        universe_meta=universe_meta, metadata=metadata, metadata_provenance=metadata_provenance,
        load_meta=load_meta, rank="nearest_ma10_ma20")
    eligible = result.eligibility
    signals = pd.DataFrame({"eligible": eligible.stack(future_stack=True),
                            "score": result.scores.stack(future_stack=True)})
    signals.index.names = ["datetime", "instrument"]
    frame = panel.copy()
    frame.index.names = ["datetime", "instrument"]
    replay_calendar = pd.DatetimeIndex(calendar)
    replay_calendar = replay_calendar[(replay_calendar >= evaluation_start) & (replay_calendar <= end)]
    if len(replay_calendar) < 2:
        raise ValueError("评估区间少于2个交易日")
    valid = result.calendar_valid.reindex(replay_calendar).fillna(False)
    evidence = {"requests": requests, "context": context, "evaluation_start": evaluation_start,
                "ranking": "nearest_ma10_ma20", "diagnostics": result.diagnostics,
                "universe": universe_meta, "metadata": metadata_provenance, "load": load_meta,
                "price_quality": price_quality,
                "label": str(params.get("label") or "历史趋势筛选"),
                "limitations": ["静态股票池及当前ST元数据，不是历史PIT成分/状态还原",
                    "使用通过历史价格校验的研究快照；复权收益不等于逐项公司行动现金流及真实成交还原",
                    "排序只使用当日及以前数据，比较结果仍是条件性研究证据"]}
    handle = (store or HistoryArtifactStore()).save(frame, signals, calendar=replay_calendar,
            signal_valid=valid.tolist(), evidence=evidence, owner=owner)
    counts = eligible.reindex(replay_calendar).sum(axis=1)
    latest = signals.xs(replay_calendar[-1])
    latest = latest.loc[latest["eligible"]].sort_index().sort_values("score", ascending=False, kind="stable")
    return {"ok": True, "__dt": "screen_history", "history_artifact": handle,
            "method": "historical_daily_screen", "_label": evidence["label"],
            "as_of": end, "evaluation_start": evaluation_start,
            "n_dates": len(replay_calendar), "n_codes": len(eligible.columns),
            "daily_candidate_counts": [[str(d.date()), int(n)] for d, n in counts.items()],
            "latest_candidates": [{"code": str(c), "score": float(r.score) if math.isfinite(r.score) else None}
                                  for c, r in latest.head(200).iterrows()],
            "latest_unrankable_candidates": int((~latest["score"].apply(math.isfinite)).sum()),
            "diagnostics": result.diagnostics, "price_quality": price_quality,
            "warnings": evidence["limitations"]}


def run_daily_backtest(history, params, *, owner=None, store=None):
    import pandas as pd
    from guanlan_v2.workflow.daily_simulator import simulate_daily_portfolio
    if not isinstance(history, dict) or history.get("__dt") != "screen_history":
        raise ValueError("日频模拟调仓仅接受 history_screen.history，不接受当日候选或因子")
    allowed = {"topn", "rebalance", "weight_mode", "initial_cash", "commission", "sell_tax", "slippage_bps"}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValueError("日频模拟调仓存在未知参数")
    if params.get("rebalance", "day") != "day" or params.get("weight_mode", "fixed_slots") != "fixed_slots":
        raise ValueError("当前仅支持日频、固定份额槽位，不足候选保留现金")
    def number(key, default):
        val = params.get(key, default)
        if isinstance(val, bool) or not isinstance(val, (float, int)) or not math.isfinite(val):
            raise ValueError(f"{key} 必须是有限数值")
        return val
    panel, signals, meta = (store or HistoryArtifactStore()).load(history.get("history_artifact"), owner=owner)
    from guanlan_v2.workflow.history_price_quality import require_history_price_quality
    # Recheck immutable older receipts too: a hash proves identity, not correct
    # adjustment or internally consistent prices. No client parameter bypass.
    price_quality = require_history_price_quality(panel,
        pd.DatetimeIndex(signals.index.get_level_values(0).unique()).sort_values(),
        adjustment_provenance=meta["evidence"].get("load", {}).get("price_adjustment"))
    calendar = pd.DatetimeIndex(meta["calendar"])
    result = simulate_daily_portfolio(panel, signals, calendar, top_n=number("topn", 10),
        initial_cash=number("initial_cash", 1_000_000), commission=number("commission", .0003),
        stamp_tax=number("sell_tax", .0005), slippage_bps=number("slippage_bps", 5),
        signal_valid=pd.Series(meta["signal_valid"], index=calendar))
    result.update({"ok": True, "__dt": "result", "method": "daily_screen_backtest",
        "composite": True, "_compose": True, "_label": meta["evidence"]["label"],
        "_universe": meta["evidence"]["context"]["universe"], "history_artifact": history["history_artifact"],
        "start": str(calendar[0].date()), "end": str(calendar[-1].date()), "n_dates": len(calendar),
        "backtest": {"rebalance": "day", "weight_mode": "fixed_slots", "topn": params.get("topn", 10),
                     "signal_time": "previous close", "execution_time": "next open", "overlapping_returns": False},
        "warnings": meta["evidence"]["limitations"], "price_quality": price_quality,
        "screen_diagnostics": meta["evidence"]["diagnostics"]})
    result["portfolio"].update({k: v for k, v in result.get("summary", {}).items()
                                if k not in {"nav_series", "return_series"}})
    return result
