# -*- coding: utf-8 -*-
"""工作流节点 REST(guanlan 自有)—— ``/feature/build``(P2)+ 后续 ``/model/*``(P3)/ ``/backtest/*``(P5)。

特征工程节点 = 收特征公式 + 标签公式(或前向收益 horizon)→ 在 universe 面板上物化真 X/y →
返回真统计(n_dates/n_codes/coverage/特征-标签 IC/预览)+ 一个**可复算 fe spec**(供 P3 ML
节点原样回 POST 重建训练集)。

复用 ``/factor/report`` 同一条 universe→panel→eval 链(``factors/eval/report.py:170-210``):
``resolve_universe_codes`` → ``get_default_loader`` → ``load_panel_cached`` →
``compile_factor`` → ``winsorize``/``zscore`` → ``forward_simple_returns`` → ``ic_analysis``。
引擎 primitive **全部函数体内延迟 import**(对齐 ``factorlib/api.py``、``seats/api.py``),
模块顶部不 import 引擎,故引擎缺失时路由组仍可构造。数据根全经 ``get_data_paths`` 解析。

诚实失败:空特征 / 表达式编不动 / 空 universe / 面板加载失败 / 物化后全 NaN →
``ok:False`` + ``reason``,HTTP 200(前端降级,不抛 500;对齐 seats/factorlib 先例)。

``build_workflow_router()`` 返回**无 prefix** 的 ``APIRouter``(一个 router 容纳 P2-P5
自有节点端点),随 cards / seats / factorlib 工厂式经 ``server.create_app`` 挂上。
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Union

import threading as _threading

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# 工作流持久化 store(顶部 import,纯本地 JSON、无引擎依赖,安全)。
from guanlan_v2.workflow.store import WorkflowStore

# 标签留空 / 这些 token → 用前向收益(不是真公式标签)。前端 feature 节点 params.tag
# 取值 IC / fwd_ret,直接映射到本端点 label;故这些 token 一并触发收益分支。
_LABEL_FWD_TOKENS = {"", "ic", "fwd_ret", "fwd", "forward", "forward_return", "return", "ret"}

_PREVIEW_CAP = 50          # preview_rows 上限
_IC_DECAY_HORIZONS = (1, 3, 5, 10, 20)    # #2 截面 IC 衰减谱:因子对 1/3/5/10/20 日前向收益的 rank-IC 谱(判最优持有期)

# ── P6 工作流模型「存入模型库」异步子进程状态机(镜像 screen/api.py _MODEL_STATE) ──────
_PROMOTE_LOCK = _threading.Lock()
_PROMOTE_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _promote_public_state() -> Dict[str, Any]:
    import time as _t
    with _PROMOTE_LOCK:
        s = dict(_PROMOTE_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_promote_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"mpromote_{spec['variant_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.model_workflow", str(sf)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _PROMOTE_LOCK:
                _PROMOTE_STATE["lines"].append(line)
                if "[model_promote]" in line:
                    _PROMOTE_STATE["phase"], _PROMOTE_STATE["label"], _PROMOTE_STATE["step"] = \
                        ("train", "生产重训中…", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _PROMOTE_LOCK:
            _PROMOTE_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})


# ── LSTM「发布为 DL 源」异步子进程状态机(镜像 _PROMOTE_*;训练→regen 链)──────────
_PUBLISH_DL_LOCK = _threading.Lock()
_PUBLISH_DL_STATE: Dict[str, Any] = {"running": False, "phase": "idle", "label": "", "step": 0,
    "total": 3, "started_at": None, "ended_at": None, "ok": None, "error": None,
    "variant_id": None, "lines": []}


def _publish_dl_public_state() -> Dict[str, Any]:
    import time as _t
    with _PUBLISH_DL_LOCK:
        s = dict(_PUBLISH_DL_STATE); s["lines"] = list(s.get("lines") or [])[-12:]
    if s.get("started_at"):
        s["elapsed_sec"] = int((s.get("ended_at") or _t.time()) - s["started_at"])
    return s


def _run_publish_dl_subprocess(spec: Dict[str, Any]) -> None:
    import os, sys as _sys, time as _t, json as _json, tempfile, subprocess
    from pathlib import Path as _P
    rc, err = None, None
    try:
        repo = _P(__file__).resolve().parents[2]
        sf = _P(tempfile.gettempdir()) / f"lstmpub_{spec['variant_id']}.json"
        sf.write_text(_json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [_sys.executable, "-m", "guanlan_v2.strategy.compute.lstm_workflow", str(sf)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, env=env)
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with _PUBLISH_DL_LOCK:
                _PUBLISH_DL_STATE["lines"].append(line)
                if "阶段1" in line:
                    _PUBLISH_DL_STATE["phase"], _PUBLISH_DL_STATE["label"], _PUBLISH_DL_STATE["step"] = \
                        ("train", "LSTM 训练中…", 1)
                elif "阶段2" in line:
                    _PUBLISH_DL_STATE["phase"], _PUBLISH_DL_STATE["label"], _PUBLISH_DL_STATE["step"] = \
                        ("regen", "折进 v4(regen)…", 2)
        proc.wait(); rc = proc.returncode
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    finally:
        with _PUBLISH_DL_LOCK:
            _PUBLISH_DL_STATE.update({"running": False, "ended_at": _t.time(),
                "ok": (rc == 0 and not err), "error": err or (None if rc == 0 else f"exit {rc}"),
                "phase": "done", "step": 3})


class FeatureBuildIn(BaseModel):
    """``POST /feature/build`` 入参(zoo-DSL 特征 + 标签公式/前向收益 horizon + universe)。"""

    # 特征表达式列表(zoo-DSL)。也接受单串 feature: str(兼容,内部归一为 list)。
    features: Optional[List[str]] = None
    feature: Optional[Union[str, List[str]]] = None

    label: Optional[str] = None          # 标签表达式;留空 / "IC" / "fwd_ret" → 前向收益
    fwd_days: int = 5                    # 前向收益 horizon(label 缺省时用)
    universe: str = "csi_fast"           # 股票池名(config/universes/*.txt 实体)
    codes: Optional[List[str]] = None    # #5 自选股池:给定则直接用这批代码(优先于 universe;单票/几只票自组池)
    benchmark: Optional[str] = None      # B2 对标宽基指数 id(csi300=真实沪深300)→ 注入 idx_ret 字段(个股 vs 大盘共振)
    leader: Optional[str] = None         # B2 龙头股代码 → 注入 ref_ret 字段(个股 vs 龙头共振)
    oos_frac: float = 0.0                # W7 样本外占比(尾段;0=不切;>0 如 0.3=末30%作 OOS 做过拟合体检)
    start: Optional[str] = None          # YYYY-MM-DD;缺省 = 今天 − 2y(对齐 report.py:184)
    end: Optional[str] = None            # YYYY-MM-DD;缺省 = 今天
    freq: str = "day"                    # 面板频率(与 loader/panel 一致)
    winsorize: bool = True               # 截面去极值开关(每特征列过 winsorize)
    winsorize_q: float = 0.01            # 去极值分位
    standardize: bool = True             # 截面 z-score 开关(每特征列过 zscore)
    preview_rows: int = Field(default=8) # 预览行数(上限 50)


def _num(v: Any) -> Optional[float]:
    """float 化并把 NaN/Inf/无法解析 → None(前端拿到 null 而非 NaN,对齐 seats/api._num)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _normalize_features(body: FeatureBuildIn) -> List[str]:
    """把 features / feature(单串或 list)归一为去空白、非空的表达式 list,保序去重。"""
    raw: List[str] = []
    if body.features:
        raw.extend(body.features)
    if body.feature is not None:
        if isinstance(body.feature, str):
            raw.append(body.feature)
        else:
            raw.extend(body.feature)
    out: List[str] = []
    seen = set()
    for e in raw:
        s = (e or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _norm_codes(codes: "Optional[List[str]]") -> List[str]:
    """#5 自选股池:把用户输入的代码归一为引擎 qlib 格式(``SH600519`` / ``SZ000858`` / ``BJ8…``)。
    接受 ``600519`` / ``sh600519`` / ``600519.SH`` 等写法;6 位纯数字按交易所加前缀:6→SH、0/3→SZ、
    4/8→BJ(北交所)。去空白、去重、保序;非 6 位数字的原样保留(交给 loader 诚实失败,不猜)。"""
    out: List[str] = []
    seen: set = set()
    for raw in (codes or []):
        c = str(raw or "").strip().upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        if not c:
            continue
        if c[:2] in ("SH", "SZ", "BJ"):
            code = c
        else:
            digits = "".join(ch for ch in c if ch.isdigit())
            if len(digits) != 6:
                code = c
            elif digits[0] == "6":
                code = "SH" + digits
            elif digits[0] in ("0", "3"):
                code = "SZ" + digits
            elif digits[0] in ("4", "8"):
                code = "BJ" + digits
            else:
                code = "SH" + digits
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


# ── B2 市场/参照序列注入(共振)──────────────────────────────────────────────
#   把"对标宽基指数日收益"(idx_ret)/"龙头股日收益"(ref_ret)按面板交易日广播成列注入
#   panel.df,再经引擎 compile_factor 的"自动暴露 panel 列"机制,使公式可写
#   correlation(returns, idx_ret, 20) / correlation(returns, ref_ret, 20) 做"个股 vs
#   大盘/龙头"的滚动共振 —— 不硬编共振因子,纯工作流组合(用户红线)。
#   数据只读:idx_ret 经 get_data_paths→etf_index.parquet(真实指数,目前仅沪深300=399300.SZ);
#   ref_ret 经 get_default_loader().fetch_quote(龙头普通 code)。诚实:取不到则不注入该列 +
#   回 warning(公式引用缺失列会在 eval 处明确报错,绝不静默假数据)。

# 对标宽基指数 id → etf_index.parquet 的 ts_code(Tushare 格式)。目前真实数据仅沪深300;
# 其它宽基(中证500/1000/上证)无真实指数序列 → 请用 csmean(returns) 在所选池上做等权代理。
_BENCHMARK_TSCODE: Dict[str, str] = {
    "csi300": "399300.SZ",   # 沪深300(真实指数,etf_index.parquet 2005~今)
}


def _benchmark_ret_series(benchmark: str, start: str, end: str):
    """对标宽基指数日收益(单层 datetime 索引 Series)。返回 ``(series|None, warning|None)``。

    真实数据源 = ``etf_index.parquet``(经 get_data_paths,只读)。目前仅沪深300(399300.SZ)
    有真实指数;其它宽基 → None + warning(引导用 csmean 等权代理)。"""
    import pandas as pd
    key = (benchmark or "").strip()
    ts = _BENCHMARK_TSCODE.get(key)
    if not ts:
        return None, (f"对标指数 '{key}' 无真实指数数据(目前仅沪深300可用);"
                      f"其它宽基请用 csmean(returns) 在所选池上做等权代理大盘")
    try:
        from financial_analyst.data.paths import get_data_paths
        f = get_data_paths().parquet_root / "etf_index.parquet"
        ei = pd.read_parquet(f, columns=["ts_code", "trade_date", "close"])
    except Exception as exc:  # noqa: BLE001
        return None, f"benchmark_load_error: {type(exc).__name__}: {exc}"
    ei = ei[ei["ts_code"] == ts]
    if ei.empty:
        return None, f"对标指数 '{ts}' 不在 etf_index 中"
    ei = ei.assign(_dt=pd.to_datetime(ei["trade_date"], format="%Y%m%d")).sort_values("_dt")
    s = ei.set_index("_dt")["close"].astype(float)
    s = s[~s.index.duplicated(keep="last")].pct_change()
    s.index.name = "datetime"
    # 新鲜度守卫(互通审计 P0③):etf_index.parquet 停更时,窗尾 idx_ret 经 reindex 全 NaN →
    # 对标/共振族静默退化。这里显形为 warning(经 _inject_market_refs 进 UI 警示区),数据不动不造假。
    try:
        if end and len(s):
            _lag = int((pd.Timestamp(str(end)) - s.index.max()).days)
            if _lag > 4:   # 容忍周末+小长假;再大即视为源停更
                return s, (f"对标指数(沪深300)数据截至 {s.index.max().date()},落后请求窗尾 {_lag} 天"
                           f"(etf_index.parquet 停更)——基准对标与 idx_ret 共振/跟随族尾部缺数")
    except Exception:  # noqa: BLE001 — 守卫自身绝不挡数据
        pass
    return s, None


def _leader_ret_series(leader: str, start: str, end: str, freq: str = "day"):
    """龙头股日收益(单层 datetime 索引 Series)。返回 ``(series|None, warning|None)``。

    经 ``get_default_loader().fetch_quote``(只读)。龙头是普通 code(归一同 _norm_codes);
    取不到 → None + warning。"""
    import pandas as pd
    code = (_norm_codes([leader]) or [None])[0]
    if not code:
        return None, f"龙头代码 '{leader}' 无法解析"
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
        df = loader.fetch_quote(code, start, end, freq="day")
    except Exception as exc:  # noqa: BLE001
        return None, f"龙头 '{code}' 取数失败: {type(exc).__name__}: {exc}"
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return None, f"龙头 '{code}' 无行情数据(代码是否正确?)"
    # fetch_quote 各 loader 索引形态不一:trade_date 列 / MultiIndex / 已是 datetime 索引 → 统一成 datetime 索引。
    if "trade_date" in df.columns:
        df = df.assign(_dt=pd.to_datetime(df["trade_date"])).set_index("_dt")
    elif isinstance(df.index, pd.MultiIndex):
        df = df.reset_index(level=[n for n in df.index.names if n != "datetime"], drop=True)
    c = df["close"].astype(float)
    c.index.name = "datetime"
    c = c[~c.index.duplicated(keep="last")].sort_index()
    return c.pct_change(), None


def _inject_market_refs(panel, benchmark: "Optional[str]", leader: "Optional[str]",
                        start: str, end: str, freq: str = "day"):
    """把对标指数/龙头日收益按面板交易日广播成 ``idx_ret`` / ``ref_ret`` 列注入面板。

    返回 ``(panel, warnings)``。**只有 benchmark/leader 真给定时才动**(否则原样返回,零开销)。
    注入时返回**新** PanelData(不 in-place 改缓存面板 → 不污染 load_panel_cached 的共享缓存)。
    取不到的参照不注入对应列(诚实:公式引用缺失列会在 eval 处报错,不假数据)。"""
    import pandas as pd
    bench = (benchmark or "").strip()
    lead = (leader or "").strip()
    if not bench and not lead:
        return panel, []
    refs: Dict[str, Any] = {}
    warnings: List[str] = []
    dt_level = panel.df.index.get_level_values("datetime")
    if bench:
        s, w = _benchmark_ret_series(bench, start, end)
        if s is not None:
            refs["idx_ret"] = s.reindex(dt_level).to_numpy()
        if w:
            warnings.append(w)
    if lead:
        s, w = _leader_ret_series(lead, start, end, freq)
        if s is not None:
            refs["ref_ret"] = s.reindex(dt_level).to_numpy()
        if w:
            warnings.append(w)
    if not refs:
        return panel, warnings
    from financial_analyst.factors.zoo.panel import PanelData
    return PanelData(panel.df.assign(**refs)), warnings


def _fail(universe: str, reason: str, **extra: Any) -> JSONResponse:
    """诚实失败:统一空壳 + reason(HTTP 200),对齐设计 §1.3。"""
    payload: Dict[str, Any] = {
        "ok": False,
        "universe": universe,
        "n_dates": 0,
        "n_codes": 0,
        "feature_names": [],
        "ic": [],
        "preview": {"columns": [], "rows": []},
        "reason": reason,
    }
    payload.update(extra)
    return JSONResponse(payload)


# ════════════════════════════════════════════════════════════════════════════
# W1a —— 数据源真实体检(/data/*)。候选股票池目录 + 体检入参模型。
#   /data/universes 逐个 resolve_universe_codes,只回真能解析(非空)者 + 真成分数;
#   /data/probe 在 universe + 时间窗上加载真面板,回真实票数/交易日/字段/覆盖率。
#   数据全经 resolve_universe_codes + get_default_loader/load_panel_cached(延迟 import,
#   不碰 engine/ 源、不碰 fa-watch-wt/stocks),诚实失败 ok:False + reason(HTTP 200)。
# ════════════════════════════════════════════════════════════════════════════

# 股票池候选目录(id → 中文名)。/data/universes 逐个 resolve,只回非空者。
# id 必须是 resolve_universe_codes 真能解析的名字(config/universes/*.txt 或 f10 指数回退)。
_UNIVERSE_CATALOG = [
    ("csi300", "沪深300"),
    ("csi500", "中证500"),
    ("csi800", "中证800"),
    ("csi1000", "中证1000"),
    ("all", "全市场A股"),
    ("csi_fast", "快速测试池(100只)"),
    ("sample30", "样本30(演示)"),
    ("etf", "ETF 池"),
    ("csi300_active", "沪深300·活跃成分"),
    ("csi300_2024h2", "沪深300·2024H2"),
]


# ════════════════════════════════════════════════════════════════════════════
# W3 —— 因子库可浏览目录。预置一批「看得懂」的研报标准 A 股因子(中文名 + zoo-DSL 表达式
#   + 大类 + 方向 + 一句话说明),供「因子库」节点橱窗式浏览、一键「用此因子」灌入下游。
#   表达式已**预定向**(高分=偏好,反转类已取负),全部只用真实可用字段(量价 + 估值;
#   W1b 已接财务 → 含 财务质量/成长/价值(真财报)因子。/factor/catalog 直接返回此常量(不读数据)。
#   全部 442 内置(alpha101/gtja191/qlib158)+ 39 仓内因子另经 /factor/list 浏览。
# ════════════════════════════════════════════════════════════════════════════
# (name, expr, cat, direction_note, desc)
_FACTOR_CATALOG = [
    # —— 动量 / 反转 ——
    ("5日反转", "-rank(ts_sum(returns,5))", "动量反转", "反向", "近5日跌得多的短期倾向反弹(A股短周期反转)"),
    ("10日反转", "-rank(ts_sum(returns,10))", "动量反转", "反向", "近10日累计收益的反向"),
    ("20日动量", "rank(ts_sum(returns,20))", "动量反转", "正向", "近20日涨势短期延续"),
    ("60日动量", "rank(ts_sum(returns,60))", "动量反转", "正向", "中期(约3月)趋势延续"),
    ("120日动量", "rank(ts_sum(returns,120))", "动量反转", "正向", "半年趋势延续"),
    ("路径调整动量", "rank(ts_sum(returns,60)/stddev(returns,60))", "动量反转", "正向", "夏普式动量:收益除以波动,走得稳的更好"),
    ("周价格反转", "-rank(delta(close,5))", "动量反转", "反向", "近5日价格变化的反向"),
    # —— 估值 ——
    ("低市净率PB", "-rank(pb)", "估值", "反向", "市净率越低越便宜→高分"),
    ("低市盈率PE", "-rank(pe_ttm)", "估值", "反向", "市盈率TTM越低越便宜"),
    ("低市销率PS", "-rank(ps_ttm)", "估值", "反向", "市销率TTM越低越便宜"),
    ("盈利收益率EP", "rank(1/pe_ttm)", "估值", "正向", "EP=1/PE,越高越便宜"),
    ("高股息", "rank(dv_ttm)", "估值", "正向", "股息率越高越好"),
    # —— 波动率 ——
    ("低波动20日", "-rank(stddev(returns,20))", "波动率", "反向", "低波异象:月度波动越小风险调整后越优"),
    ("低波动60日", "-rank(stddev(returns,60))", "波动率", "反向", "季度低波"),
    ("低振幅", "-rank(ts_mean((high-low)/close,20))", "波动率", "反向", "日内振幅越小越稳"),
    ("彩票因子", "-rank(ts_max(returns,20))", "波动率", "反向", "近20日单日最大涨幅高的(博彩偏好)反而差"),
    ("区间稳定", "rank(ts_min(close,20)/ts_max(close,20))", "波动率", "正向", "近20日最低/最高越接近1越稳"),
    # —— 波动率 · OHLC 已实现波动率(用日内高低开收,比 close-to-close 更高效;wiki「波动率」) ——
    ("Parkinson低波", "-rank(ts_mean(power(log(high/low),2),20))", "波动率", "反向", "Parkinson(1980)用高低价幅估已实现波动,比收盘价std更高效;低波→高分(rank下省去常数与开方)"),
    ("GarmanKlass低波", "-rank(ts_mean(0.5*power(log(high/low),2)-0.386*power(log(close/open),2),20))", "波动率", "反向", "Garman-Klass(1980)用OHLC四价估波动,效率高于Parkinson;低波→高分"),
    ("ATR低波幅", "-rank(ts_mean(max_pair(high-low,max_pair(abs(high-delay(close,1)),abs(low-delay(close,1)))),14)/close)", "波动率", "反向", "真实波幅ATR(14)/收盘价归一,含跳空缺口的波动度;低波→高分"),
    # —— 流动性 / 换手 ——
    ("低换手", "-rank(turnover_rate)", "流动性", "反向", "换手率越低越好"),
    ("换手放大降权", "-rank(turnover_rate/ts_mean(turnover_rate,20))", "流动性", "反向", "近期换手相对放大→降权"),
    ("Amihud非流动性", "rank(ts_mean(abs(returns)/amount,20))", "流动性", "正向", "非流动性溢价"),
    ("缩量企稳", "-rank(volume/ts_mean(volume,20))", "流动性", "反向", "近期相对缩量"),
    ("成交额放量", "rank(ts_mean(amount,20)/ts_mean(amount,60))", "流动性", "正向", "近期成交额相对放大(资金关注)"),
    # —— 技术形态 ——
    ("均线乖离回归", "-rank(close/ts_mean(close,20)-1)", "技术", "反向", "偏离20日均线太多→均值回归"),
    ("布林带位置", "-rank((close-ts_mean(close,20))/stddev(close,20))", "技术", "反向", "越超买越降分"),
    ("量价背离", "-rank(correlation(close,volume,10))", "技术", "反向", "价量正相关过高→见顶信号"),
    ("接近年内高点", "rank(close/ts_max(close,240))", "技术", "正向", "越接近一年高点越强势"),
    ("远离年内低点", "rank(close/ts_min(close,240))", "技术", "正向", "离一年低点越远越强"),
    ("均线多头排列", "rank(ts_mean(close,5)/ts_mean(close,20)-1)", "技术", "正向", "5日均线相对20日均线的距离(金叉/多头排列强度);趋势跟随→实测IC验真A股是否吃这套"),
    # —— 规模 ——
    ("小总市值", "-rank(total_mv)", "规模", "反向", "总市值越小越好(小盘溢价)"),
    ("小流通市值", "-rank(circ_mv)", "规模", "反向", "流通市值越小越好"),
    ("对数小市值", "-rank(log(total_mv))", "规模", "反向", "对数市值,削弱极端值"),
    # —— 财务质量(W1b 接入财务后解锁;季频·公告日 PIT 对齐) ——
    ("高ROE质量", "rank(roe)", "财务质量", "正向", "净资产收益率越高,盈利能力越强(质量因子)"),
    ("高ROA质量", "rank(roa)", "财务质量", "正向", "总资产收益率越高,资产运用效率越好"),
    ("高净利率", "rank(net_margin)", "财务质量", "正向", "净利率越高,产品/护城河越强"),
    ("低杠杆", "rank(-debt_ratio)", "财务质量", "反向", "资产负债率越低,财务风险越小"),
    ("高现金含量", "rank(cfo/net_income)", "财务质量", "正向", "经营现金流/净利,盈利质量越实"),
    # —— 成长 ——
    ("营收高成长", "rank(rev_yoy)", "成长", "正向", "营业收入同比增速越高越好(同期对比)"),
    ("净利高成长", "rank(np_yoy)", "成长", "正向", "净利润同比增速越高越好(同期对比)"),
    # —— 价值(基于真财报,优于价格快照口径) ——
    ("盈利收益率(真财报)", "rank(net_income/total_mv)", "估值", "正向", "净利/总市值,PIT 真实盈利收益率"),
    ("账面市值比BM", "rank(total_equity/total_mv)", "估值", "正向", "净资产/市值,高 BM 价值股"),
    # —— 情绪(连续·单票口径,配「个股时序IC」节点;不要套 rank) ——
    ("换手突增", "turnover_rate/ts_mean(turnover_rate,20)", "情绪", "正向", "换手率相对20日均值突增=关注度/拥挤(连续→时序IC)"),
    ("振幅强度", "((high-low)/delay(close,1))/ts_mean((high-low)/delay(close,1),20)", "情绪", "正向", "日内振幅相对放大=情绪波动(连续→时序IC)"),
    ("量价情绪共振", "sign(delta(close,1))*(turnover_rate/ts_mean(turnover_rate,20))", "情绪", "正向", "放量上涨为正(看涨拥挤)/放量下跌为负(恐慌)→时序IC"),
    # —— 反弹(离散触发,>0 即 firing,配「事件研究」节点;不要套 rank/截面) ——
    ("距均线乖离反弹", "cross(close,sma(close,5))*(((delay(close,1)-sma(close,20))/sma(close,20))<=-0.08)", "反弹", "事件", "超卖(低于20日线8%)后上穿5日线=反弹触发→事件研究CAR"),
    ("连跌后反转", "(returns>0)*(delay(ts_sum((returns<0)*1.0,4),1)>=4)", "反弹", "事件", "连跌≥4日后首个上涨日=见底反弹触发→事件研究CAR"),
    ("超卖业绩反弹", "(np_yoy>0.30)*(delta(np_yoy,1)!=0)*(((close-sma(close,20))/sma(close,20))<=-0.05)", "反弹", "事件", "超卖+净利同比>30%公告日=PEAD代理(惊喜=YoY代理)→事件研究"),
    # —— 消息面(价格代理·离散触发,配「事件研究」节点;无文本情绪数据,价格代理顶上) ——
    ("3σ异动", "abs((returns-ts_mean(returns,60))/stddev(returns,60))>3", "消息面", "事件", "收益偏离60日均值>3σ=消息催化代理→事件研究CAR"),
    ("跳空高开", "(open/delay(close,1)-1)>0.05", "消息面", "事件", "高开>5%=隔夜消息代理→事件研究CAR"),
    ("放量异动", "(volume/ts_mean(volume,20)>3)*(abs(returns)>0.03)", "消息面", "事件", "成交量>3倍20日均量且涨跌>3%=催化代理→事件研究CAR"),
    # —— 技术(精算字段:引擎已暴露 rsi_14/macd_signal/amihud_20/mom_*,无需重构) ——
    ("RSI14", "rsi_14", "技术", "反向", "Wilder RSI14 精算字段(超卖<30 超买>70;低 RSI=超卖反弹倾向)"),
    ("MACD信号", "macd_signal", "技术", "正向", "MACD 信号线精算字段(>0 多头动能)"),
    ("Amihud非流动性精算", "amihud_20", "流动性", "正向", "Amihud 20日非流动性精算字段(非流动性溢价)"),
    ("中期动量精算", "rank(mom_120)", "动量反转", "正向", "120日动量精算字段(≈ts_sum(returns,120) 但精算)"),
    # —— 共振(个股 vs 大盘/行业/龙头,连续关系因子,配「个股时序IC」;需 source 设对标指数/龙头) ——
    ("市场共振相关", "correlation(returns,idx_ret,20)", "共振", "正向", "个股与沪深300的20日滚动相关(需数据源对标指数;连续→时序IC)"),
    ("市场共振强度R²", "rsqr(returns,idx_ret,60)", "共振", "正向", "大盘能解释该股多少波动(R²拟合优度=共振强度;需对标指数)"),
    ("市场Beta", "regbeta(returns,idx_ret,60)", "共振", "正向", "个股对大盘的滚动β弹性(>1放大跟涨跌;需对标指数)"),
    ("共振变化", "delta(correlation(returns,idx_ret,20),5)", "共振", "正向", "相关性近5日上升/下降(差异化信号,非恒≈1的死水部分)"),
    ("行业共振", "correlation(returns,indmean(returns,industry),20)", "共振", "正向", "个股与所在行业均值的滚动相关(需 industry 列)"),
    ("龙头共振", "correlation(returns,ref_ret,20)", "共振", "正向", "个股与指定龙头的滚动相关(需数据源龙头代码)"),
    ("下行Beta跟跌", "regbeta(returns,filter_where(idx_ret,idx_ret<0),60,15)", "共振", "正向", "只在大盘下跌日的β(跟跌弹性;min_periods=15 容 NaN;需对标指数)"),
    # —— 跟随属性(滞后互相关/方向/弹性,连续关系因子,配「个股时序IC」;需 source 设对标指数/龙头) ——
    ("跟随大盘强度", "correlation(returns,delay(idx_ret,1),20)", "跟随", "正向", "个股今日 vs 大盘昨日的相关(高=跟随大盘;需对标指数)"),
    ("领先滞后方向", "correlation(returns,delay(idx_ret,1),20)-correlation(delay(returns,1),idx_ret,20)", "跟随", "正向", ">0 该股跟随大盘 / <0 领先大盘(Granger 方向代理;需对标指数)"),
    ("龙头跟随弹性", "regbeta(returns,ref_ret,20)", "跟随", "正向", "对龙头的滚动β(跟随幅度;需龙头代码)"),
    ("跟随稳定度", "-stddev(correlation(returns,delay(idx_ret,1),20),60)", "跟随", "正向", "跟随相关的波动越小越稳定(适合事件跟随;需对标指数)"),
    # —— 资金面(东财五档日频净流入·截面 rank·方向为假设待实测IC验真) ——
    ("主力净流入强度", "rank(ts_mean(main_net_pct,5))", "资金面", "正向", "近5日主力净占比均值,主力持续流入(方向假设,IC验真)"),
    ("超大单倾向", "rank(ts_mean(super_large_net_pct,5))", "资金面", "正向", "近5日超大单净占比,机构/大资金方向(方向假设)"),
    ("主力净流入动量", "rank(ts_sum(main_net_pct,10))", "资金面", "正向", "近10日累计主力净占比(方向假设)"),
    ("连续净流入", "rank(ts_sum(sign(main_net_amount),10))", "资金面", "正向", "近10日主力净流入天数(sign 计净流入日;方向假设)"),
    ("资金集中度", "rank((super_large_net_pct+large_net_pct)-(medium_net_pct+small_net_pct))", "资金面", "正向", "大资金净流入 减 中小单净流入(大资金主导;方向假设)"),
    ("散户出逃", "rank(-ts_mean(small_net_pct,5))", "资金面", "反向", "近5日小单净流出(常伴主力吸筹;方向假设)"),
]
_FACTOR_CATS = ["动量反转", "估值", "财务质量", "成长", "波动率", "流动性", "技术", "规模", "情绪", "反弹", "消息面", "共振", "跟随", "资金面"]


class DataProbeIn(BaseModel):
    """``POST /data/probe`` 入参:对一个股票池 + 时间窗做真实覆盖体检。"""

    universe: str = "csi300"
    start: Optional[str] = None      # YYYY-MM-DD;缺省 = 今天 − 90d(守秒级)
    end: Optional[str] = None        # YYYY-MM-DD;缺省 = 今天
    freq: str = "day"                # 面板频率(day / 5min)


class FactorPreviewIn(BaseModel):
    """``POST /factor/preview`` 入参:校验一条 zoo-DSL 表达式 + 在小池上算真预览。"""

    expr: str = ""
    universe: str = "csi_fast"       # 默认小池,守秒级
    start: Optional[str] = None      # 缺省 = 今天 − 90d
    end: Optional[str] = None        # 缺省 = 今天
    freq: str = "day"


# ════════════════════════════════════════════════════════════════════════════
# P3 —— ML 训练节点(/model/*)。4 模型(XGBoost / LightGBM / SVM / RandomForest)
# 共享一条流程:复用 /feature/build 同款引擎 primitive 物化真 X/y → 按 compose 范式
# (compose.py:202-218)时序切 train/test → fit → predict → **预测分=仅 test 行有值的
# 截面因子 Series** → reindex 全面板喂 build_report 得 OOS FactorReport(真 IC/分层/组合)。
# 仅 _build_model 一处按 kind 岔开(实例化对应 sklearn-API 回归器)。诚实失败 ok:False +
# reason(HTTP 200),不抛 500。
# 守秒级:默认 csi_fast(100 码)+ 短窗(默认今天−1y)+ SVM 训练行采样上限。
# 数据只经 get_default_loader/load_panel_cached/resolve_universe_codes(全延迟 import)。
# ════════════════════════════════════════════════════════════════════════════

# kind → 库门禁 import 名(诚实失败「<lib> 未装」用)。svm/rf/mlp 同属 sklearn。
_MODEL_LIB = {"xgboost": "xgboost", "lightgbm": "lightgbm", "svm": "sklearn", "rf": "sklearn", "mlp": "sklearn", "lstm": "torch"}
_MODEL_KINDS = tuple(_MODEL_LIB)         # ("xgboost","lightgbm","svm","rf","mlp")
_SVM_TRAIN_CAP = 6000                    # SVM(SVR)/ MLP 训练行上限,超则定种子下采样(守 30s)
_MODEL_DEFAULT_YEARS = 1                 # ML 默认窗口 = 今天 − 1y(比 feature/build 的 2y 短,守秒级)


class ModelTrainIn(BaseModel):
    """``POST /model/train``(或 ``/model/{kind}``)入参。

    与 P2 ``fe_spec`` 同名字段原样回灌(前端把 ``/feature/build`` 返回的 ``fe`` 块逐字段
    POST 回来即可重建同一训练集);``kind`` 选模型;``params`` 接前端 node.params 原物。
    """

    kind: str = "xgboost"                # xgboost | lightgbm | svm | rf | mlp
    # —— fe_spec 透传字段(逐字对齐 FeatureBuildIn)——
    features: Optional[List[str]] = None
    feature: Optional[Union[str, List[str]]] = None
    label: Optional[str] = None          # 留空 / IC / fwd_ret → 前向收益;否则当 zoo 表达式
    fwd_days: int = 5
    universe: str = "csi_fast"           # 小池,守秒级
    codes: Optional[List[str]] = None    # #5 自选股池:给定则直接用这批代码(优先于 universe)
    benchmark: Optional[str] = None      # B2 对标宽基指数 id(csi300=真实沪深300)→ 注入 idx_ret 字段
    leader: Optional[str] = None         # B2 龙头股代码 → 注入 ref_ret 字段
    oos_frac: float = 0.0                # W7 样本外占比(尾段;0=不切;>0 如 0.3=末30%作 OOS 做过拟合体检)
    combine: str = "equal"               # 多因子合成法 equal|ic|icir(>1 feature 时生效;ic/icir 样本内估权)
    combine_train_frac: float = 0.6      # ic/icir 估权的样本内占比(前 X% 调仓日;防前视)
    wf_refit: bool = False               # W7 真滚动前进重训(ML):逐折 expanding-train 重训新模型→预测下段 OOS(K×成本,默认关)
    wf_folds: int = 4                    # 滚动重训折数(2~6)
    start: Optional[str] = None          # 缺省 = 今天 − 1y(ML 短窗)
    end: Optional[str] = None
    freq: str = "day"
    winsorize: bool = True
    winsorize_q: float = 0.01
    standardize: bool = True
    # —— 训练控制 ——
    train_frac: float = 0.7              # 时序 split,前 70% 调仓日训练,余测试
    params: Dict[str, Any] = Field(default_factory=dict)  # 前端超参(见 _build_model)
    preview_rows: int = Field(default=8)


def _fail_model(universe: str, reason: str, **extra: Any) -> JSONResponse:
    """ML 端点诚实失败空壳(HTTP 200),形状对齐 _fail 但带模型字段。"""
    payload: Dict[str, Any] = {
        "ok": False,
        "universe": universe,
        "n_train": 0,
        "n_test": 0,
        "n_train_dates": 0,
        "n_test_dates": 0,
        "feature_names": [],
        "metrics": {},
        "feature_importance": [],
        "preview": {"columns": [], "rows": []},
        "reason": reason,
    }
    payload.update(extra)
    return JSONResponse(payload)


def _materialize_xy(body: "ModelTrainIn", universe: str, features: List[str], start: str, end: str):
    """复用 /feature/build 同款引擎 primitive 物化真 X/y(panel + 特征矩阵 + 标签)。

    返回 ``(panel, fe_df, label_s, feature_names)``,其中 ``fe_df`` 是
    MultiIndex(datetime,code) × feature_names 的特征 DataFrame(每列已按开关 winsorize/
    zscore),``label_s`` 是同 index 的前向收益或 formula 标签 Series。失败 → ``JSONResponse``
    (诚实失败,调用方原样 return)。逐字复刻 api.py:144-232 的链,故与 fe spec 可复算契约一致。
    """
    import pandas as pd

    freq = (body.freq or "day").strip() or "day"

    # —— universe 解析(空 → 诚实失败)。自选股池 codes 给定则优先(#5)——
    custom = _norm_codes(getattr(body, "codes", None))
    if custom:
        codes = custom
    else:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail_model(universe, f"universe_error: {type(exc).__name__}: {exc}")
    if not codes:
        return _fail_model(universe, f"empty_universe: universe '{universe}' 解析为空")

    # —— 面板加载(逐字同 report.py:186-200 / feature/build)——
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:  # noqa: BLE001  —— 行业图缺失只是少 industry 列,不阻断
            ind_loader = None
        panel = load_panel_cached(loader, codes, start, end, freq=freq, industry_loader=ind_loader)
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"load_error: {type(exc).__name__}: {exc}",
                           start=start, end=end)

    # B2:注入对标指数/龙头日收益(idx_ret/ref_ret)→ 公式可做个股 vs 大盘/龙头共振(给定才动)。
    panel, _ = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                   getattr(body, "leader", None), start, end, freq)

    # —— 逐特征 compile_factor 求值 + 截面预处理 ——
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from financial_analyst.factors.zoo.registry import get as _get_alpha
        from financial_analyst.factors.eval.preprocess import winsorize, zscore
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"engine_import_error: {type(exc).__name__}: {exc}",
                           start=start, end=end)

    feat_series: List[pd.Series] = []
    feature_names: List[str] = []
    for i, expr in enumerate(features):
        name = f"f{i + 1}"
        try:
            try:
                s = _get_alpha(expr).compute(panel)
            except KeyError:
                validate_expr(expr)
                s = compile_factor(expr)(panel)
        except Exception as exc:  # noqa: BLE001
            return _fail_model(universe, f"{type(exc).__name__}: {exc}", start=start, end=end)
        if not isinstance(s, pd.Series):
            return _fail_model(universe,
                               f"特征 '{expr}' 求值返回 {type(s).__name__}, 期望 pd.Series",
                               start=start, end=end)
        if body.winsorize and body.winsorize_q and body.winsorize_q > 0:
            s = winsorize(s, body.winsorize_q)
        if body.standardize:
            s = zscore(s)
        feat_series.append(s.rename(name))
        feature_names.append(name)

    # —— 标签物化(留空/IC/fwd_ret → 前向收益;否则 zoo 表达式)——
    fwd_days = max(1, int(body.fwd_days or 5))
    label_raw = (body.label or "").strip()
    use_fwd = label_raw.lower() in _LABEL_FWD_TOKENS
    try:
        from financial_analyst.factors.eval.report import forward_simple_returns
        if use_fwd:
            label_s = forward_simple_returns(panel, fwd_days)
        else:
            try:
                label_s = _get_alpha(label_raw).compute(panel)
            except KeyError:
                validate_expr(label_raw)
                label_s = compile_factor(label_raw)(panel)
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"label_error: {type(exc).__name__}: {exc}",
                           start=start, end=end)
    if not isinstance(label_s, pd.Series):
        return _fail_model(universe,
                           f"标签求值返回 {type(label_s).__name__}, 期望 pd.Series",
                           start=start, end=end)

    fe_df = pd.concat(feat_series, axis=1)
    label_s = label_s.rename("__label__")
    return panel, fe_df, label_s, feature_names


def _combine_alpha(fe_df, feature_names, panel, fwd_days, method="equal",
                   train_frac=0.6, member_labels=None):
    """把多列(已 winsorize/zscore 的)因子合成一条 composite 截面因子。

    method:
      equal  等权 1/n(默认,与历史口径一致)
      ic     按**样本内** rank-IC 加权(保号:负 IC 的因子自动反向参与)
      icir   按**样本内** rank-ICIR 加权(IC 均值/标准差,惩罚 IC 不稳定的因子)
    ic/icir 的权重只用样本内(前 ``train_frac`` 的调仓日)各列 vs 前向收益的 rank-IC 估出,
    估完即固定、应用到全样本 → **不引入未来函数**;样本内 IC≈0(无信号)→ 诚实回退等权。
    返回 ``(alpha: pd.Series, weights: list[dict], note: str|None)``;weights 用
    ``member_labels`` 把内部列名(f1/f2…)显示回原表达式。"""
    import numpy as np
    import pandas as pd

    cols = [c for c in feature_names if c in fe_df.columns]
    labels = member_labels or {}
    _lab = lambda c: labels.get(c, c)
    if not cols:
        return pd.Series(dtype=float, name="alpha"), [], "无可用因子列"
    if len(cols) == 1:
        return (fe_df[cols[0]].dropna().rename("alpha"),
                [{"name": _lab(cols[0]), "weight": 1.0}], None)

    method = (method or "equal").strip().lower()

    def _equal():
        comp = None
        for nm in cols:
            comp = fe_df[nm] if comp is None else comp.add(fe_df[nm], fill_value=0.0)
        return (comp / float(len(cols))).dropna().rename("alpha")

    if method not in ("ic", "icir"):
        w = round(1.0 / len(cols), 4)
        return _equal(), [{"name": _lab(nm), "weight": w} for nm in cols], None

    # —— ic / icir:样本内估权(防前视)——
    from financial_analyst.factors.eval.ic import ic_analysis
    from financial_analyst.factors.eval.report import forward_simple_returns
    fwd = forward_simple_returns(panel, max(1, int(fwd_days or 21)))
    dts = fe_df.index.get_level_values("datetime")
    uniq = pd.DatetimeIndex(sorted(set(dts)))
    k = max(1, int(round(len(uniq) * float(train_frac or 0.6))))
    train_dates = set(uniq[:k])
    train_mask = pd.Index(dts).isin(train_dates)

    raw: Dict[str, float] = {}
    weights: List[Dict[str, Any]] = []
    for nm in cols:
        col_tr = fe_df[nm][train_mask].dropna()
        ic_v = icir_v = float("nan")
        if len(col_tr) > 0:
            try:
                r = ic_analysis(col_tr, fwd.reindex(col_tr.index))
                ic_v = float(r.rank_ic_mean); icir_v = float(r.rank_icir)
            except Exception:  # noqa: BLE001 — 单列估权失败按 0 权处理
                pass
        wv = icir_v if method == "icir" else ic_v
        raw[nm] = wv if np.isfinite(wv) else 0.0
        weights.append({"name": _lab(nm),
                        "ic": (round(ic_v, 4) if np.isfinite(ic_v) else None),
                        "icir": (round(icir_v, 3) if np.isfinite(icir_v) else None)})

    denom = sum(abs(v) for v in raw.values())
    if denom <= 1e-12:   # 样本内全无信号 → 诚实回退等权
        for i, nm in enumerate(cols):
            weights[i]["weight"] = round(1.0 / len(cols), 4)
        return _equal(), weights, f"{method} 估权样本内 IC≈0,已退回等权(诚实)"

    comp = None
    for nm in cols:
        term = fe_df[nm] * (raw[nm] / denom)
        comp = term if comp is None else comp.add(term, fill_value=0.0)
    for i, nm in enumerate(cols):
        weights[i]["weight"] = round(raw[nm] / denom, 4)
    return comp.dropna().rename("alpha"), weights, None


def _build_model(kind: str, params: Dict[str, Any]):
    """kind + 前端 node.params → 实例化对应 sklearn-API 回归器(延迟 import 各库)。

    超参映射对齐前端 SPECS(workflow.jsx:22-25):
      xgb  {trees,depth,lr,sub} → n_estimators/max_depth/learning_rate/subsample
      lgbm {leaves,lr}         → num_leaves/learning_rate
      svm  {c}                 → SVR(C=…)
      rf   {trees}             → RandomForestRegressor(n_estimators=…)
    未知 params 键忽略;缺省走库/前端默认。返回 (model, hyper_dict)(hyper_dict 回显真用超参)。
    """
    p = params or {}

    def _i(key: str, default: int) -> int:
        v = _num(p.get(key)); return int(v) if v is not None else int(default)

    def _f(key: str, default: float) -> float:
        v = _num(p.get(key)); return float(v) if v is not None else float(default)

    if kind == "xgboost":
        from xgboost import XGBRegressor
        hyper = {
            "n_estimators": max(1, _i("trees", 100)),
            "max_depth": max(1, _i("depth", 3)),
            "learning_rate": _f("lr", 0.1),
            "subsample": min(1.0, max(0.1, _f("sub", 1.0))),
        }
        model = XGBRegressor(
            objective="reg:squarederror", tree_method="hist",
            n_jobs=4, verbosity=0, random_state=0, **hyper,
        )
        return model, hyper

    if kind == "lightgbm":
        from lightgbm import LGBMRegressor
        hyper = {
            "num_leaves": max(2, _i("leaves", 31)),
            "learning_rate": _f("lr", 0.05),
            "n_estimators": max(1, _i("trees", 100)),
        }
        model = LGBMRegressor(
            objective="regression", n_jobs=4, verbosity=-1,
            min_child_samples=20, subsample=0.8, random_state=0, **hyper,
        )
        return model, hyper

    if kind == "svm":
        from sklearn.svm import SVR
        hyper = {"C": max(1e-3, _f("c", 1.0)), "kernel": "rbf"}
        return SVR(**hyper), hyper

    if kind == "rf":
        from sklearn.ensemble import RandomForestRegressor
        hyper = {"n_estimators": max(1, _i("trees", 200)), "max_depth": None}
        model = RandomForestRegressor(n_jobs=4, random_state=0, **hyper)
        return model, hyper

    if kind == "mlp":
        # 前馈多层感知机(NN 后端)。torch 未装 → 走 sklearn MLPRegressor(adam),
        # 原生满足 _train_eval 写死的 .fit(X,y)/.predict(X) 契约,与 4 ML 节点同形。
        # 超参对齐前端 SPECS.nn:hidden(隐层神经元)/layers(隐层数)/lr/epochs/alpha(L2 正则,替 dropout)。
        from sklearn.neural_network import MLPRegressor
        n_hidden = max(1, _i("hidden", 64))
        n_layers = max(1, _i("layers", 1))
        hyper = {
            "hidden_layer_sizes": tuple([n_hidden] * n_layers),  # layers 层 × hidden 神经元
            "learning_rate_init": max(1e-5, _f("lr", 1e-3)),
            "max_iter": max(1, _i("epochs", 200)),               # epochs = adam 迭代上限
            "alpha": max(0.0, _f("alpha", 1e-4)),                # L2 正则强度
            "activation": "relu",
        }
        model = MLPRegressor(solver="adam", random_state=0, early_stopping=False, **hyper)
        return model, hyper

    raise ValueError(f"unknown_kind: {kind}")


def _feature_importance(kind: str, model, feature_names: List[str], exprs: List[str]):
    """从已 fit 模型取归一特征重要度(树模型有;SVM/线性无 → 空表)。"""
    import numpy as np
    imp = getattr(model, "feature_importances_", None)  # xgb / lgbm / rf 都有
    if imp is None:
        return []
    arr = np.asarray(imp, dtype="float64")
    total = float(arr.sum())
    if total > 0:
        arr = arr / total
    out: List[Dict[str, Any]] = []
    for nm, ex, v in zip(feature_names, exprs, arr):
        out.append({"feature": nm, "expr": ex, "importance": _num(v)})
    out.sort(key=lambda r: (r["importance"] if r["importance"] is not None else -1), reverse=True)
    return out


def _report_blocks(report) -> Dict[str, Any]:
    """已 fit 模型的 OOS ``FactorReport`` → ``/factor/report`` 同形顶层块
    (meta/ic/quantile/portfolio/characteristics + report + composite),供模型节点
    **单独**也能在前端「因子分析」渲染整张报告(对齐 P4 ``_factor_eval`` 形)。
    report 为 None / 非 ok → 空块 + ``composite:False``(前端安全降级,仍只读标量
    ``metrics``,不谎报)。仅做序列化,无副作用(故对既有 4 模型零风险)。"""
    from dataclasses import asdict as _asdict
    if report is None or getattr(report, "status", "") != "ok":
        return {
            "meta": None, "ic": {}, "quantile": {}, "portfolio": {},
            "characteristics": {}, "report": None, "composite": False,
        }
    rep = _jsonable(_asdict(report))
    return {
        "meta": rep.get("meta"),
        "ic": rep.get("ic") or {},
        "quantile": rep.get("quantile") or {},
        "portfolio": rep.get("portfolio") or {},
        "characteristics": rep.get("characteristics") or {},
        "report": rep,
        "composite": True,
    }


def _oos_model_response(body: "ModelTrainIn", kind: str, libname: str, universe: str,
                        panel, pred_s, label_s, feature_names: List[str],
                        features: List[str], freq: str, fwd_days: int,
                        start: str, end: str, hyper: Dict[str, Any],
                        feat_imp: List[Dict[str, Any]], n_train: int, n_test: int,
                        n_train_dates: int, n_test_dates: int, train_frac: float,
                        sampled: int, preview_rows: int) -> JSONResponse:
    """模型节点共享 OOS 评测尾段(LSTM 用;4 ML / MLP 走 _train_eval 内联同款逻辑)。

    截面预测分 Series → reindex 整面板 ``build_report`` 出 OOS FactorReport → headline
    ``ic_analysis`` + OOS R² + 标量 ``metrics`` + **完整报告顶层块**(``_report_blocks``)
    + 预测分预览 + 可复算 fe spec。``build_report`` 退化只告警, 仍回标量 metrics(不抛)。
    形状与 _train_eval 返回逐字对齐(故 LSTM 与 4 ML 节点前端零差异)。"""
    import numpy as np
    import pandas as pd
    from financial_analyst.factors.eval.report import build_report, forward_simple_returns
    from financial_analyst.factors.eval.config import EvalConfig
    from financial_analyst.factors.eval.ic import ic_analysis

    fwd = forward_simple_returns(panel, fwd_days)

    pred_full = pred_s.reindex(panel.df.index)
    cfg = EvalConfig(
        universe=universe, freq=freq, start=start, end=end, fwd_days=fwd_days,
        winsorize_q=(body.winsorize_q if body.winsorize else 0.0),
        standardize=bool(body.standardize),
    )
    try:
        report = build_report(panel, lambda p: pred_full, cfg, f"model[{kind}]", "model")
    except Exception as exc:  # noqa: BLE001
        report = None
        report_err = f"{type(exc).__name__}: {exc}"
    else:
        report_err = ""
        if getattr(report, "status", "") != "ok":
            report_err = f"report_{getattr(report, 'status', '?')}: {getattr(report, 'error', '') or '评测退化'}"
            report = None

    fwd_oos = fwd.reindex(pred_s.index)
    ic_res = ic_analysis(pred_s, fwd_oos)

    lbl_oos = label_s.reindex(pred_s.index)
    joined = pd.concat([pred_s.rename("p"), lbl_oos.rename("y")], axis=1).dropna()
    r2 = None
    if len(joined) >= 2:
        yv = joined["y"].to_numpy("float64"); pv = joined["p"].to_numpy("float64")
        ss_tot = float(((yv - yv.mean()) ** 2).sum())
        ss_res = float(((yv - pv) ** 2).sum())
        if ss_tot > 0:
            r2 = _num(1.0 - ss_res / ss_tot)

    sharpe = ann_ret = None
    if report is not None and report.portfolio is not None:
        sharpe = _num(report.portfolio.sharpe)
        ann_ret = _num(getattr(report.portfolio, "ann_return", None))

    metrics = {
        "rank_ic": _num(ic_res.rank_ic_mean),
        "rank_icir": _num(ic_res.rank_icir),
        "ic": _num(ic_res.ic_mean),
        "icir": _num(ic_res.icir),
        "ic_tstat": _num(ic_res.ic_tstat),
        "oos_r2": r2,
        "sharpe": sharpe,
        "ann_return": ann_ret,
    }

    cols_prev = ["datetime", "code", "pred"]
    prev_rows: List[List[Any]] = []
    if preview_rows > 0:
        head = pred_s.sort_index().head(preview_rows)
        for idx, v in head.items():
            dt, cd = idx
            prev_rows.append([str(pd.Timestamp(dt).date()), str(cd), _num(v)])

    warnings: List[str] = []
    if n_test_dates < 12:
        warnings.append(f"OOS 测试调仓期太短 ({n_test_dates} < 12), 结论不稳健。")
    if sampled:
        warnings.append(f"{kind.upper()} 训练样本下采样至 {sampled} (守时延)。")
    if metrics["rank_ic"] is not None and metrics["rank_ic"] < 0:
        warnings.append("OOS RankIC 为负 — 模型预测方向反向。")
    if report_err:
        warnings.append(f"build_report 退化: {report_err}")

    fe_spec = {
        "features": features,
        "feature_names": feature_names,
        "label": None if (body.label or "").strip().lower() in _LABEL_FWD_TOKENS else (body.label or "").strip(),
        "fwd_days": fwd_days,
        "universe": universe,
        "start": start,
        "end": end,
        "freq": freq,
        "winsorize": bool(body.winsorize),
        "winsorize_q": float(body.winsorize_q),
        "standardize": bool(body.standardize),
    }

    out: Dict[str, Any] = {
        "ok": True,
        "kind": kind,
        "universe": universe,
        "model": {"kind": kind, "lib": libname, "hyperparams": hyper},
        "n_train": int(n_train),
        "n_test": int(n_test),
        "n_train_dates": n_train_dates,
        "n_test_dates": n_test_dates,
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "train_frac": train_frac,
        "metrics": metrics,
        "feature_importance": feat_imp,
        "preview": {"columns": cols_prev, "rows": prev_rows},
        "warnings": warnings,
        "status": "ok",
        "fe": fe_spec,
    }
    out.update(_report_blocks(report))   # #2:meta/ic/quantile/portfolio/characteristics/report/composite
    return JSONResponse(out)


def _walkforward_refit(body, kind, panel, fe_df, label_s, feature_names, fwd, reb, folds=4):
    """W7 真滚动前进重训(区别于 _wf_factor/_wf_perf 的事后切片统计):把调仓日切 folds+1 段,
    逐折 **expanding train [0:i] → 重新 fit 一个新模型 → predict 第 i+1 段 OOS**,拼接逐折 OOS
    预测 → 整体 OOS RankIC + 逐折 RankIC + 正占比。这是机构对 ML 策略的最低稳健性证据
    (单次切分易被某段行情运气主导)。复用 _build_model/_materialize_xy 物化结果,不动引擎。"""
    import numpy as np
    import pandas as pd
    from financial_analyst.factors.eval.ic import ic_analysis

    n = len(reb)
    folds = max(2, min(int(folds or 4), 6))
    if n < (folds + 1) * 3:   # 每段至少 ~3 调仓日才有意义
        return {"enabled": False, "reason": f"调仓期不足以做 {folds} 折滚动重训 (n={n}, 需≥{(folds + 1) * 3})"}
    bounds = [int(round(n * k / (folds + 1))) for k in range(folds + 2)]  # folds+1 段边界
    dt_level = fe_df.index.get_level_values("datetime")
    seg: List[Dict[str, Any]] = []
    oos_parts: List[Any] = []
    for i in range(1, folds + 1):
        tr_end, te_end = bounds[i], bounds[i + 1]
        train_dates = set(reb[:tr_end]); test_dates = set(reb[tr_end:te_end])
        if not train_dates or not test_dates:
            continue
        train_df = fe_df[dt_level.isin(train_dates)].join(label_s).dropna()
        test_feat = fe_df[dt_level.isin(test_dates)].dropna(how="all")
        if train_df.empty or test_feat.empty:
            continue
        Xtr = train_df[feature_names].to_numpy("float64"); ytr = train_df["__label__"].to_numpy("float64")
        if kind in ("svm", "mlp") and Xtr.shape[0] > _SVM_TRAIN_CAP:
            rng = np.random.RandomState(0)
            idx = rng.choice(Xtr.shape[0], _SVM_TRAIN_CAP, replace=False)
            Xtr, ytr = Xtr[idx], ytr[idx]
        try:
            model, _h = _build_model(kind, body.params)
            model.fit(Xtr, ytr)
            pred = np.asarray(model.predict(test_feat[feature_names].fillna(0.0).to_numpy("float64")), dtype="float64")
        except Exception:  # noqa: BLE001 — 单折失败跳过,不阻断整体
            continue
        ps = pd.Series(pred, index=test_feat.index)
        oos_parts.append(ps)
        icr = ic_analysis(ps, fwd.reindex(ps.index))
        seg.append({"fold": i, "train_end": str(pd.Timestamp(reb[tr_end - 1]).date()),
                    "rank_ic": _num(icr.rank_ic_mean), "n_test_dates": len(test_dates)})
    if not oos_parts:
        return {"enabled": False, "reason": "滚动重训无有效折(各折训练/测试段为空)"}
    stitched = pd.concat(oos_parts)
    stitched = stitched[~stitched.index.duplicated(keep="last")]
    ic_all = ic_analysis(stitched, fwd.reindex(stitched.index))
    ics = [s["rank_ic"] for s in seg if s["rank_ic"] is not None]
    pos = sum(1 for v in ics if v > 0)
    return {
        "enabled": True, "refit": True, "folds": len(seg),
        "stitched_oos_rank_ic": _num(ic_all.rank_ic_mean),
        "stitched_oos_rank_icir": _num(ic_all.rank_icir),
        "mean_fold_rank_ic": _num(float(np.mean(ics)) if ics else float("nan")),
        "pos_ratio": (_num(pos / len(ics)) if ics else None),
        "segments": seg,
        "note": "逐折 expanding-train → 重训新模型 → 预测下一段 OOS,拼接算整体 RankIC(真前进验证,非切片统计)。",
    }


def _train_eval(body: "ModelTrainIn", kind: str) -> JSONResponse:
    """4 模型共享流程母版:物化 X/y → 时序 split → fit → predict → 预测因子 → OOS 评测。

    步骤(全部对照真实引擎 primitive):
      1. 库门禁:kind 对应库未装 → 诚实失败「<lib> 未装」。
      2. 归一特征(空 → 诚实失败)。
      3. ``_materialize_xy`` 复用 /feature/build 链物化 panel + fe_df + label_s。
      4. compose 范式(compose.py:202-218):rebalance_dates → train_frac floor 切
         train/test 调仓日 → 行掩码。
      5. 训练集 = train 行 ∩ (特征,标签) 非 NaN;SVM 训练行 > cap → 定种子下采样。
      6. ``_build_model(kind,params)`` 实例化 → fit。
      7. predict 测试行 → 预测分 Series(**仅 test 行有值**,train 行 NaN)。
      8. reindex 全面板 → ``build_report`` 得 OOS FactorReport;另 ``ic_analysis`` 取
         headline rank-IC / ICIR;OOS R²(predict vs 标签)。
      9. 真指标 + 特征重要度 + 预测分预览 + 可复算 fe spec。
    任何阶段失败 → ``ok:False`` + reason(HTTP 200)。
    """
    import importlib.util as _ilu
    import numpy as np
    import pandas as pd

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    kind = (kind or "xgboost").strip().lower()

    # —— 1. 库门禁 ——
    libname = _MODEL_LIB.get(kind)
    if libname is None:
        return _fail_model(universe, f"unknown_kind: {kind}", kind=kind)
    if _ilu.find_spec(libname) is None:
        return _fail_model(universe, f"{libname} 未装 (pip install {libname})", kind=kind)

    # —— 2. 归一特征 ——
    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_model(universe, "因子不能为空", kind=kind)

    freq = (body.freq or "day").strip() or "day"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    fwd_days = max(1, int(body.fwd_days or 5))
    train_frac = float(body.train_frac if body.train_frac is not None else 0.7)
    train_frac = min(0.95, max(0.05, train_frac))
    preview_rows = max(0, min(int(body.preview_rows or 0), _PREVIEW_CAP))

    # —— 3. 物化 X/y ——
    mat = _materialize_xy(body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat  # 物化阶段已诚实失败(已带 kind? 否 → 补 kind 由前端无碍)
    panel, fe_df, label_s, feature_names = mat

    # —— 4. compose 范式时序切分(compose.py:202-218 逐字)——
    from financial_analyst.factors.eval.report import (
        forward_simple_returns, rebalance_dates, build_report,
    )
    from financial_analyst.factors.eval.config import EvalConfig
    from financial_analyst.factors.eval.ic import ic_analysis

    fwd = forward_simple_returns(panel, fwd_days)   # 评测口径前向收益(独立于标签)
    reb = sorted(pd.DatetimeIndex(rebalance_dates(list(panel.dates()), freq)))
    n_reb = len(reb)
    n_train = int(np.floor(train_frac * n_reb))
    train_dates = set(reb[:n_train])
    test_dates = set(reb[n_train:])
    n_train_dates = len(train_dates)
    n_test_dates = len(test_dates)
    if n_train_dates == 0:
        return _fail_model(universe, "训练段无调仓日 (窗口太短或 train_frac 过小)",
                           kind=kind, start=start, end=end, n_test_dates=n_test_dates)
    if n_test_dates == 0:
        return _fail_model(universe, "测试段无调仓日 (窗口太短或 train_frac 过大)",
                           kind=kind, start=start, end=end, n_train_dates=n_train_dates)

    # —— 5. 训练集(train 调仓行 ∩ 非 NaN 特征/标签)——
    dt_level = fe_df.index.get_level_values("datetime")
    train_row_mask = dt_level.isin(train_dates)
    test_row_mask = dt_level.isin(test_dates)
    cols = feature_names

    train_df = fe_df[train_row_mask].join(label_s).dropna()
    if train_df.empty:
        return _fail_model(universe, "训练段为空 (特征/标签在训练调仓行全 NaN)",
                           kind=kind, start=start, end=end,
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)
    Xtr = train_df[cols].to_numpy("float64")
    ytr = train_df["__label__"].to_numpy("float64")

    sampled = 0
    if kind in ("svm", "mlp") and Xtr.shape[0] > _SVM_TRAIN_CAP:
        rng = np.random.RandomState(0)
        idx = rng.choice(Xtr.shape[0], _SVM_TRAIN_CAP, replace=False)
        Xtr, ytr = Xtr[idx], ytr[idx]
        sampled = int(_SVM_TRAIN_CAP)

    # 测试设计矩阵(仅特征非全 NaN 行;NaN 特征项 fillna 0,同 combine.py 测试段约定)
    test_feat = fe_df[test_row_mask]
    test_feat = test_feat.dropna(how="all")
    if test_feat.empty:
        return _fail_model(universe, "测试段特征全 NaN",
                           kind=kind, start=start, end=end,
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)
    Xte = test_feat[cols].fillna(0.0).to_numpy("float64")

    # —— 6. fit ——
    try:
        model, hyper = _build_model(kind, body.params)
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"build_error: {type(exc).__name__}: {exc}",
                           kind=kind, start=start, end=end)
    try:
        model.fit(Xtr, ytr)
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"fit_error: {type(exc).__name__}: {exc}",
                           kind=kind, start=start, end=end,
                           n_train=int(Xtr.shape[0]),
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)

    # —— 7. predict 测试行 → 预测分 Series(仅 test 行有值)——
    try:
        pred = np.asarray(model.predict(Xte), dtype="float64")
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"predict_error: {type(exc).__name__}: {exc}",
                           kind=kind, start=start, end=end)
    pred_s = pd.Series(pred, index=test_feat.index, name="pred")

    # —— 8. OOS 评测:reindex 全面板 → build_report;ic_analysis 取 headline;OOS R² ——
    pred_full = pred_s.reindex(panel.df.index)
    cfg = EvalConfig(
        universe=universe, freq=freq, start=start, end=end, fwd_days=fwd_days,
        winsorize_q=(body.winsorize_q if body.winsorize else 0.0),
        standardize=bool(body.standardize),
    )
    try:
        report = build_report(panel, lambda p: pred_full, cfg, f"model[{kind}]", "model")
    except Exception as exc:  # noqa: BLE001
        report = None
        report_err = f"{type(exc).__name__}: {exc}"
    else:
        report_err = ""

    # headline IC:预测分(test 行) vs 同行前向收益
    fwd_oos = fwd.reindex(pred_s.index)
    ic_res = ic_analysis(pred_s, fwd_oos)

    # W7 ML 样本外体检:训练期(IS)vs 测试期(OOS)RankIC 并排(按 train_frac 切分)。
    # ML 的 IS=对训练行的预测(含拟合优度,天然偏高)→ 训练→测试的塌缩即过拟合信号,诚实标注。
    ml_oos: Dict[str, Any] = {"enabled": False}
    try:
        pred_is = pd.Series(model.predict(train_df[cols].to_numpy("float64")), index=train_df.index)
        ic_is = ic_analysis(pred_is, fwd.reindex(pred_is.index))
        _v, _r, _lbl = _oos_verdict(ic_is.rank_ic_mean, ic_res.rank_ic_mean, n_test_dates)
        _first_oos = sorted(test_dates)[0] if test_dates else None
        ml_oos = {
            "enabled": True, "metric": "rank_ic", "frac": round(1.0 - train_frac, 3),
            "split_date": (str(pd.Timestamp(_first_oos).date()) if _first_oos is not None else None),
            "is": {"rank_ic": _num(ic_is.rank_ic_mean), "n_dates": n_train_dates},
            "oos": {"rank_ic": _num(ic_res.rank_ic_mean), "n_dates": n_test_dates},
            "decay_ratio": _num(_r), "verdict": _v,
            "note": _lbl + " · ML 样本内=训练期拟合(偏高属常态),重点看样本外是否仍为正",
        }
    except Exception:  # noqa: BLE001 —— IS 体检失败不阻断主流程
        ml_oos = {"enabled": False}

    # OOS R²:predict vs 真标签(test 行,标签口径,可与 fwd 不同)
    lbl_oos = label_s.reindex(pred_s.index)
    joined = pd.concat([pred_s.rename("p"), lbl_oos.rename("y")], axis=1).dropna()
    r2 = None
    if len(joined) >= 2:
        yv = joined["y"].to_numpy("float64"); pv = joined["p"].to_numpy("float64")
        ss_tot = float(((yv - yv.mean()) ** 2).sum())
        ss_res = float(((yv - pv) ** 2).sum())
        if ss_tot > 0:
            r2 = _num(1.0 - ss_res / ss_tot)

    # report 的分层/组合指标(若 build_report 成功)
    sharpe = ann_ret = quantile_monotonic = None
    if report is not None and getattr(report, "status", "") == "ok":
        if report.portfolio is not None:
            sharpe = _num(report.portfolio.sharpe)
            ann_ret = _num(getattr(report.portfolio, "ann_return", None))
        if report.quantile is not None:
            quantile_monotonic = bool(getattr(report.quantile, "monotonic", False)) \
                if hasattr(report.quantile, "monotonic") else None

    metrics = {
        "rank_ic": _num(ic_res.rank_ic_mean),
        "rank_icir": _num(ic_res.rank_icir),
        "ic": _num(ic_res.ic_mean),
        "icir": _num(ic_res.icir),
        "ic_tstat": _num(ic_res.ic_tstat),
        "oos_r2": r2,
        "sharpe": sharpe,
        "ann_return": ann_ret,
    }

    feat_imp = _feature_importance(kind, model, feature_names, features)

    # —— 9. 预测分预览(test 行前 N,按日期升序)——
    cols_prev = ["datetime", "code", "pred"]
    prev_rows: List[List[Any]] = []
    if preview_rows > 0:
        head = pred_s.sort_index().head(preview_rows)
        for idx, v in head.items():
            dt, cd = idx
            prev_rows.append([str(pd.Timestamp(dt).date()), str(cd), _num(v)])

    warnings: List[str] = []
    if n_test_dates < 12:
        warnings.append(f"OOS 测试调仓期太短 ({n_test_dates} < 12), 结论不稳健。")
    if sampled:
        warnings.append(f"{kind.upper()} 训练行下采样至 {sampled} (守时延)。")
    if metrics["rank_ic"] is not None and metrics["rank_ic"] < 0:
        warnings.append("OOS RankIC 为负 — 模型预测方向反向。")
    if report_err:
        warnings.append(f"build_report 退化: {report_err}")

    fe_spec = {
        "features": features,
        "feature_names": feature_names,
        "label": None if (body.label or "").strip().lower() in _LABEL_FWD_TOKENS else (body.label or "").strip(),
        "fwd_days": fwd_days,
        "universe": universe,
        "start": start,
        "end": end,
        "freq": freq,
        "winsorize": bool(body.winsorize),
        "winsorize_q": float(body.winsorize_q),
        "standardize": bool(body.standardize),
    }

    # W7 真滚动前进重训(可选,K× 成本;默认关)。逐折 expanding-train 重训新模型 → 预测下段 OOS。
    wf_refit_block: Dict[str, Any] = {"enabled": False}
    if getattr(body, "wf_refit", False):
        try:
            wf_refit_block = _walkforward_refit(body, kind, panel, fe_df, label_s,
                                                feature_names, fwd, reb,
                                                folds=getattr(body, "wf_folds", 4))
        except Exception as exc:  # noqa: BLE001 — 滚动重训失败不阻断主流程
            wf_refit_block = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}

    _out: Dict[str, Any] = {
        "ok": True,
        "kind": kind,
        "universe": universe,
        "model": {"kind": kind, "lib": libname, "hyperparams": hyper},
        "n_train": int(Xtr.shape[0]),
        "n_test": int(Xte.shape[0]),
        "n_train_dates": n_train_dates,
        "n_test_dates": n_test_dates,
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "train_frac": train_frac,
        "metrics": metrics,
        "oos": ml_oos,   # W7 样本外体检块(训练期 IS vs 测试期 OOS RankIC + 衰减判定)
        "walkforward_refit": wf_refit_block,   # W7 真滚动前进重训(逐折重训→拼接 OOS;wf_refit=true 才算)
        "feature_importance": feat_imp,
        "preview": {"columns": cols_prev, "rows": prev_rows},
        "warnings": warnings,
        "status": "ok",
        "fe": fe_spec,
    }
    _out.update(_report_blocks(report))   # #2:完整 OOS 报告顶层块(模型节点单独可在「因子分析」出整张报告)
    return JSONResponse(_out)


def _lstm_eval(body: "ModelTrainIn", kind: str = "lstm") -> JSONResponse:
    """LSTM 序列网络(PyTorch)训练 + OOS 评测。与 4 ML / MLP 节点**同形输出**
    (ok/kind/model/metrics/feature_importance/preview/fe + 完整 OOS 报告顶层块),
    但训练用**真时间序列**:复用 /feature/build 物化真特征矩阵 → 逐 code 按日期排序,
    对每个调仓标签日 t 取其前 ``seq_len`` 期特征窗(seq_len × n_features)作样本、标签[t]
    作目标 → ``nn.LSTM`` → ``Linear(hidden→1)``,adam/MSE 训 ``epochs`` 轮 → 预测 test
    标签日 → 预测分截面因子 Series → reindex 整面板 ``build_report`` 出 OOS 报告
    (经共享 ``_oos_model_response`` 尾段,与 4 ML 节点报告同形)。

    torch 未装 → 诚实 ``ok:False``「torch 未装」。守秒级:csi_fast 小池 + 短窗(今天−1y)
    + 训练样本上限 ``_SVM_TRAIN_CAP`` 定种子下采样。数据只经 _materialize_xy 内
    get_default_loader(延迟 import,不碰 engine//fa-watch-wt/stocks)。"""
    import importlib.util as _ilu
    import numpy as np
    import pandas as pd

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    kind = (kind or "lstm").strip().lower()

    # —— 1. 库门禁:torch ——
    if _ilu.find_spec("torch") is None:
        return _fail_model(universe, "torch 未装 (pip install torch)", kind=kind)

    # —— 2. 归一特征 ——
    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_model(universe, "因子不能为空", kind=kind)

    freq = (body.freq or "day").strip() or "day"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    fwd_days = max(1, int(body.fwd_days or 5))
    train_frac = min(0.95, max(0.05, float(body.train_frac if body.train_frac is not None else 0.7)))
    preview_rows = max(0, min(int(body.preview_rows or 0), _PREVIEW_CAP))

    # —— LSTM 超参(对齐前端 SPECS.lstm:seq_len/hidden/layers/lr/epochs)——
    p = body.params or {}

    def _i(key: str, default: int) -> int:
        v = _num(p.get(key)); return int(v) if v is not None else int(default)

    def _f(key: str, default: float) -> float:
        v = _num(p.get(key)); return float(v) if v is not None else float(default)

    lookback = max(2, min(_i("seq_len", 10), 60))
    hidden = max(1, _i("hidden", 32))
    n_layers = max(1, min(_i("layers", 1), 3))
    lr = max(1e-5, _f("lr", 1e-3))
    epochs = max(1, min(_i("epochs", 40), 500))

    # —— 3. 物化 X/y(复用 /feature/build 链)——
    mat = _materialize_xy(body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat
    panel, fe_df, label_s, feature_names = mat

    # —— 4. compose 范式时序切分(同 _train_eval)——
    from financial_analyst.factors.eval.report import rebalance_dates
    reb = sorted(pd.DatetimeIndex(rebalance_dates(list(panel.dates()), freq)))
    n_reb = len(reb)
    n_train_reb = int(np.floor(train_frac * n_reb))
    train_dates = set(reb[:n_train_reb])
    test_dates = set(reb[n_train_reb:])
    n_train_dates = len(train_dates)
    n_test_dates = len(test_dates)
    if n_train_dates == 0:
        return _fail_model(universe, "训练段无调仓日 (窗口太短或 train_frac 过小)",
                           kind=kind, start=start, end=end, n_test_dates=n_test_dates)
    if n_test_dates == 0:
        return _fail_model(universe, "测试段无调仓日 (窗口太短或 train_frac 过大)",
                           kind=kind, start=start, end=end, n_train_dates=n_train_dates)

    # —— 5. 构序列:逐 code 排日期, 滑窗 lookback;标签日 ∈ train/test 分流 ——
    cols = feature_names
    joined = fe_df.join(label_s)               # __label__ 列(_materialize_xy 命名)
    train_X: List[Any] = []
    train_y: List[float] = []
    test_X: List[Any] = []
    test_idx: List[Any] = []
    for code, g in joined.groupby(level="code"):
        g = g.sort_index(level="datetime")
        dts = g.index.get_level_values("datetime")
        feat_arr = g[cols].to_numpy("float64")
        lab_arr = g["__label__"].to_numpy("float64")
        m = feat_arr.shape[0]
        for t in range(lookback - 1, m):
            win = feat_arr[t - lookback + 1: t + 1]      # (lookback, n_features)
            if not np.isfinite(win).all():
                continue
            dt = dts[t]
            if dt in train_dates:
                yv = lab_arr[t]
                if not np.isfinite(yv):
                    continue
                train_X.append(win)
                train_y.append(float(yv))
            elif dt in test_dates:
                test_X.append(win)
                test_idx.append((dt, code))
    if len(train_X) < 10:
        return _fail_model(universe,
                           f"LSTM 训练序列不足 ({len(train_X)}<10);窗口太短或 seq_len({lookback}) 过大",
                           kind=kind, start=start, end=end,
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)
    if not test_X:
        return _fail_model(universe, "LSTM 测试序列为空 (测试段无足量历史窗)",
                           kind=kind, start=start, end=end,
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)

    # 训练样本上限(守时延, 定种子下采样)
    sampled = 0
    if len(train_X) > _SVM_TRAIN_CAP:
        rng = np.random.RandomState(0)
        sel = rng.choice(len(train_X), _SVM_TRAIN_CAP, replace=False)
        train_X = [train_X[i] for i in sel]
        train_y = [train_y[i] for i in sel]
        sampled = int(_SVM_TRAIN_CAP)

    # —— 6. torch LSTM 训练(CPU;定种子可复现)——
    import torch
    from torch import nn as _nn
    torch.manual_seed(0)
    try:
        torch.set_num_threads(4)
        Xtr = torch.tensor(np.asarray(train_X, dtype="float32"))            # (N, L, F)
        ytr = torch.tensor(np.asarray(train_y, dtype="float32")).view(-1, 1)
        Xte = torch.tensor(np.asarray(test_X, dtype="float32"))

        class _LSTMNet(_nn.Module):
            def __init__(self, n_features: int, hid: int, nl: int):
                super().__init__()
                self.lstm = _nn.LSTM(n_features, hid, nl, batch_first=True)
                self.head = _nn.Linear(hid, 1)

            def forward(self, x):
                out, _h = self.lstm(x)
                return self.head(out[:, -1, :])      # 取序列末步隐态 → 标量预测

        net = _LSTMNet(len(cols), hidden, n_layers)
        opt = torch.optim.Adam(net.parameters(), lr=lr)
        lossfn = _nn.MSELoss()
        net.train()
        bs = 256
        N = int(Xtr.shape[0])
        for _ep in range(epochs):
            perm = torch.randperm(N)
            for i in range(0, N, bs):
                bidx = perm[i: i + bs]
                opt.zero_grad()
                loss = lossfn(net(Xtr[bidx]), ytr[bidx])
                loss.backward()
                opt.step()
        net.eval()
        with torch.no_grad():
            pred = net(Xte).view(-1).cpu().numpy().astype("float64")
    except Exception as exc:  # noqa: BLE001
        return _fail_model(universe, f"lstm_error: {type(exc).__name__}: {exc}",
                           kind=kind, start=start, end=end,
                           n_train_dates=n_train_dates, n_test_dates=n_test_dates)

    pred_s = pd.Series(
        pred, name="pred",
        index=pd.MultiIndex.from_tuples(test_idx, names=["datetime", "code"]),
    )

    # —— 7. 共享 OOS 尾段(reindex → build_report → 完整报告 + 标量 metrics)——
    hyper = {"lookback": lookback, "hidden": hidden, "layers": n_layers,
             "lr": lr, "epochs": epochs}
    return _oos_model_response(
        body, kind, "torch", universe, panel, pred_s, label_s,
        feature_names, features, freq, fwd_days, start, end, hyper, [],
        len(train_X), len(test_X), n_train_dates, n_test_dates,
        train_frac, sampled, preview_rows,
    )


# ════════════════════════════════════════════════════════════════════════════
# P4 —— 多因子构建后端(/factor/pca、/factor/spearman)。两端点共享一条流程:
# 复用 /feature/build 同款 _materialize_xy(fe_spec → 已 winsorize/zscore 的多特征
# 矩阵 X)→ 把多列 X 压成**一条截面因子 Series**(PCA 取主成分得分;Spearman 取
# 各特征对前向收益的 rank-IC 加权合成)→ reindex 整面板喂 build_report 得 OOS
# FactorReport(真 IC/分层/多空,同 /factor/report 形)+ headline ic_analysis。
# 仅「X → 因子 Series」一处岔开,尾段(_factor_eval)完全共用。诚实失败 ok:False +
# reason(HTTP 200),不抛 500。数据只经 _materialize_xy 内的 get_default_loader /
# resolve_universe_codes(全延迟 import,不碰 engine//fa-watch-wt/stocks)。
# 守秒级:默认 csi_fast(小池)+ 短窗(默认今天−1y,同 P3)。
# 库门禁:PCA 需 sklearn,Spearman 优先 scipy.stats.spearmanr,缺则退 pandas rank+corr
# (恒可用,故 Spearman 无硬库门禁;scipy 在 venv 已装)。
# ════════════════════════════════════════════════════════════════════════════


def _jsonable(obj: Any) -> Any:
    """递归把 dataclasses.asdict 结构里 NaN/Inf → None、numpy 标量 → 原生(供浏览器
    JSON.parse 不挂),逐字对齐引擎 buddy/server.py:_jsonable。FactorReport 经
    ``asdict`` 后过此函数,得到与 /factor/report 完全同形的可序列化嵌套字典。"""
    try:
        import numpy as _np
        if isinstance(obj, _np.generic):
            obj = obj.item()
    except Exception:  # noqa: BLE001  —— numpy 缺失不影响纯 Python 分支
        pass
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _fail_factor(universe: str, reason: str, **extra: Any) -> JSONResponse:
    """P4 因子端点诚实失败空壳(HTTP 200),顶层带 /factor/report 同名空块
    (ic/quantile/portfolio/characteristics)使前端 ResultsDrawer 安全降级。"""
    payload: Dict[str, Any] = {
        "ok": False,
        "universe": universe,
        "n_dates": 0,
        "feature_names": [],
        "ic": {},
        "quantile": {},
        "portfolio": {},
        "characteristics": {},
        "warnings": [],
        "status": "error",
        "reason": reason,
    }
    payload.update(extra)
    return JSONResponse(payload)


class PCAFactorIn(ModelTrainIn):
    """``POST /factor/pca`` 入参:继承 ``ModelTrainIn`` 全部 fe_spec 透传字段
    (features/feature/label/fwd_days/universe/start/end/freq/winsorize…)+ PCA 专有
    ``k``/``component``。``kind`` 继承但不用;``label`` 仅供评测口径回显,PCA 本身是纯
    无监督降维 X(不需标签)。前端 pca 节点 params 仅 ``k``,故 component 默认取 PC1。"""

    k: int = 5            # 主成分数(前端 pca.params.k);裁到 [1, n_features]
    component: int = 1    # 取第几主成分作因子(1-based,默认 PC1)


class SpearmanFactorIn(ModelTrainIn):
    """``POST /factor/spearman`` 入参:继承 ``ModelTrainIn`` 全部 fe_spec 透传字段。
    Spearman 因子 = 各特征对前向收益的(全样本)rank-IC 作权重,对 z-score 后的特征
    截面加权合成的复合因子(IC 正贡献的特征正权、负贡献的负权,自动方向对齐)。
    前端 spearman 节点无 params,全部走 fe_spec + 缺省。"""

    pass


class BacktestVectorIn(ModelTrainIn):
    """``POST /backtest/vector`` 入参:继承 ``ModelTrainIn`` 全部 fe_spec 透传字段
    (features/feature/label/fwd_days/universe/start/end/freq/winsorize/winsorize_q/
    standardize)+ 回测专有参数。

    因子来源**统一走 ``features``**(fe_spec 透传,与 P4 同):上游公式/因子库因子 →
    前端把其表达式放进 ``features:[expr]``;pca/spearman/mf 复合因子(顶层带报告而无
    原始表达式)→ 前端把其 ``_label`` 当合成表达式放进 ``features``。后端 ``_materialize_xy``
    统一处理(注册名优先,否则编译 zoo-DSL 表达式),**无需分支**。多 feature 时按等权
    z-score 合成一条截面因子(与 Spearman 退化等权同口径)。

    前端 backtest 节点 params 仅 ``cash``/``topn``;其余取缺省。"""

    topn: int = 30           # 每调仓期等权持有的 TopN 只(按因子值 nlargest)
    cash: float = 1_000_000  # 初始资金(仅供 trades 名义市值回显;净值是归一倍数, 不受 cash 影响)
    long_short: bool = False # True → 多空(TopN 多 − BottomN 空, 美元中性);默认纯多头
    cost_bps: float = 0.0    # 单边换手交易成本(基点);默认 0(老调用兼容,叠加进合成成本)
    commission: float = 0.0003   # 佣金率(双边按成交额;A股约万三)
    stamp_tax: float = 0.0005    # 印花税(仅卖出腿,A股 0.05%;_topn_portfolio 内按换手近似,偏小)
    slippage_bps: float = 5.0    # 滑点(基点,双边)
    rebalance: str = "month"     # 调仓频率 day/week/month(默认月频 → 根治日频 ppy=252 的年化虚高)
    weighting: str = "equal"     # W5-1→回测:持仓定权 equal|mktcap|inv_vol|risk_parity(非等权按目标权重序列回测)
    vol_forecast: str = "hist"   # (b)预测波动钩子:hist=历史滚动波动(默认,行为不变)|ewma|garch=用预测波动喂 inv_vol/min_var 等定权
    max_weight: float = 0.0      # 单票权重上限(0=不限;>0 截断后重归一,逐期生效)
    industry_neutral: bool = False  # True → 逐期行业内等权(近似行业中性)
    n_groups: int = 10       # 透传 build_report 的分位数(ic/quantile 因子视图用,默认 10)
    with_trades: bool = True # True → 附逐调仓期 TopN 持仓进出场(trades 块);默认开(回测要看持仓)
    trades_cap: int = 24     # trades 回显期数上限(取最近 N 期, 控体积)


class PortfolioBuildIn(ModelTrainIn):
    """``POST /portfolio/build`` 入参:继承 ``ModelTrainIn`` 全部 fe_spec 透传字段
    (features/feature/label/universe/start/end/freq/winsorize…)+ 组合构建专有参数。

    因子来源统一走 ``features``(与 backtest 同口径,多 feature 等权 z-score 合成一条截面
    因子)。在**最新调仓截面**上取因子 ``nlargest(topn)`` 作目标持仓,按 ``weighting`` 定权:
      equal       等权 1/n
      mktcap      市值加权(∝ total_mv)
      inv_vol     反波动加权(∝ 1/σ,σ=区间日收益标准差)
      risk_parity 风险平价(本期**近似 = 反波动**,未用协方差矩阵 → 诚实标注 approx)
    可选 ``industry_neutral`` 行业内再平衡(近似中性)、``max_weight`` 单票上限(截断后重归一)。
    产出**最新一期目标权重**(下单/展示用),**不回测**该权重序列(honest:回测节点仍按
    TopN 等权)。缺列(total_mv/returns/industry)则对应权重法降级等权 + 告警,绝不谎报。"""

    topn: int = 30                 # 目标持仓数(因子 nlargest)
    weighting: str = "equal"       # equal | mktcap | inv_vol | risk_parity | min_var | max_sharpe | true_risk_parity
    vol_forecast: str = "hist"     # (b)预测波动钩子:hist(默认,行为不变)| ewma | garch
    max_weight: float = 0.0        # 单票权重上限(0 = 不限;>0 截断后重归一)
    industry_neutral: bool = False # True → 行业内等权分配(近似行业中性)


def _factor_eval(body: "ModelTrainIn", universe: str, panel, pred_s,
                 feature_names: List[str], start: str, end: str, freq: str,
                 fwd_days: int, factor_label: str, method: str,
                 extra: Dict[str, Any]) -> JSONResponse:
    """PCA / Spearman 共用尾段:截面因子 Series → OOS build_report + headline IC →
    组装与 /factor/report 同顶层形(ic/quantile/portfolio/characteristics/meta/
    warnings/status)+ P4 额外字段(ok/universe/n_dates/feature_names/method/fe/
    composite + 调用方 extra)。

    ``pred_s``: 截面因子 Series(MultiIndex(datetime,code),可只在部分行有值)。
    reindex 整面板后用 ``lambda p: pred_full`` 喂 build_report(与 P3 _train_eval
    第 8 步同款)。``composite:true`` 标记 → 前端 analysis 节点命中透传分支(因子无
    expr,靠此直接当终端报告渲染)。任何阶段失败 → ok:False + reason(HTTP 200)。
    """
    from dataclasses import asdict as _asdict
    from financial_analyst.factors.eval.report import build_report, forward_simple_returns
    from financial_analyst.factors.eval.config import EvalConfig
    from financial_analyst.factors.eval.ic import ic_analysis

    pred_full = pred_s.reindex(panel.df.index)
    cfg = EvalConfig(
        universe=universe, freq=freq, start=start, end=end, fwd_days=fwd_days,
        winsorize_q=(body.winsorize_q if body.winsorize else 0.0),
        standardize=bool(body.standardize),
    )
    try:
        report = build_report(panel, lambda p: pred_full, cfg, factor_label, "composite")
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"build_report_error: {type(exc).__name__}: {exc}",
                            start=start, end=end, method=method,
                            feature_names=feature_names, **extra)
    if getattr(report, "status", "") != "ok":
        return _fail_factor(universe,
                            f"report_{report.status}: {getattr(report, 'error', '') or '评测退化'}",
                            start=start, end=end, method=method,
                            feature_names=feature_names, **extra)

    rep_dict = _jsonable(_asdict(report))   # 与 /factor/report 完全同形(NaN→null)

    # headline IC:因子(有值行) vs 同行前向收益(独立再算一遍,口径与 build_report 内部一致)
    fwd = forward_simple_returns(panel, fwd_days)
    fwd_oos = fwd.reindex(pred_s.index)
    ic_head = ic_analysis(pred_s, fwd_oos)

    n_dates = int(report.meta.n_dates)
    warnings: List[str] = list(report.warnings or [])
    if n_dates < 12:
        warnings.append(f"OOS 有效调仓期太短 ({n_dates} < 12), 结论不稳健。")

    fe_spec = {
        "features": extra.get("features_exprs") or feature_names,
        "feature_names": feature_names,
        "label": None if (body.label or "").strip().lower() in _LABEL_FWD_TOKENS else (body.label or "").strip(),
        "fwd_days": fwd_days,
        "universe": universe,
        "start": start,
        "end": end,
        "freq": freq,
        "winsorize": bool(body.winsorize),
        "winsorize_q": float(body.winsorize_q),
        "standardize": bool(body.standardize),
    }
    extra.pop("features_exprs", None)   # 仅内部用于 fe_spec.features,不外泄到顶层

    out: Dict[str, Any] = {
        "ok": True,
        "universe": universe,
        "method": method,
        "n_dates": n_dates,
        "n_codes": int(report.meta.n_codes),
        "feature_names": feature_names,
        "headline_ic": {
            "rank_ic": _num(ic_head.rank_ic_mean),
            "rank_icir": _num(ic_head.rank_icir),
            "ic": _num(ic_head.ic_mean),
            "icir": _num(ic_head.icir),
            "ic_tstat": _num(ic_head.ic_tstat),
        },
        # —— /factor/report 同形顶层块(前端 ResultsDrawer / analysis 直接消费)——
        "meta": rep_dict.get("meta"),
        "ic": rep_dict.get("ic") or {},
        "quantile": rep_dict.get("quantile") or {},
        "portfolio": rep_dict.get("portfolio") or {},
        "characteristics": rep_dict.get("characteristics") or {},
        "warnings": warnings,
        "status": "ok",
        # —— 完整报告 + composite 标记(因子无 expr,前端 analysis 靠 composite 透传)——
        "report": rep_dict,
        "composite": True,
        "fe": fe_spec,
    }
    out.update(extra)
    return JSONResponse(out)


def _pca_factor(body: "PCAFactorIn") -> JSONResponse:
    """PCA 因子构建:fe_spec 重建多特征 X(_materialize_xy)→ sklearn PCA 降维取第
    component 主成分得分作截面因子 → _factor_eval 出 OOS 报告。

    1. 库门禁:sklearn 未装 → ok:False「sklearn 未装」。
    2. 归一特征;空 → ok:False。窗口缺省同 P3(今天−1y,守秒级)。
    3. _materialize_xy 复用 /feature/build 链 → panel + fe_df(已 winsorize/zscore 的 X)。
    4. X = fe_df[feature_names].dropna();不足 → ok:False。k 裁到 [1,n_features]。
       StandardScaler → PCA(k).fit_transform → 主成分得分;取第 component 列作因子 Series。
       符号对齐:让该主成分载荷绝对值最大的特征贡献为正(方向稳定,避免随机翻转)。
    5. _factor_eval:reindex 整面板 build_report(真 IC/分位/多空)+ headline IC。
       返回顶层 ic/quantile/portfolio/characteristics + explained_variance_ratio/k 等。
    任何阶段失败 → ok:False + reason(HTTP 200)。
    """
    import importlib.util as _ilu
    import numpy as np
    import pandas as pd

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    if _ilu.find_spec("sklearn") is None:
        return _fail_factor(universe, "sklearn 未装 (pip install scikit-learn)", method="pca")

    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_factor(universe, "因子不能为空", method="pca")

    freq = (body.freq or "day").strip() or "day"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    fwd_days = max(1, int(body.fwd_days or 5))

    mat = _materialize_xy(body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat
    panel, fe_df, _label_s, feature_names = mat

    X_df = fe_df[feature_names].dropna()             # PCA 需稠密无 NaN
    if X_df.shape[0] < 2 or X_df.shape[1] < 1:
        return _fail_factor(universe, "PCA 输入为空 (特征矩阵 dropna 后行/列不足)",
                            method="pca", start=start, end=end, feature_names=feature_names)

    n_feat = int(X_df.shape[1])
    k = max(1, min(int(body.k or 1), n_feat))
    comp_idx = max(1, min(int(body.component or 1), k)) - 1

    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler

    try:
        Xs = StandardScaler().fit_transform(X_df.to_numpy("float64"))   # X 已 zscore,再标准化保险
        pca = PCA(n_components=k, random_state=0)
        scores = pca.fit_transform(Xs)               # (n_rows, k)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"pca_error: {type(exc).__name__}: {exc}",
                            method="pca", start=start, end=end, feature_names=feature_names)

    evr = [_num(v) for v in np.asarray(pca.explained_variance_ratio_, dtype="float64")]
    comp_scores = scores[:, comp_idx].astype("float64")

    # 符号对齐:载荷绝对值最大的特征,使其在该主成分上贡献为正 → 因子方向稳定可复现。
    loadings = np.asarray(pca.components_[comp_idx], dtype="float64")
    if loadings.size and loadings[int(np.argmax(np.abs(loadings)))] < 0:
        comp_scores = -comp_scores

    pred_s = pd.Series(comp_scores, index=X_df.index, name="pca")
    label = f"PCA[PC{comp_idx + 1}/{k}]({'+'.join(features)})"
    extra = {
        "k": k,
        "component": comp_idx + 1,
        "n_features": n_feat,
        "explained_variance_ratio": evr,
        "features_exprs": features,
    }
    return _factor_eval(body, universe, panel, pred_s, feature_names, start, end,
                        freq, fwd_days, label, "pca", extra)


def _spearman_factor(body: "SpearmanFactorIn") -> JSONResponse:
    """Spearman 因子构建:fe_spec 重建多特征 X → 各特征对前向收益的 rank-IC 作权重,
    对截面特征加权合成一条复合因子 → _factor_eval 出 OOS 报告。

    1. 归一特征;空 → ok:False。窗口缺省同 P3。(Spearman 无硬库门禁:优先 scipy.stats
       .spearmanr,缺则退 pandas rank+corr,恒可用。)
    2. _materialize_xy → panel + fe_df(已 winsorize/zscore 的 X)。
    3. 评测口径前向收益 fwd;逐特征对 fwd 算(全样本)Spearman rank-IC w_i(scipy 优先,
       退 pandas);权重归一(∑|w|=1)。空/全 NaN → ok:False。
    4. 复合因子 = Σ_i w_i · f_i(z-score 后的特征截面加权,IC 负贡献特征自动负权对齐方向)。
    5. _factor_eval:reindex 整面板 build_report + headline IC。返回顶层报告 + 每特征
       rank-IC 权重(spearman_weights)。
    任何阶段失败 → ok:False + reason(HTTP 200)。
    """
    import importlib.util as _ilu
    import numpy as np
    import pandas as pd

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_factor(universe, "因子不能为空", method="spearman")

    freq = (body.freq or "day").strip() or "day"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    fwd_days = max(1, int(body.fwd_days or 5))

    mat = _materialize_xy(body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat
    panel, fe_df, _label_s, feature_names = mat

    from financial_analyst.factors.eval.report import forward_simple_returns
    fwd = forward_simple_returns(panel, fwd_days)

    have_scipy = _ilu.find_spec("scipy") is not None

    def _spearman_ic(f: pd.Series, r: pd.Series) -> Optional[float]:
        """单特征 vs 前向收益的全样本 Spearman rank-IC。scipy 优先,退 pandas rank+corr。"""
        j = pd.concat([f.rename("a"), r.rename("f")], axis=1).dropna()
        if len(j) < 3:
            return None
        if have_scipy:
            try:
                from scipy.stats import spearmanr
                rho, _p = spearmanr(j["a"].to_numpy("float64"), j["f"].to_numpy("float64"))
                return _num(rho)
            except Exception:  # noqa: BLE001  —— scipy 异常 → 退 pandas
                pass
        return _num(j["a"].corr(j["f"], method="spearman"))

    weights: List[float] = []
    weight_rows: List[Dict[str, Any]] = []
    for nm, expr in zip(feature_names, features):
        w = _spearman_ic(fe_df[nm], fwd)
        wv = float(w) if w is not None else 0.0
        weights.append(wv)
        weight_rows.append({"feature": nm, "expr": expr, "rank_ic": w})

    wabs = float(np.sum(np.abs(weights)))
    if wabs <= 0.0:
        return _fail_factor(universe, "Spearman 权重全 0 (各特征对前向收益 rank-IC 退化/样本不足)",
                            method="spearman", start=start, end=end,
                            feature_names=feature_names, spearman_weights=weight_rows)
    w_norm = [w / wabs for w in weights]              # ∑|w|=1

    # 复合因子 = Σ w_i · f_i(特征已 z-score;负 IC 特征自动负权 → 方向与未来收益对齐)
    comp = None
    for nm, wv in zip(feature_names, w_norm):
        term = fe_df[nm] * wv
        comp = term if comp is None else comp.add(term, fill_value=0.0)
    comp = comp.dropna()
    if comp.empty:
        return _fail_factor(universe, "Spearman 复合因子为空 (加权后全 NaN)",
                            method="spearman", start=start, end=end,
                            feature_names=feature_names, spearman_weights=weight_rows)

    pred_s = comp.rename("spearman")
    for i, r in enumerate(weight_rows):
        r["weight"] = _num(w_norm[i])
    label = f"Spearman({'+'.join(features)})"
    extra = {
        "n_features": len(feature_names),
        "spearman_weights": weight_rows,
        "scipy": bool(have_scipy),
        "features_exprs": features,
    }
    return _factor_eval(body, universe, panel, pred_s, feature_names, start, end,
                        freq, fwd_days, label, "spearman", extra)


# ════════════════════════════════════════════════════════════════════════════
# P5 —— 向量化回测后端(/backtest/vector)。因子 → 逐调仓期 nlargest(topn) 等权 →
# 真净值 + 年化/Sharpe/回撤/Calmar/换手/胜率。一处岔开:把 long_short_portfolio 的
# 「top 组 − bottom 组」换成「因子 nlargest(topn) 等权」(可选多空)。复用 _materialize_xy
# (fe_spec → 已 winsorize/zscore 多特征矩阵, 等权 z-score 合成一条因子)+ build_report
# (取诚实 ic/quantile/characteristics 因子视图 + benchmark_nav)+ portfolio_stats
# (年化/Sharpe/MDD/Calmar/胜率)。返回 /factor/report 兼容顶层形(portfolio 块用真
# TopN 账户覆盖)+ 回测专有(topn/cash/trades)+ composite/_compose → 前端终端送抽屉。
# 诚实失败 ok:False + reason(HTTP 200)。数据只经 _materialize_xy 内 get_default_loader /
# resolve_universe_codes(全延迟 import,不碰 engine//fa-watch-wt/stocks)。守秒级:默认
# csi_fast(小池)+ 短窗(默认今天−1y,同 P3/P4)。
# ════════════════════════════════════════════════════════════════════════════


# 调仓频率 → 前向收益窗口(交易日):月频≈21日, 使每期收益≈持有期收益、消除日频重叠过采样。
_FWD_BY_REBAL = {"day": 1, "week": 5, "month": 21}


def _forecast_vol_series(df, asof, codes, model="ewma", cache=None,
                         lookback=500, refit_days=90):
    """(b) 每只票截至 ``asof``(含)的**下一期预测波动 σ**(EWMA / GARCH(1,1));无前视。
    返回 ``pd.Series(code → σ)``;数据不足(<``volmodel.MIN_OBS``)的票留 NaN(调用方退历史波动)。
    ``cache``(可选 dict,``_topn_portfolio`` 跨调仓日持有)按 code 缓存 GARCH 参数 + 上次拟合 asof,
    距上次 >``refit_days`` 天才重拟合(季度级),其间复用参数只按最新收益重算 σ → 把逐调仓日逐票
    数千次 MLE 压到几百次。EWMA 无需拟合(无 cache)。"""
    import numpy as np
    import pandas as pd

    from guanlan_v2.workflow.volmodel import MIN_OBS, forecast_sigma

    out: Dict[Any, float] = {}
    try:
        dts = df.index.get_level_values("datetime")
        sub = df.loc[dts <= asof]
        if "returns" in df.columns:
            ser = sub["returns"]
        elif "close" in df.columns:
            ser = sub["close"].groupby(level="code").pct_change(fill_method=None)
        else:
            return pd.Series(dtype=float)
        R = ser.unstack("code")
        asof_ts = pd.Timestamp(asof)
        use_garch = (model == "garch")
        for c in list(codes):
            if c not in R.columns:
                out[c] = np.nan
                continue
            col = R[c].dropna()
            if len(col) > lookback:
                col = col.iloc[-lookback:]
            if len(col) < MIN_OBS:
                out[c] = np.nan                       # 样本不足 → 退历史波动
                continue
            arr = col.to_numpy(dtype=float)
            if use_garch and isinstance(cache, dict):
                ent = cache.get(c)
                cached = None
                if ent is not None and (asof_ts - ent[0]).days <= refit_days:
                    cached = ent[1]                   # 季度内复用参数,跳过重拟合
                sigma, params = forecast_sigma(arr, model="garch", cached_params=cached)
                if cached is None and params is not None:
                    cache[c] = (asof_ts, params)      # 新拟合 → 记缓存(锚定本次 asof)
            else:
                sigma, _ = forecast_sigma(arr, model=("garch" if use_garch else "ewma"))
            out[c] = float(sigma) if (sigma and np.isfinite(sigma) and sigma > 0) else np.nan
    except Exception:  # noqa: BLE001  预测波动失败 → 全空,调用方退历史波动(诚实降级)
        return pd.Series(dtype=float)
    return pd.Series(out, dtype=float)


def _cross_weights(codes, df, asof, weighting="equal", max_weight=0.0,
                   industry_neutral=False, vol_asof=None, scores=None,
                   vol_forecast="hist", garch_cache=None):
    """给定一个截面(asof 日的 ``codes``)按 ``weighting`` 定权 → 返回
    ``(weights: pd.Series(index=codes,∑=1), used_weighting: str, did_ind: bool, warnings: list)``。

    weighting: equal | mktcap(∝total_mv) | inv_vol(∝1/σ) | risk_parity(≈反波动,近似)。
    ``vol_asof``:可选预算好的 code→波动率(反波动用);为 None 则用 df 中 **asof 及之前** 的
    close 日收益标准差(无前视)。行业中性(近似:行业内按基础权重、各行业等权)+ 单票上限
    (水填充截断后重归一)。缺列则对应法降级等权 + 告警(诚实,绝不谎报)。

    抽出自 ``_portfolio_build`` 的定权块,供其与 ``_topn_portfolio`` 逐期回测**共用同一口径**。"""
    import numpy as np
    import pandas as pd
    codes = list(codes)
    warnings: List[str] = []
    used = weighting

    def _col_asof(col):
        if col not in df.columns:
            return None
        try:
            return df[col].xs(asof, level="datetime").reindex(codes)
        except Exception:  # noqa: BLE001
            return None

    def _returns_matrix(lookback=252, min_obs=30):
        """选中票截至 asof(含)的日收益矩形矩阵(行=日期, 列=codes 子集);无前视。
        返回 ``(R: pd.DataFrame(T×N), used_codes: list)``;不可建 → ``(None, [])``。
        去 NaN 太多的列(<60% 有效)+ 含 NaN 的行 → 喂协方差估计的干净矩形。"""
        try:
            dts = df.index.get_level_values("datetime")
            sub = df.loc[dts <= asof]
            if "returns" in df.columns:
                ser = sub["returns"]
            elif "close" in df.columns:
                ser = sub["close"].groupby(level="code").pct_change(fill_method=None)
            else:
                return None, []
            R = ser.unstack("code")
            keep = [c for c in codes if c in R.columns]
            if len(keep) < 2:
                return None, []
            R = R.reindex(columns=keep).tail(lookback)
            R = R.dropna(axis=1, thresh=max(min_obs, int(0.6 * len(R))))  # 列:≥60%有效
            R = R.dropna(axis=0, how="any")                               # 行:对齐为矩形
            if R.shape[0] < min_obs or R.shape[1] < 2:
                return None, []
            return R, list(R.columns)
        except Exception:  # noqa: BLE001
            return None, []

    base = pd.Series(1.0, index=codes)
    if weighting == "mktcap":
        mv = _col_asof("total_mv")
        if mv is None or mv.dropna().empty or bool((mv.fillna(0.0) <= 0).all()):
            warnings.append("total_mv 缺失 → 市值加权降级等权")
            used = "equal"
        else:
            base = mv.clip(lower=0).fillna(0.0)
    elif weighting in ("inv_vol", "risk_parity"):
        vol = vol_asof.reindex(codes) if vol_asof is not None else None
        if vol_forecast in ("ewma", "garch"):   # (b) 预测波动钩子:覆盖历史波动(不足票退历史)
            fvol = _forecast_vol_series(df, asof, codes, model=vol_forecast, cache=garch_cache)
            if fvol is not None and not fvol.dropna().empty:
                vol = fvol.reindex(codes).combine_first(vol) if vol is not None else fvol.reindex(codes)
                warnings.append(f"反波动/风险平价用 {vol_forecast} 预测波动(vol_asof 钩子;不足票退历史)")
        if vol is None:
            try:
                _dts = df.index.get_level_values("datetime")
                if "returns" in df.columns:   # 面板带成品 returns 列(部分 loader)
                    vol = df.loc[_dts <= asof, "returns"].groupby(level="code").std().reindex(codes)
                elif "close" in df.columns:   # 否则 close 日收益标准差(≤asof,无前视)
                    vol = df.loc[_dts <= asof, "close"].groupby(level="code").apply(
                        lambda x: x.pct_change(fill_method=None).std()).reindex(codes)
            except Exception:  # noqa: BLE001
                vol = None
        if vol is None or vol.dropna().empty or bool((vol.fillna(0.0) <= 0).all()):
            warnings.append("收益波动不可估 → 反波动/风险平价降级等权")
            used = "equal"
        else:
            inv = 1.0 / vol.replace(0.0, np.nan)
            fill = float(inv[inv > 0].min()) if bool((inv > 0).any()) else 1.0
            base = inv.fillna(fill)
        if weighting == "risk_parity":
            warnings.append("风险平价为近似(= 反波动,未用协方差矩阵)")
    elif weighting in ("min_var", "max_sharpe", "true_risk_parity"):
        # 真协方差优化:建选中票截至 asof 的收益矩阵 → Ledoit-Wolf Σ → 优化权重(portfolio_opt)。
        R, used_codes = _returns_matrix()
        w_opt, note = None, ""
        if R is not None and len(used_codes) >= 2:
            try:
                from guanlan_v2.workflow.portfolio_opt import optimize_weights
                mu = None
                if weighting == "max_sharpe" and scores is not None:
                    try:
                        mu = pd.Series(scores).reindex(used_codes).to_numpy(dtype=float)
                    except Exception:  # noqa: BLE001
                        mu = None
                tvols = None
                if vol_forecast in ("ewma", "garch"):   # (b) 预测波动注入 Σ 对角(保历史相关)
                    fvol = _forecast_vol_series(df, asof, used_codes, model=vol_forecast, cache=garch_cache)
                    if fvol is not None and not fvol.dropna().empty:
                        tvols = fvol.reindex(used_codes).to_numpy(dtype=float)
                w_opt, note = optimize_weights(R.to_numpy(), weighting, mu=mu, target_vols=tvols)
            except Exception as exc:  # noqa: BLE001
                w_opt, note = None, f"{type(exc).__name__}: {exc}"
        else:
            note = "收益矩阵不可建(样本/列不足)"
        if w_opt is None:
            warnings.append(f"{weighting} → 降级等权({note})")
            used = "equal"
        else:
            warnings.append(f"{weighting}:真协方差优化({note})")
            base = pd.Series(w_opt, index=used_codes).reindex(codes).fillna(0.0)
    elif weighting == "black_litterman":
        # #9 Black-Litterman:市值均衡先验 Π=δΣw_mkt + 因子/LLM 观点(scores)→ 贝叶斯后验收益 → 长仓 MV 最优。
        #   观点=每票绝对视图 Q=Π+λ·z(score)·std(Π)(z=截面标准化因子分,λ=1),P=单位、Ω=τ·diag(Σ);
        #   无 scores → 退纯均衡(=市值权重);无市值 → w_mkt 退等权。**后验是观点+均衡贝叶斯混合,非确定性回测**。
        import numpy as _np
        R, used_codes = _returns_matrix()
        w_bl, note = None, ""
        if R is not None and len(used_codes) >= 2:
            try:
                from guanlan_v2.workflow.portfolio_opt import shrink_cov, black_litterman_weights
                Sigma, cov_note = shrink_cov(R.to_numpy())
                mv = _col_asof("total_mv")
                wm = mv.reindex(used_codes).clip(lower=0).fillna(0.0) if mv is not None else None
                if wm is None or float(wm.sum()) <= 0:
                    wm = pd.Series(1.0, index=used_codes); note += "·w_mkt 缺市值退等权"
                wm = (wm / float(wm.sum())).to_numpy(dtype=float)
                P = Q = Om = None
                if scores is not None:
                    sc = pd.Series(scores).reindex(used_codes).astype(float)
                    if int(sc.notna().sum()) >= 2 and float(sc.std(ddof=0) or 0) > 0:
                        z = ((sc - sc.mean()) / sc.std(ddof=0)).fillna(0.0).to_numpy()
                        Pi = 2.5 * (Sigma @ wm)
                        sprd = float(_np.std(Pi)) or float(_np.sqrt(_np.mean(_np.clip(_np.diag(Sigma), 0, None))))
                        K = len(used_codes)
                        P = _np.eye(K); Q = Pi + 1.0 * z * sprd
                        Om = _np.diag(0.05 * _np.clip(_np.diag(Sigma), 1e-12, None))
                if P is None:
                    note += "·无因子观点(退纯均衡=市值)"
                    P = _np.zeros((0, len(used_codes))); Q = _np.zeros(0); Om = _np.zeros((0, 0))
                w_bl = black_litterman_weights(Sigma, wm, P, Q, Om, delta=2.5, tau=0.05)
                note = (cov_note + note) if w_bl is not None else (note or "BL 后验/解非法")
            except Exception as exc:  # noqa: BLE001
                w_bl, note = None, f"{type(exc).__name__}: {exc}"
        else:
            note = "收益矩阵不可建(样本/列不足)"
        if w_bl is None:
            warnings.append(f"black_litterman → 降级等权({note})"); used = "equal"
        else:
            warnings.append(f"black_litterman:市值均衡先验+因子观点贝叶斯后验(非确定性回测·{note})")
            base = pd.Series(w_bl, index=used_codes).reindex(codes).fillna(0.0)
    if used == "equal":
        base = pd.Series(1.0, index=codes)
    base = base.clip(lower=0)
    if float(base.sum()) <= 0:
        base = pd.Series(1.0, index=codes)

    ind = _col_asof("industry") if industry_neutral else None
    did_ind = bool(industry_neutral and ind is not None and ind.notna().any())
    if did_ind:
        ind2 = ind.fillna("NA")
        w = pd.Series(0.0, index=codes)
        grp = ind2.groupby(ind2).groups
        n_ind = max(1, len(grp))
        for _gname, idx in grp.items():
            sub = base.reindex(idx).fillna(0.0).clip(lower=0)
            ssum = float(sub.sum())
            share = (sub / ssum) if ssum > 0 else pd.Series(1.0 / len(idx), index=idx)
            w.loc[idx] = share.values * (1.0 / n_ind)
        weights = w
    else:
        if industry_neutral:
            warnings.append("industry 缺失 → 行业中性跳过")
        weights = base / float(base.sum())

    if max_weight and max_weight > 0:
        if max_weight * len(weights) < 1.0 - 1e-9:
            warnings.append(f"单票上限 {max_weight:.4f} × {len(weights)} 只 < 1,无法满仓 → 上限本次不生效")
        else:
            for _ in range(50):
                over = weights > max_weight + 1e-12
                if not bool(over.any()):
                    break
                excess = float((weights[over] - max_weight).sum())
                weights[over] = max_weight
                under = ~over
                room = float(weights[under].sum())
                if room <= 0:
                    break
                weights[under] = weights[under] + excess * (weights[under] / room)
            weights = weights.clip(upper=max_weight)
    weights = weights / float(weights.sum())
    return weights, used, did_ind, warnings


def _topn_portfolio(alpha_r, fwd_r, topn: int, ppy: int,
                    long_short: bool = False, cost_bps: float = 0.0, stamp_tax: float = 0.0,
                    commission: float = 0.0, slippage_bps: float = 0.0,
                    weighting: str = "equal", panel_df=None,
                    max_weight: float = 0.0, industry_neutral: bool = False,
                    vol_lookback: int = 120, vol_forecast: str = "hist"):
    """逐调仓日取因子 ``nlargest(topn)``,按 ``weighting`` 定权(equal/mktcap/inv_vol/
    risk_parity,经 ``_cross_weights`` 与 portfolio 节点**同一口径**;非等权需传 ``panel_df``),
    组合期收益 = Σ wᵢ·fᵢ(等权时退化为 ``top['f'].mean()``,与旧口径一致)。

    成本**分腿**真算:由相邻两期权重向量差 Δw 拆 买入腿 Σmax(0,Δw) 与 卖出腿 Σmax(0,-Δw),
    买入腿扣 佣金+滑点,卖出腿额外扣 **印花税**(A股仅卖出收,修旧版按对称换手双计的偏差)。
    首期建仓不计成本(同旧口径)。``long_short=True`` 时空腿仍按等权(加权只作用多头,诚实标注),
    成本×2 代理双腿。换手回显 = 各期 (买入腿+卖出腿)/2 均值。复用 ``portfolio_stats`` 算指标。

    ``inv_vol/risk_parity`` 的波动用**截至各调仓日**的滚动窗口(``vol_lookback`` 日)估,无前视。
    返回 ``(PortfolioResult, trades, gross_ann, total_cost)``;空输入 → 诚实空壳。"""
    import numpy as np
    import pandas as pd
    from financial_analyst.factors.eval.portfolio import PortfolioResult, portfolio_stats

    joined = pd.concat([alpha_r.rename("a"), fwd_r.rename("f")], axis=1).dropna()
    if joined.empty:
        return PortfolioResult(), [], float("nan"), 0.0

    weighting = (weighting or "equal").strip().lower()
    if weighting not in ("equal", "mktcap", "inv_vol", "risk_parity",
                         "min_var", "max_sharpe", "true_risk_parity", "black_litterman"):
        weighting = "equal"
    vol_forecast = (vol_forecast or "hist").strip().lower()
    if vol_forecast not in ("hist", "ewma", "garch"):
        vol_forecast = "hist"
    garch_cache = {} if vol_forecast == "garch" else None   # 跨调仓日复用 GARCH 参数(季度重拟合)
    use_weight = (weighting != "equal") and (panel_df is not None) and (not long_short)

    # 反波动/风险平价:一次性预算"截至各调仓日的滚动窗口波动"面板(rolling → 无前视)。
    vol_panel = None
    if use_weight and weighting in ("inv_vol", "risk_parity") and "close" in panel_df.columns:
        try:
            dret = panel_df["close"].groupby(level="code").pct_change(fill_method=None)
            vol_panel = dret.groupby(level="code").transform(
                lambda s: s.rolling(int(vol_lookback), min_periods=20).std())
        except Exception:  # noqa: BLE001
            vol_panel = None

    # 成本费率(小数):买入腿 = 佣金 + 滑点(+兼容旧 cost_bps);卖出腿额外 + 印花税。
    buy_rate = float(commission) + float(slippage_bps) / 1e4 + float(cost_bps) / 1e4
    sell_rate = buy_rate + float(stamp_tax)

    dates = sorted(joined.index.get_level_values("datetime").unique())
    rets: List[float] = []
    gross_rets: List[float] = []   # 扣成本前的逐期收益(算 gross 年化)
    cost_acc = 0.0                  # 累计成本拖累(各期成本之和, 近似总成本)
    turns: List[float] = []
    used: List[Any] = []
    trades: List[Dict[str, Any]] = []
    prev_w = pd.Series(dtype=float)
    for d in dates:
        sl = joined.xs(d, level="datetime")
        if len(sl) == 0:
            continue
        n = max(1, min(int(topn), len(sl)))
        top = sl.nlargest(n, "a")
        codes = list(top.index)
        if use_weight:
            vol_asof = None
            if vol_panel is not None:
                try:
                    vol_asof = vol_panel.xs(d, level="datetime")
                except Exception:  # noqa: BLE001
                    vol_asof = None
            w, _uw, _di, _wn = _cross_weights(codes, panel_df, d, weighting,
                                              max_weight=max_weight,
                                              industry_neutral=industry_neutral,
                                              vol_asof=vol_asof,
                                              scores=top["a"].reindex(codes),
                                              vol_forecast=vol_forecast,
                                              garch_cache=garch_cache)
            w = w.reindex(codes).fillna(0.0)
            r_gross = float((w * top["f"].reindex(codes)).sum())
        else:
            w = pd.Series(1.0 / len(codes), index=codes)
            r_gross = float(top["f"].mean())
        if long_short:
            bot = sl.nsmallest(n, "a")
            r_gross = r_gross - float(bot["f"].mean())
        # 分腿换手:相邻期权重向量差(首期=建仓,不计成本,同旧口径)。
        if len(prev_w):
            allc = prev_w.index.union(w.index)
            dw = w.reindex(allc).fillna(0.0) - prev_w.reindex(allc).fillna(0.0)
            buy_turn = float(dw[dw > 0].sum())
            sell_turn = float(-dw[dw < 0].sum())
            cost = (buy_turn * buy_rate + sell_turn * sell_rate) * (2 if long_short else 1)
            turn_disp = (buy_turn + sell_turn) / 2.0
        else:
            cost = 0.0
            turn_disp = 0.0
        r = r_gross - cost
        rets.append(r)
        gross_rets.append(r_gross)
        cost_acc += cost
        turns.append(turn_disp)
        used.append(d)
        cur = set(codes)
        prev_codes = set(prev_w.index)
        trades.append({
            "date": str(pd.Timestamp(d).date()),
            "enter": sorted(str(c) for c in (cur - prev_codes)),
            "exit": sorted(str(c) for c in (prev_codes - cur)),
            "hold_n": len(cur),
        })
        prev_w = w

    ls = pd.Series(rets, index=pd.Index(used, name="datetime"))
    st = portfolio_stats(ls, ppy)
    gross_ann = (portfolio_stats(pd.Series(gross_rets, index=pd.Index(used, name="datetime")), ppy)["ann_return"]
                 if gross_rets else float("nan"))
    total_cost = float(cost_acc)
    nav = (1 + ls).cumprod()
    nav_series = [(str(pd.Timestamp(d).date()), float(v)) for d, v in nav.items()]
    pf = PortfolioResult(
        nav_series=nav_series, benchmark_nav=None,
        ann_return=st["ann_return"], sharpe=st["sharpe"],
        max_drawdown=st["max_drawdown"], volatility=st["volatility"],
        turnover=float(np.mean(turns)) if turns else float("nan"),
        win_rate=st["win_rate"], calmar=st["calmar"],
    )
    return pf, trades, gross_ann, total_cost


def _w6_perf(nav_series, ann_return, ppy: int, start: str, end: str) -> Dict[str, Any]:
    """W6 绩效补全:从回测净值序列 + 真沪深300 派生 Sortino / 信息比率 / 沪深300 基准净值。

    全部从已有 ``nav_series``([date,nav] 对)+ ``_benchmark_ret_series('csi300')`` 真算,
    **不动引擎**。口径锚定量化 wiki(start/sharpe.md):
      · Sortino = 年化收益 ÷ 年化下行波动(只罚负收益期,σ×√ppy);
      · 信息比率 IR = 年化超额收益 ÷ 跟踪误差(对标**真沪深300**,annualize:mean×ppy / σ×√ppy);
      · bench300_nav = 买入持有沪深300、对齐回测净值日期、归一首日=1(供抽屉叠加 + IR 计算)。
    取不到沪深300(无真实指数)→ IR/基准留空(诚实,不假造)。"""
    out: Dict[str, Any] = {"sortino": None, "information_ratio": None, "bench300_nav": []}
    if not nav_series or len(nav_series) < 3:
        return out
    import pandas as pd
    idx = pd.to_datetime([d for d, _ in nav_series])
    nav = pd.Series([float(v) for _, v in nav_series], index=idx).sort_index()
    port_ret = nav.pct_change().dropna()
    # —— Sortino:年化收益 ÷ 年化下行波动 ——
    downside = port_ret[port_ret < 0]
    if ann_return is not None and ann_return == ann_return and len(downside) > 1:
        dd_vol = float(downside.std(ddof=1)) * (float(ppy) ** 0.5)
        if dd_vol > 0:
            out["sortino"] = float(ann_return) / dd_vol
    # —— 真沪深300 基准 + 信息比率(复用 B2 的 _benchmark_ret_series)——
    s, _w = _benchmark_ret_series("csi300", start, end)
    if _w:
        out["bench_note"] = _w   # 指数源停更等口径提示,随绩效块下发(诚实显形,P0③)
    if s is not None and not s.dropna().empty:
        bnav = (1.0 + s.fillna(0.0)).cumprod()
        # 对齐到回测净值日期(取 ≤ 该日最近的指数净值 ffill),归一首日=1
        b = bnav.reindex(bnav.index.union(idx)).sort_index().ffill().reindex(idx)
        if int(b.notna().sum()) >= 3 and b.iloc[0] == b.iloc[0] and float(b.iloc[0]) != 0:
            b = b / float(b.iloc[0])
            out["bench300_nav"] = [(str(pd.Timestamp(d).date()), float(v))
                                   for d, v in b.items() if v == v]
            bret = b.pct_change().dropna()
            act = (port_ret.reindex(bret.index) - bret).dropna()
            if len(act) > 1:
                te = float(act.std(ddof=1)) * (float(ppy) ** 0.5)
                if te > 0:
                    out["information_ratio"] = float(act.mean() * float(ppy)) / te
    return out


# ── W7 样本外验证 / 过拟合体检 ────────────────────────────────────────────────
#   口径锚定量化 wiki(basic/quant/回测_Backtesting.md + 华泰 AI 系列对抗过拟合):
#   留出样本外,IS(样本内)与 OOS(样本外)同指标并排;**IS≈OOS→稳健,OOS 较 IS 塌缩/反号
#   →疑似过拟合**(数据挖掘虚高的体检)。尾段法:按调仓日/净值点末 oos_frac 作 OOS。
#   全部按日期子集从**已有**因子/收益序列派生 + 复用 ic_analysis/portfolio_stats,**不动引擎**。

def _oos_verdict(is_v, oos_v, n_oos, min_n: int = 6):
    """IS/OOS 同指标 → 过拟合判定。返回 ``(verdict, decay_ratio, label)``。

    decay_ratio = OOS/IS(仅 IS>0 有意义);门槛 0.6/0.2:≥0.6 稳健、≥0.2 衰减、<0.2(含反号)
    疑似过拟合。OOS 期数 < min_n → insufficient(不下结论);IS 无正信号 → na(无从判)。"""
    if n_oos is None or n_oos < min_n:
        return "insufficient", None, "样本外期数不足,暂不下结论"
    if is_v is None or is_v != is_v or oos_v is None or oos_v != oos_v:
        return "na", None, "指标缺失"
    if is_v <= 0:
        return "na", None, "样本内本身无正信号(无从判过拟合)"
    ratio = float(oos_v) / float(is_v)
    if ratio >= 0.6:
        return "robust", ratio, "稳健 · 样本外保持"
    if ratio >= 0.2:
        return "degraded", ratio, "衰减 · 样本外走弱"
    return "overfit", ratio, "疑似过拟合 · 样本外塌缩/反号"


def _rank_ic_series(fac, fwd) -> "List[Any]":
    """#4 逐期 **rank-IC**(Spearman)序列 [[date, ic], ...]。引擎 ic 块只导出 Pearson
    ``ic_series``,而前端 IC 时序图标的是 RankIC(口径名实不符)→ 这里在薄壳用 fac/fwd 自算
    逐调仓日的截面秩相关(= 各日 fac 与 fwd 的 rank 后 Pearson),与"RankIC"标题一致、抗异常值。
    自包含纯 pandas(不依赖引擎私有函数),失败/截面太小(<5)跳过。"""
    import pandas as pd
    try:
        j = pd.concat([fac.rename("a"), fwd.rename("f")], axis=1).dropna()
    except Exception:  # noqa: BLE001
        return []
    if j.empty:
        return []
    out: List[Any] = []
    for d, sub in j.groupby(level="datetime"):
        if len(sub) < 5:
            continue
        c = sub["a"].rank().corr(sub["f"].rank())
        if c == c:   # 非 NaN
            out.append([str(pd.Timestamp(d).date()), round(float(c), 4)])
    return out


def _inject_rank_ic_series(ic_block, fac, fwd, panel=None, freq=None) -> None:
    """把逐期 rank-IC 序列写进 ic 块(就地;ic_block 须是 dict)。供 report2/backtest/compose 共用。
    给 panel+freq → 先把 fac/fwd 限制到**调仓日**(与引擎 ic_series 同粒度,匹配图 x 轴「调仓日」),
    否则用 fac 原生(逐日)粒度。"""
    if not isinstance(ic_block, dict):
        return
    f, fw = fac, fwd
    if panel is not None and freq:
        try:
            from financial_analyst.factors.eval.report import rebalance_dates, _restrict
            reb = rebalance_dates(list(panel.dates()), freq)
            f, fw = _restrict(fac, reb), _restrict(fwd, reb)
        except Exception:  # noqa: BLE001 — 限制失败则退回原生粒度
            f, fw = fac, fwd
    try:
        ic_block["rank_ic_series"] = _rank_ic_series(f, fw)
    except Exception:  # noqa: BLE001
        pass


def _oos_ic_block(fac, fwd, oos_frac) -> Dict[str, Any]:
    """因子 IS/OOS RankIC 体检(按调仓日尾段切分,复用 ic_analysis,不动引擎)。"""
    import pandas as pd
    from financial_analyst.factors.eval.ic import ic_analysis
    out: Dict[str, Any] = {"enabled": False}
    frac = float(oos_frac or 0)
    if frac <= 0 or frac >= 1:
        return out
    lv = fac.index.get_level_values("datetime")
    dts = sorted(pd.Index(lv).unique())
    if len(dts) < 8:
        return {"enabled": True, "verdict": "insufficient", "note": "总调仓期太少,无法切样本外"}
    k = min(len(dts) - 1, max(1, int(round(len(dts) * (1.0 - frac)))))
    is_set, oos_set = set(dts[:k]), set(dts[k:])
    fac_is, fac_oos = fac[lv.isin(is_set)], fac[lv.isin(oos_set)]
    ic_is = ic_analysis(fac_is, fwd.reindex(fac_is.index))
    ic_oos = ic_analysis(fac_oos, fwd.reindex(fac_oos.index))
    verdict, ratio, label = _oos_verdict(ic_is.rank_ic_mean, ic_oos.rank_ic_mean, len(oos_set))
    return {
        "enabled": True, "metric": "rank_ic", "frac": frac,
        "split_date": str(pd.Timestamp(dts[k]).date()),
        "is": {"rank_ic": _num(ic_is.rank_ic_mean), "rank_icir": _num(ic_is.rank_icir), "n_dates": len(is_set)},
        "oos": {"rank_ic": _num(ic_oos.rank_ic_mean), "rank_icir": _num(ic_oos.rank_icir), "n_dates": len(oos_set)},
        "decay_ratio": _num(ratio), "verdict": verdict, "note": label,
    }


def _oos_perf_block(nav_series, ppy, oos_frac) -> Dict[str, Any]:
    """回测 IS/OOS 绩效体检(按净值点尾段切分,复用 portfolio_stats,不动引擎)。"""
    import pandas as pd
    from financial_analyst.factors.eval.portfolio import portfolio_stats
    out: Dict[str, Any] = {"enabled": False}
    frac = float(oos_frac or 0)
    if frac <= 0 or frac >= 1 or not nav_series or len(nav_series) < 8:
        return out
    idx = pd.to_datetime([d for d, _ in nav_series])
    nav = pd.Series([float(v) for _, v in nav_series], index=idx).sort_index()
    ret = nav.pct_change().dropna()
    if len(ret) < 6:
        return {"enabled": True, "verdict": "insufficient", "note": "净值点太少,无法切样本外"}
    k = min(len(ret) - 1, max(1, int(round(len(ret) * (1.0 - frac)))))
    is_r, oos_r = ret.iloc[:k], ret.iloc[k:]
    st_is, st_oos = portfolio_stats(is_r, ppy), portfolio_stats(oos_r, ppy)
    verdict, ratio, label = _oos_verdict(st_is["sharpe"], st_oos["sharpe"], len(oos_r))
    return {
        "enabled": True, "metric": "sharpe", "frac": frac,
        "split_date": str(pd.Timestamp(ret.index[k]).date()),
        "is": {"sharpe": _num(st_is["sharpe"]), "ann_return": _num(st_is["ann_return"]), "n_periods": int(len(is_r))},
        "oos": {"sharpe": _num(st_oos["sharpe"]), "ann_return": _num(st_oos["ann_return"]), "n_periods": int(len(oos_r))},
        "decay_ratio": _num(ratio), "verdict": verdict, "note": label,
    }


# ── W7-2 walk-forward 滚动前进 / W7-3 滚动夏普 ──────────────────────────────
#   口径锚定量化 wiki(华泰「时序交叉验证」对抗过拟合 + long-short.md 滚动夏普)。
#   walk-forward:把评测窗切 K 段连续子区间,逐段算 OOS 指标 + 汇总(均值/正占比)→
#   多次样本外比单次切分更稳(挤掉运气)。滚动夏普:净值上滚动窗口夏普曲线 → 子区间稳定性。
#   全部从已有 fac/fwd/nav 切段派生 + 复用 ic_analysis/portfolio_stats,**不动引擎**。

def _rolling_sharpe(nav_series, ppy) -> "List[Any]":
    """W7-3 滚动夏普:回测净值的滚动窗口夏普曲线(窗口 = max(4,min(12,n//2)))。"""
    import pandas as pd
    if not nav_series or len(nav_series) < 6:
        return []
    idx = pd.to_datetime([d for d, _ in nav_series])
    nav = pd.Series([float(v) for _, v in nav_series], index=idx).sort_index()
    ret = nav.pct_change().dropna()
    if len(ret) < 5:
        return []
    win = max(4, min(12, len(ret) // 2))
    sq = float(ppy) ** 0.5
    out: "List[Any]" = []
    for i in range(win - 1, len(ret)):
        w = ret.iloc[i - win + 1: i + 1]
        sd = float(w.std(ddof=1))
        out.append([str(pd.Timestamp(ret.index[i]).date()), _num((float(w.mean()) / sd * sq) if sd > 0 else 0.0)])
    return out


def _wf_factor(fac, fwd, k: int = 4) -> "Dict[str, Any]":
    """W7-2 walk-forward(因子):调仓日切 K 段连续子区间,逐段 OOS RankIC + 汇总。"""
    import pandas as pd
    from financial_analyst.factors.eval.ic import ic_analysis
    lv = fac.index.get_level_values("datetime")
    dts = sorted(pd.Index(lv).unique())
    if len(dts) < 12:
        return {"enabled": False}
    k = max(2, min(int(k), len(dts) // 4))
    n = len(dts); size = n // k
    segs: "List[Dict[str, Any]]" = []
    for s in range(k):
        a, b = s * size, ((s + 1) * size if s < k - 1 else n)
        seg = set(dts[a:b])
        fseg = fac[lv.isin(seg)]
        ic = ic_analysis(fseg, fwd.reindex(fseg.index))
        segs.append({"period": str(pd.Timestamp(dts[a]).date()) + "~" + str(pd.Timestamp(dts[b - 1]).date()),
                     "rank_ic": _num(ic.rank_ic_mean), "n_dates": len(seg)})
    ics = [s["rank_ic"] for s in segs if s["rank_ic"] is not None]
    return {"enabled": True, "metric": "rank_ic", "n_segments": len(segs), "segments": segs,
            "mean_ic": _num((sum(ics) / len(ics)) if ics else None),
            "pos_ratio": _num((sum(1 for v in ics if v > 0) / len(ics)) if ics else None)}


def _wf_perf(nav_series, ppy, k: int = 4) -> "Dict[str, Any]":
    """W7-2 walk-forward(回测):净值期收益切 K 段,逐段 Sharpe/年化 + 汇总。"""
    import pandas as pd
    from financial_analyst.factors.eval.portfolio import portfolio_stats
    if not nav_series or len(nav_series) < 12:
        return {"enabled": False}
    idx = pd.to_datetime([d for d, _ in nav_series])
    nav = pd.Series([float(v) for _, v in nav_series], index=idx).sort_index()
    ret = nav.pct_change().dropna()
    if len(ret) < 12:
        return {"enabled": False}
    k = max(2, min(int(k), len(ret) // 4))
    n = len(ret); size = n // k
    segs: "List[Dict[str, Any]]" = []
    for s in range(k):
        a, b = s * size, ((s + 1) * size if s < k - 1 else n)
        w = ret.iloc[a:b]
        st = portfolio_stats(w, ppy)
        segs.append({"period": str(pd.Timestamp(ret.index[a]).date()) + "~" + str(pd.Timestamp(ret.index[b - 1]).date()),
                     "sharpe": _num(st["sharpe"]), "ann_return": _num(st["ann_return"]), "n": int(len(w))})
    shs = [s["sharpe"] for s in segs if s["sharpe"] is not None]
    return {"enabled": True, "metric": "sharpe", "n_segments": len(segs), "segments": segs,
            "mean_sharpe": _num((sum(shs) / len(shs)) if shs else None),
            "pos_ratio": _num((sum(1 for v in shs if v > 0) / len(shs)) if shs else None)}


# ── 风险度量(VaR / CVaR / EVT / Kupiec)── nav_series 加法 helper(同 _w6_perf 形)──────
#   口径锚定量化 wiki(基本概念/风险, 入门/EVT在VaR中的应用):VaR=损失分布分位数,
#   CVaR(ES)=尾部条件均值(超过 VaR 的平均损失)。从已有 nav_series 派生组合期收益 →
#   三法 VaR(历史/参数正态/蒙特卡罗)+ CVaR + EVT(POT+GPD 拟合尾部估超高分位)+
#   Kupiec POF VaR 回测。纯 numpy;EVT 惰性 import scipy,不可用则诚实跳过。**不动引擎**。
_RISK_Z = {"95": 1.6448536269514722, "99": 2.3263478740408408}        # 标准正态上分位 z
_RISK_PHI = {"95": 0.10313564037537719, "99": 0.026652194585563254}   # φ(z),正态 CVaR 闭式用


def _risk_block(nav_series, ppy: int) -> Dict[str, Any]:
    """组合损失分布风险度量。``nav_series``=[date,nav] 对(回测净值,频率=调仓频率)。
    单期口径(=调仓频率),另给年化波动标注。短序列(<20 期)→ 诚实留空(VaR 无意义)。"""
    out: Dict[str, Any] = {"enabled": False, "warnings": []}
    if not nav_series or len(nav_series) < 20:
        out["warnings"].append("风险度量需 ≥20 个净值点(组合期收益样本不足);请加宽窗口或用更密调仓频率。")
        return out
    import math
    import numpy as np
    import pandas as pd
    nav = pd.Series([float(v) for _, v in nav_series]).astype(float)
    ret = nav.pct_change().dropna().to_numpy()
    if ret.size < 20:
        out["warnings"].append("有效组合收益样本 <20,诚实留空。")
        return out
    losses = -ret                       # 损失 = 负收益(正数 = 亏)
    mu = float(np.mean(ret)); sd = float(np.std(ret, ddof=1))
    out.update({"enabled": True, "n_periods": int(ret.size), "mean_ret": mu, "vol": sd,
                "ann_vol": sd * (float(ppy) ** 0.5),
                "period_note": "单期口径(=调仓频率);年化:均值×ppy、波动×√ppy"})

    levels: Dict[str, Any] = {}
    for cl, key in ((0.95, "95"), (0.99, "99")):
        hist_var = float(np.quantile(losses, cl))           # 历史模拟 VaR = 损失分布分位
        tail = losses[losses >= hist_var]
        hist_cvar = float(np.mean(tail)) if tail.size else hist_var   # CVaR = 尾部均值
        z = _RISK_Z[key]; phi = _RISK_PHI[key]
        para_var = -mu + z * sd                              # 参数(正态)VaR
        para_cvar = -mu + sd * phi / (1.0 - cl)              # 正态 CVaR 闭式
        levels[key] = {"hist_var": hist_var, "hist_cvar": hist_cvar,
                       "param_var": float(para_var), "param_cvar": float(para_cvar)}
    out["levels"] = levels

    # 蒙特卡罗 VaR(正态重采样,定种子保可复现;wiki 三法之一)
    try:
        rng = np.random.default_rng(20260614)
        sim = -rng.normal(mu, sd, 40000)
        out["mc"] = {"var95": float(np.quantile(sim, 0.95)), "var99": float(np.quantile(sim, 0.99)),
                     "note": "正态蒙特卡罗(40k 抽样·定种子);正态假设下≈参数法"}
    except Exception:  # noqa: BLE001
        pass

    # EVT:POT(90% 阈值)+ GPD 拟合超阈值损失 → 估 99.5%/99.9% 超高分位
    evt: Dict[str, Any] = {"enabled": False}
    try:
        from scipy.stats import genpareto
        thr = float(np.quantile(losses, 0.90))
        exc = losses[losses > thr] - thr
        if exc.size >= 10:
            xi, _loc, beta = genpareto.fit(exc, floc=0.0)
            nu = float(losses.size); k = float(exc.size)

            def _evt_var(p):
                if abs(xi) > 1e-6:
                    return float(thr + (beta / xi) * (((nu / k) * (1.0 - p)) ** (-xi) - 1.0))
                return float(thr + beta * math.log((nu / k) / (1.0 - p)))

            v995 = _evt_var(0.995); v999 = _evt_var(0.999)
            cvar995 = float((v995 + beta - xi * thr) / (1.0 - xi)) if xi < 1 else None
            evt = {"enabled": True, "threshold": thr, "shape_xi": float(xi), "scale_beta": float(beta),
                   "n_exceed": int(k), "var_995": v995, "var_999": v999, "cvar_995": cvar995,
                   "tail_note": ("重尾 ξ>0(尾比正态厚)" if xi > 0 else ("薄尾 ξ<0" if xi < 0 else "指数尾 ξ≈0"))}
        else:
            evt["note"] = f"超阈值样本不足({int(exc.size)}<10),EVT 诚实跳过。"
    except Exception as e:  # noqa: BLE001
        evt["note"] = f"EVT 跳过(scipy 不可用或拟合失败:{type(e).__name__})。"
    out["evt"] = evt

    # Kupiec POF VaR 回测:以历史 95% VaR 为模型,看实际突破率是否显著偏离 5%(χ²₁ 95%=3.841)
    try:
        var95 = levels["95"]["hist_var"]; n = int(losses.size)
        x = int(np.sum(losses > var95)); p = 0.05; pi = x / n if n else 0.0
        lr = None
        if 0 < x < n:
            lr = -2.0 * (((n - x) * math.log(1 - p) + x * math.log(p))
                         - ((n - x) * math.log(1 - pi) + x * math.log(pi)))
        out["kupiec"] = {"var95": float(var95), "breaches": x, "n": n, "breach_rate": float(pi),
                         "expected_rate": p, "lr_stat": (float(lr) if lr is not None else None),
                         "reject_5pct": bool(lr is not None and lr > 3.841),
                         "note": "Kupiec POF:LR>3.841→拒绝'突破率=5%',VaR 校准存疑"}
    except Exception:  # noqa: BLE001
        pass

    # 损失分布直方图(供抽屉画分布 + VaR/CVaR 竖线)
    try:
        counts, edges = np.histogram(ret, bins=30)
        out["hist"] = {"counts": [int(c) for c in counts], "edges": [float(x) for x in edges]}
    except Exception:  # noqa: BLE001
        pass
    return out


def _backtest_vector(body: "BacktestVectorIn") -> JSONResponse:
    """TopN 向量化回测:fe_spec 物化因子截面值 → 逐调仓期 nlargest(topn) 等权 → 真净值 + 指标。

    流程(全部引擎 primitive,函数体内延迟 import):
      1. 归一特征(空 → ok:False)。窗口缺省同 P3/P4(今天−1y,守秒级)。
      2. ``_materialize_xy`` 复用 /feature/build 链 → panel + fe_df(已 winsorize/zscore 的
         多特征 X)。多 feature → 等权 z-score 合成一条截面因子(单 feature 直接取该列)。
      3. ``build_report``(因子视图):reindex 整面板 → 真 IC/分位/characteristics + benchmark_nav。
         IC/分位仍是诚实的**因子**质量视图(不被 TopN 账户污染)。评测退化只告警, 不阻断回测。
      4. ``_topn_portfolio``:在调仓日截面上逐期 nlargest(topn) 等权(可选多空)→ 真净值 +
         年化/Sharpe/MDD/Calmar/换手/胜率,**覆盖** portfolio 块(头条曲线 = 实际 TopN 账户)。
         基准净值沿用 build_report 的等权全池 benchmark_nav(无报告时独立 _benchmark_nav 兜底)。
      5. 组装 /factor/report 兼容顶层形(ic/quantile/portfolio/characteristics/meta)+ 回测专有
         (topn/cash/long_short/trades/portfolio_kpi)+ composite/_compose(前端终端送抽屉)。
    任何阶段失败 → ok:False + reason(HTTP 200),顶层带空 ic/quantile/portfolio/characteristics
    使前端 ResultsDrawer 安全降级(同 _fail_factor 形)。"""
    import numpy as np
    import pandas as pd
    from dataclasses import asdict as _asdict
    from financial_analyst.factors.eval.report import (
        forward_simple_returns, rebalance_dates, _restrict, _benchmark_nav, build_report,
    )
    from financial_analyst.factors.eval.config import EvalConfig

    universe = (body.universe or "csi_fast").strip() or "csi_fast"

    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_factor(universe, "回测因子不能为空 (上游需提供因子表达式或复合因子标签)",
                            method="backtest_vector")

    freq = (body.freq or "day").strip() or "day"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    # 调仓频率(独立于面板加载频率;默认月频 → 根治日频 ppy=252 的年化虚高)。
    rebalance = (body.rebalance or "month").strip() or "month"
    if rebalance not in ("day", "week", "month"):
        rebalance = "month"
    # 前向收益窗口按调仓频率取(月频≈21日),使每期收益≈持有期收益、修正日频重叠过采样。
    fwd_days = _FWD_BY_REBAL.get(rebalance, 21)
    topn = max(1, int(body.topn or 30))
    cash = float(body.cash) if body.cash is not None else 1_000_000.0
    long_short = bool(body.long_short)
    # A股三段真实成本 → 合成单边等效 bps(佣金 + 滑点 + 兼容老 cost_bps);印花税单列(卖出腿)。
    commission = max(0.0, float(body.commission or 0.0))
    stamp_tax = max(0.0, float(body.stamp_tax or 0.0))
    slippage = max(0.0, float(body.slippage_bps or 0.0))
    cost_bps = commission * 1e4 + slippage + max(0.0, float(body.cost_bps or 0.0))
    # 回测组合定权(默认等权;非等权 → 逐调仓日按所选法加权多头,经 _cross_weights 同 portfolio 口径)。
    bt_weighting = (getattr(body, "weighting", "equal") or "equal").strip().lower()
    if bt_weighting not in ("equal", "mktcap", "inv_vol", "risk_parity",
                            "min_var", "max_sharpe", "true_risk_parity", "black_litterman"):
        bt_weighting = "equal"
    bt_max_weight = max(0.0, float(getattr(body, "max_weight", 0.0) or 0.0))
    bt_industry_neutral = bool(getattr(body, "industry_neutral", False))
    bt_vol_forecast = (getattr(body, "vol_forecast", "hist") or "hist").strip().lower()
    if bt_vol_forecast not in ("hist", "ewma", "garch"):
        bt_vol_forecast = "hist"
    n_groups = max(2, int(body.n_groups or 10))
    trades_cap = max(0, int(body.trades_cap if body.trades_cap is not None else 24))

    # —— 1-2. 物化因子截面值(复用 /feature/build 链;多 feature 等权 z-score 合成)——
    #    面板始终按**日频**加载(qlib 仅有 day provider;week/month 是调仓重采样而非 loader 频率,
    #    同引擎 report.py:196 硬编 freq="day")。故喂 _materialize_xy 一个 freq="day" 的 body 副本
    #    (不改原 body、不动共享 _materialize_xy),用户 freq 仅驱动下方 rebalance_dates/EvalConfig。
    load_body = body.model_copy(update={"freq": "day"})
    mat = _materialize_xy(load_body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat  # 物化阶段诚实失败(_fail_model 形已带 ok:False/reason)
    panel, fe_df, _label_s, feature_names = mat

    combine_method = (getattr(body, "combine", "equal") or "equal").strip().lower()
    _labels = {feature_names[i]: features[i] for i in range(min(len(feature_names), len(features)))}
    alpha, combine_weights, combine_note = _combine_alpha(
        fe_df, feature_names, panel, fwd_days, method=combine_method,
        train_frac=getattr(body, "combine_train_frac", 0.6), member_labels=_labels)
    alpha = alpha.rename("alpha")
    if alpha.empty:
        return _fail_factor(universe, "回测因子物化后为空 (特征全 NaN)",
                            method="backtest_vector", start=start, end=end,
                            feature_names=feature_names)

    # —— 3. 因子视图报告(诚实 IC/分位/characteristics + 基准)——
    alpha_full = alpha.reindex(panel.df.index)
    cfg = EvalConfig(
        universe=universe, freq=rebalance, start=start, end=end, fwd_days=fwd_days,
        n_groups=n_groups,
        winsorize_q=(body.winsorize_q if body.winsorize else 0.0),
        standardize=bool(body.standardize),
    )
    report = None
    report_err = ""
    try:
        report = build_report(panel, lambda p: alpha_full, cfg, f"backtest[top{topn}]", "backtest")
    except Exception as exc:  # noqa: BLE001  —— 因子视图退化不阻断回测, 仅缺 ic/分位
        report_err = f"{type(exc).__name__}: {exc}"
    if report is not None and getattr(report, "status", "") != "ok":
        report_err = report_err or f"report_{report.status}: {getattr(report, 'error', '') or '评测退化'}"
        report = None

    # —— 4. TopN 组合(真账户)。在调仓日截面上逐期 nlargest(topn) 等权 ——
    reb = rebalance_dates(list(panel.dates()), rebalance)
    fwd = forward_simple_returns(panel, fwd_days)
    alpha_r = _restrict(alpha, reb)
    fwd_r = _restrict(fwd, reb)
    ppy = cfg.periods_per_year()
    pf, trades, gross_ann, total_cost = _topn_portfolio(
        alpha_r, fwd_r, topn, ppy, long_short=long_short,
        cost_bps=float(body.cost_bps or 0.0), stamp_tax=stamp_tax,
        commission=commission, slippage_bps=slippage,
        weighting=bt_weighting, panel_df=panel.df,
        max_weight=bt_max_weight, industry_neutral=bt_industry_neutral,
        vol_forecast=bt_vol_forecast)
    if not pf.nav_series:
        return _fail_factor(universe,
                            "TopN 组合为空 (调仓日截面无可选标的或前向收益全 NaN)",
                            method="backtest_vector", start=start, end=end,
                            feature_names=feature_names, topn=topn)
    # 基准:优先 build_report 的等权全池基准;无报告 → 独立 _benchmark_nav 兜底。
    pf.benchmark_nav = (report.portfolio.benchmark_nav
                        if (report is not None and report.portfolio is not None
                            and report.portfolio.benchmark_nav)
                        else _benchmark_nav(fwd_r))

    # —— 5. 组装 /factor/report 兼容顶层 + 回测专有 ——
    pf_dict = _jsonable(_asdict(pf))
    pf_dict["gross_ann"] = _num(gross_ann)   # 扣成本前年化
    pf_dict["net_ann"] = _num(pf.ann_return)  # 扣成本后年化(= ann_return)
    pf_dict["total_cost"] = _num(total_cost)  # 累计成本拖累(各期成本之和)
    # W6 绩效补全:Sortino / 信息比率 / 真沪深300 基准净值(从净值序列 + B2 沪深300 真算,不动引擎)。
    _w6 = _w6_perf(pf.nav_series, pf.ann_return, ppy, start, end)
    pf_dict["sortino"] = _num(_w6["sortino"])
    pf_dict["information_ratio"] = _num(_w6["information_ratio"])
    pf_dict["bench300_nav"] = _w6["bench300_nav"]   # [date, nav] 对,归一首日=1;空=取不到真指数
    pf_dict["risk"] = _risk_block(pf.nav_series, ppy)  # VaR/CVaR/EVT/Kupiec(白捡进回测抽屉;短序列诚实空)
    # W7 样本外体检:回测净值切尾段 → IS/OOS Sharpe·年化并排 + 衰减判定(不动引擎)。
    bt_oos = _oos_perf_block(pf.nav_series, ppy, getattr(body, "oos_frac", 0.0))
    bt_wf = _wf_perf(pf.nav_series, ppy)          # W7-2 walk-forward:逐段 Sharpe + 汇总
    bt_rsharpe = _rolling_sharpe(pf.nav_series, ppy)  # W7-3 滚动夏普曲线(子区间稳定性)
    rep_dict = _jsonable(_asdict(report)) if report is not None else None
    if rep_dict is not None:   # #4 逐期 rank-IC 序列进 ic 块(口径修真;按调仓频率)
        _inject_rank_ic_series(rep_dict.get("ic"), alpha, fwd, panel, rebalance)

    n_periods = len(pf.nav_series)
    nav_end = pf.nav_series[-1][1] if pf.nav_series else None
    warnings: List[str] = list(report.warnings) if (report is not None and report.warnings) else []
    if report_err:
        warnings.append(f"因子视图报告退化(IC/分位缺失): {report_err}")
    if n_periods < 12:
        warnings.append(f"回测有效调仓期太短 ({n_periods} < 12), 净值/指标不稳健。")
    if pf.ann_return is not None and pf.ann_return == pf.ann_return and pf.ann_return < 0:
        warnings.append("TopN 组合年化为负 — 因子方向可能反向(试因子取负或换多空)。")
    if bt_oos.get("verdict") in ("overfit", "degraded"):
        warnings.append(f"样本外体检:{bt_oos.get('note', '')}(样本内 Sharpe "
                        f"{_num((bt_oos.get('is') or {}).get('sharpe'))} → 样本外 "
                        f"{_num((bt_oos.get('oos') or {}).get('sharpe'))})。")

    portfolio_kpi = {
        "ann_return": _num(pf.ann_return),
        "sharpe": _num(pf.sharpe),
        "sortino": _num(_w6["sortino"]),                       # W6 下行风险调整收益
        "max_drawdown": _num(pf.max_drawdown),
        "calmar": _num(pf.calmar),
        "volatility": _num(pf.volatility),
        "turnover": _num(pf.turnover),
        "win_rate": _num(pf.win_rate),
        "information_ratio": _num(_w6["information_ratio"]),   # W6 对标真沪深300 的信息比率
        "gross_ann": _num(gross_ann),
        "net_ann": _num(pf.ann_return),
        "total_cost": _num(total_cost),
        "nav_end": _num(nav_end),
        "n_periods": n_periods,
    }

    fe_spec = {
        "features": features,
        "feature_names": feature_names,
        "label": None if (body.label or "").strip().lower() in _LABEL_FWD_TOKENS else (body.label or "").strip(),
        "fwd_days": fwd_days,
        "universe": universe,
        "start": start,
        "end": end,
        "freq": freq,
        "winsorize": bool(body.winsorize),
        "winsorize_q": float(body.winsorize_q),
        "standardize": bool(body.standardize),
    }

    label = f"TopN[{topn}]" + ("(多空)" if long_short else "") + f"({'+'.join(features)})"
    out: Dict[str, Any] = {
        "ok": True,
        "universe": universe,
        "method": "backtest_vector",
        "n_dates": (int(report.meta.n_dates) if report is not None else n_periods),
        "n_periods": n_periods,
        "feature_names": feature_names,
        # —— 多因子合成口径(>1 feature 时;ic/icir 样本内估权)——
        "combine": combine_method,
        "combine_weights": combine_weights,
        "combine_note": combine_note,
        # —— /factor/report 同形顶层块(前端 ResultsDrawer 直接消费)——
        #    portfolio 用真 TopN 账户覆盖;ic/quantile/characteristics 仍是诚实因子视图(可空)。
        "meta": (rep_dict.get("meta") if rep_dict else None),
        "ic": (rep_dict.get("ic") if rep_dict else {}) or {},
        "quantile": (rep_dict.get("quantile") if rep_dict else {}) or {},
        "portfolio": pf_dict,
        "characteristics": (rep_dict.get("characteristics") if rep_dict else {}) or {},
        "oos": bt_oos,   # W7 样本外体检块(enabled/is/oos/decay_ratio/verdict/note)
        "walkforward": bt_wf,        # W7-2 walk-forward 逐段 Sharpe + 汇总
        "rolling_sharpe": bt_rsharpe,  # W7-3 滚动夏普曲线 [[date,sharpe],...]
        "warnings": warnings,
        "status": "ok",
        # —— 回测专有 ——
        "backtest": {
            "topn": topn,
            "cash": cash,
            "long_short": long_short,
            "cost_bps": cost_bps,
            "commission": commission,
            "stamp_tax": stamp_tax,
            "slippage_bps": slippage,
            "rebalance": rebalance,
            "rebalance_freq": rebalance,
            "weighting": bt_weighting,                 # W5-1→回测:持仓定权法(非等权=真按目标权重序列回测)
            "max_weight": bt_max_weight,
            "industry_neutral": bt_industry_neutral,
            "gross_ann": _num(gross_ann),
            "net_ann": _num(pf.ann_return),
            "total_cost": _num(total_cost),
            "fwd_days": fwd_days,
            "portfolio_kpi": portfolio_kpi,
        },
        "trades": (trades[-trades_cap:] if (body.with_trades and trades_cap > 0) else []),
        # —— composite 标记(回测无 expr,前端 analysis/终端靠此透传/送抽屉)——
        "composite": True,
        "fe": fe_spec,
    }
    if rep_dict is not None:
        out["report"] = rep_dict
    return JSONResponse(out)


def _portfolio_risk_attr(df, asof, codes, weights, lookback: int = 250, min_obs: int = 60):
    """组合层风险归因(欧拉分解)。建选中票截至 ``asof`` 的收益矩阵 → Ledoit-Wolf Σ →
    ``portfolio_opt.risk_contributions(Σ, w)``,逐持仓拆「贡献了组合多少风险」。
    返回 ``{port_vol, covered, total, by_code:{code:{pct,comp_var95,comp_var99,mcr}}, note}``
    或 ``None``(收益矩阵列/样本不足、Σ 病态、全零权重 → 诚实降级,不抛)。
    缺数据票不参与分解(权重在覆盖票内重归一,note 标 covered/total);**纯描述性当前持仓风险结构
    快照,非回测、非业绩**;成分 VaR 为正态近似。Σ 用 ≤asof 收益,无前视。"""
    try:
        import pandas as pd   # api.py 的 pd/np 是函数内局部 import(非模块级),本助手须自带
        dts = df.index.get_level_values("datetime")
        sub = df.loc[dts <= asof]
        if "returns" in df.columns:
            ser = sub["returns"]
        elif "close" in df.columns:
            ser = sub["close"].groupby(level="code").pct_change(fill_method=None)
        else:
            return None
        R = ser.unstack("code")
        keep = [c for c in codes if c in R.columns]
        if len(keep) < 2:
            return None
        R = R.reindex(columns=keep).tail(lookback)
        R = R.dropna(axis=1, thresh=max(min_obs, int(0.6 * len(R))))
        R = R.dropna(axis=0, how="any")
        if R.shape[0] < min_obs or R.shape[1] < 2:
            return None
        used = list(R.columns)
        w = pd.Series(weights).reindex(used).fillna(0.0).to_numpy(dtype=float)
        sw = float(w.sum())
        if not (sw > 0):
            return None
        w = w / sw                                   # 覆盖票内重归一(缺数据票不参与,诚实)
        from guanlan_v2.workflow.portfolio_opt import shrink_cov, risk_contributions
        Sigma, cov_note = shrink_cov(R.to_numpy())
        rc = risk_contributions(Sigma, w)
        if rc.get("pct") is None:
            return None
        by_code = {str(c): {"pct": _num(rc["pct"][i]), "comp_var95": _num(rc["comp_var95"][i]),
                            "comp_var99": _num(rc["comp_var99"][i]), "mcr": _num(rc["mcr"][i])}
                   for i, c in enumerate(used)}
        return {"port_vol": _num(rc["port_vol"]), "covered": len(used), "total": len(codes),
                "by_code": by_code,
                "note": cov_note + "·成分VaR正态近似·当前持仓风险结构快照(非回测/非业绩)"}
    except Exception:  # noqa: BLE001 — 风险归因失败不拖垮组合构建,诚实 None
        return None


def _portfolio_build(body: "PortfolioBuildIn") -> JSONResponse:
    """组合构建:fe_spec 物化因子截面 → **最新截面** nlargest(topn) → 按 weighting 定权
    (+ 可选行业中性 / 单票上限)→ 最新一期目标持仓权重(下单/展示用)。

    流程(全部引擎 primitive,函数体内延迟 import):
      1. 归一特征(空 → ok:False)。窗口缺省同 P3/P4(今天−1y)。
      2. ``_materialize_xy`` 复用 /feature/build 链 → panel + fe_df(已 winsorize/zscore 的
         多特征 X)。多 feature → 等权 z-score 合成一条截面因子(单 feature 直接取该列)。
      3. 取**最新调仓截面**的因子值 → nlargest(topn) 为目标持仓。
      4. 按 weighting 定权:equal / mktcap(∝total_mv) / inv_vol(∝1/σ) / risk_parity
         (本期近似=反波动)。缺列则该法降级等权 + 告警。
      5. 可选行业中性(行业内按基础权重、各行业等权)+ 单票上限(水填充截断后重归一)。
      6. 组装最新一期目标权重 holdings(权重降序)+ fe spec 透传(供回测.pf 口复算)。
    数据只经 _materialize_xy 内 get_default_loader(不碰 engine/)。诚实失败 ok:False +
    reason(空因子 / 物化失败 / 截面为空,HTTP 200)。**不回测**该权重(honest:回测节点
    仍按 TopN 等权;此处仅出最新期目标权重)。"""
    import numpy as np
    import pandas as pd

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    features = _normalize_features(body)  # type: ignore[arg-type]
    if not features:
        return _fail_factor(universe, "组合因子不能为空 (上游需提供因子表达式或复合因子标签)",
                            method="portfolio_build")

    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    topn = max(1, int(body.topn or 30))
    weighting = (body.weighting or "equal").strip() or "equal"
    if weighting not in ("equal", "mktcap", "inv_vol", "risk_parity",
                         "min_var", "max_sharpe", "true_risk_parity", "black_litterman"):  # #3 修半套:独立组合节点也放行优化器/BL(原只 4 基础静默降级)
        weighting = "equal"
    max_weight = max(0.0, float(body.max_weight or 0.0))
    industry_neutral = bool(body.industry_neutral)

    # —— 1-2. 物化因子截面(复用 /feature/build 链;面板按日频加载,同 backtest)——
    load_body = body.model_copy(update={"freq": "day"})
    mat = _materialize_xy(load_body, universe, features, start, end)
    if isinstance(mat, JSONResponse):
        return mat  # 物化阶段诚实失败(_fail_model 形已带 ok:False/reason)
    panel, fe_df, _label_s, feature_names = mat

    combine_method = (getattr(body, "combine", "equal") or "equal").strip().lower()
    _labels = {feature_names[i]: features[i] for i in range(min(len(feature_names), len(features)))}
    alpha, combine_weights, combine_note = _combine_alpha(
        fe_df, feature_names, panel, 21, method=combine_method,
        train_frac=getattr(body, "combine_train_frac", 0.6), member_labels=_labels)
    alpha = alpha.rename("alpha")
    if alpha.empty:
        return _fail_factor(universe, "组合因子物化后为空 (特征全 NaN)",
                            method="portfolio_build", start=start, end=end,
                            feature_names=feature_names)

    # —— 3. 最新截面 → nlargest(topn) ——
    try:
        dts = alpha.index.get_level_values("datetime")
        asof = max(dts.unique())
        cross = alpha.xs(asof, level="datetime").dropna()
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"cross_section_error: {type(exc).__name__}: {exc}",
                            method="portfolio_build", start=start, end=end,
                            feature_names=feature_names)
    if cross.empty:
        return _fail_factor(universe, "最新截面无可选标的", method="portfolio_build",
                            start=start, end=end, feature_names=feature_names)
    n = max(1, min(topn, len(cross)))
    picks = cross.nlargest(n)
    codes = list(picks.index)

    df = panel.df
    warnings: List[str] = []

    def _latest_col(col: str):
        if col not in df.columns:
            return None
        try:
            return df[col].xs(asof, level="datetime").reindex(codes)
        except Exception:  # noqa: BLE001
            return None

    # —— 4-5. 定权(equal/mktcap/inv_vol/risk_parity)+ 行业中性 + 单票上限 ——
    #    抽出到 _cross_weights(与回测 _topn_portfolio 逐期共用同一口径);最新截面快照,
    #    波动用全历史(≤asof)close 日收益标准差(vol_asof=None → 内部自算)。
    pb_vol_forecast = (getattr(body, "vol_forecast", "hist") or "hist").strip().lower()
    if pb_vol_forecast not in ("hist", "ewma", "garch"):
        pb_vol_forecast = "hist"
    weights, used_weighting, did_ind, _wn = _cross_weights(
        codes, df, asof, weighting, max_weight=max_weight, industry_neutral=industry_neutral,
        scores=picks, vol_forecast=pb_vol_forecast,
        garch_cache=({} if pb_vol_forecast == "garch" else None))
    warnings.extend(_wn)

    # —— 6. 组装 holdings(权重降序)+ 组合层风险归因(成分风险占比/成分VaR)+ fe spec 透传 ——
    mv_all = _latest_col("total_mv")
    risk_attr = _portfolio_risk_attr(df, asof, codes, weights)   # #4 风险归因(Σ over ≤asof 收益;失败诚实 None)
    rby = (risk_attr or {}).get("by_code") or {}
    holdings: List[Dict[str, Any]] = []
    for code in weights.sort_values(ascending=False).index:
        mv_v = None
        if mv_all is not None and code in mv_all.index:
            try:
                fv = float(mv_all[code])
                mv_v = _num(fv) if fv == fv else None
            except Exception:  # noqa: BLE001
                mv_v = None
        rc_c = rby.get(str(code)) or {}
        holdings.append({
            "code": str(code),
            "weight": _num(float(weights[code])),
            "alpha": _num(float(picks.get(code, float("nan")))),
            "total_mv": mv_v,
            "risk_pct": rc_c.get("pct"),            # 成分风险占比(缺数据票 None 诚实降级)
            "comp_var95": rc_c.get("comp_var95"),   # 成分 VaR95(正态近似)
        })

    fe_spec = {
        "features": features,
        "feature_names": feature_names,
        "label": None if (body.label or "").strip().lower() in _LABEL_FWD_TOKENS else (body.label or "").strip(),
        "fwd_days": max(1, int(body.fwd_days or 5)),
        "universe": universe,
        "start": start,
        "end": end,
        "freq": (body.freq or "day").strip() or "day",
        "winsorize": bool(body.winsorize),
        "winsorize_q": float(body.winsorize_q),
        "standardize": bool(body.standardize),
    }

    out: Dict[str, Any] = {
        "ok": True,
        "universe": universe,
        "method": "portfolio_build",
        "asof": str(pd.Timestamp(asof).date()),
        "weighting": used_weighting,
        "weighting_req": weighting,
        "industry_neutral": did_ind,
        "max_weight": max_weight,
        "n_holdings": len(holdings),
        "n_candidates": int(len(cross)),
        "sum_weight": _num(float(weights.sum())),
        "feature_names": feature_names,
        # —— 多因子合成口径(>1 feature 时;ic/icir 样本内估权)——
        "combine": combine_method,
        "combine_weights": combine_weights,
        "combine_note": combine_note,
        "holdings": holdings,
        "risk_attr": risk_attr,        # #4 组合层风险归因(欧拉分解;Σ over ≤asof;失败 None)
        "warnings": warnings,
        "status": "ok",
        # —— composite 标记 + fe 透传(前端终端送抽屉看目标持仓;回测.pf 口靠 fe 复算)——
        "composite": True,
        "fe": fe_spec,
    }
    return JSONResponse(out)


class FactorReport2In(BaseModel):
    """``POST /factor/report2`` 入参:**可配**单因子报告(壳内复用 ``build_report``)——补
    引擎 ``/factor/report``(import-only 不可改)缺的 ``n_groups``/``fwd_days``/``direction``
    入参,使前端 ``analysis``(分组/调仓/方向)与 ``iccalc``(精确周期)三个旋钮真生效。"""

    expr_or_name: str = ""           # zoo 表达式 或 注册因子名
    universe: str = "csi_fast"
    codes: Optional[List[str]] = None    # #5 自选股池:给定则直接用这批代码(优先于 universe)
    benchmark: Optional[str] = None      # B2 对标宽基指数 id(csi300=真实沪深300)→ 注入 idx_ret 字段(共振)
    leader: Optional[str] = None         # B2 龙头股代码 → 注入 ref_ret 字段(共振)
    oos_frac: float = 0.0                # W7 样本外占比(尾段;0=不切;>0 如 0.3=末30%作 OOS 做过拟合体检)
    start: Optional[str] = None
    end: Optional[str] = None
    freq: str = "month"              # 调仓/评测频率 day/week/month
    n_groups: int = 10               # 分层分组数(quantile 视图)
    fwd_days: int = 0                # 0 → 按 freq 自动(day1/week5/month21);>0 → 精确前向天数(iccalc 周期)
    direction: int = 0               # 0 原始 / -1 取反(因子 ×-1)
    winsorize_q: float = 0.01
    standardize: bool = True
    neutralize: bool = False         # #1 截面行业+市值中性化:对原始因子逐日 OLS 残差化(残差替代原值)


class FactorComposeIn(ModelTrainIn):
    """``POST /factor/compose`` 入参:把多个因子(zoo 表达式/注册名)按 ``method`` 合成一条
    composite 截面因子并出完整报告(ic/分层/多空 + 各腿权重)。继承 ``ModelTrainIn`` 的
    universe/start/end/winsorize/standardize/benchmark/leader/oos_frac/combine_train_frac。

    method:
      equal  等权(默认)
      ic     样本内 rank-IC 加权(保号)
      icir   样本内 rank-ICIR 加权(惩罚不稳定因子)
    ic/icir 权重只用样本内(前 combine_train_frac 调仓日)估出 → 不引入未来函数。"""

    members: List[str] = []          # 多个因子表达式 / 注册名(≥1;<2 退化为单因子)
    method: str = "equal"            # equal | ic | icir
    n_groups: int = 10               # 分层分组数(quantile 视图)
    direction: int = 0               # 0 原始 / -1 取反(对合成因子整体)
    freq: str = "month"              # 调仓/评测频率 day/week/month


def _cscv_pbo(perf_matrix, n_blocks: int = 8):
    """CSCV → PBO(回测过拟合概率,López de Prado / 华泰对抗"挑出来的虚高回测")。

    ``perf_matrix``:候选 × 时间块 的表现矩阵(此处用各成员因子在各时间块的 rank-IC),
    形状 (N候选, S块)。把 S 块枚举所有 C(S, S/2) 互补划分(一半 IS 一半 OOS):每划分用
    IS 选表现最优候选 → 看它在 OOS 的相对排名 ω∈(0,1) → ω<0.5(落入 OOS 后半)记一次过拟合。
    PBO = 这类划分占比。需 N≥2 候选 & S≥4 偶数块。返回 {enabled, pbo, n_combos, ...}。"""
    import numpy as np
    from itertools import combinations
    M = np.asarray(perf_matrix, dtype="float64")
    if M.ndim != 2 or M.shape[0] < 2 or M.shape[1] < 4:
        return {"enabled": False, "reason": f"PBO 需 ≥2 候选 & ≥4 时间块(得 {tuple(M.shape) if M.ndim == 2 else M.shape})"}
    N, S = M.shape
    if S % 2 == 1:   # 取偶数块
        S -= 1
        M = M[:, :S]
    M = np.nan_to_num(M, nan=0.0)
    half = S // 2
    n_below = 0
    n_tot = 0
    for is_idx in combinations(range(S), half):
        oos_idx = [b for b in range(S) if b not in is_idx]
        is_perf = M[:, list(is_idx)].mean(axis=1)
        oos_perf = M[:, oos_idx].mean(axis=1)
        best = int(np.argmax(is_perf))                       # IS 最优候选
        order = oos_perf.argsort()                            # OOS 升序
        rank = int(np.where(order == best)[0][0]) + 1         # 1=最差 .. N=最优
        omega = rank / (N + 1.0)
        if omega < 0.5:
            n_below += 1
        n_tot += 1
    pbo = (n_below / n_tot) if n_tot else float("nan")
    return {"enabled": True, "pbo": _num(pbo), "n_combos": int(n_tot),
            "n_candidates": int(N), "n_blocks": int(S),
            "note": "PBO = IS 最优候选在 OOS 落入后半的概率(CSCV);>0.5 提示选优过拟合,<0.3 较稳健。"}


def _factor_compose(body: "FactorComposeIn") -> JSONResponse:
    """多因子合成(equal/ic/icir 加权)→ 完整 OOS 报告 + 各腿权重。复用 ``_materialize_xy``
    物化各 member(已 winsorize/zscore)→ ``_combine_alpha`` 合成 → ``build_report``。
    数据只经 ``get_default_loader``(不碰 engine/)。诚实失败 ok:False + reason(HTTP 200)。"""
    import pandas as pd  # noqa: F401
    from dataclasses import asdict as _asdict
    from financial_analyst.factors.eval.report import build_report, forward_simple_returns
    from financial_analyst.factors.eval.config import EvalConfig
    from financial_analyst.factors.eval.ic import ic_analysis

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    members = [str(m).strip() for m in (body.members or []) if str(m).strip()]
    if not members:
        return _fail_factor(universe, "多因子合成: members 为空 (需≥1 个因子表达式/名称)", method="compose")

    freq = (body.freq or "month").strip() or "month"
    if freq not in ("day", "week", "month"):
        freq = "month"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)).isoformat()
    n_groups = max(2, int(getattr(body, "n_groups", 10) or 10))
    fwd_days = _FWD_BY_REBAL.get(freq, 21)
    method = (body.method or "equal").strip().lower()
    if method not in ("equal", "ic", "icir"):
        method = "equal"
    direction = int(getattr(body, "direction", 0) or 0)

    # 物化各 member 为 winsorize/zscore 后的列(复用 _materialize_xy;面板按日频加载)。
    load_body = body.model_copy(update={"freq": "day", "features": members})
    mat = _materialize_xy(load_body, universe, members, start, end)
    if isinstance(mat, JSONResponse):
        return mat
    panel, fe_df, _label_s, feature_names = mat

    _labels = {feature_names[i]: members[i] for i in range(min(len(feature_names), len(members)))}
    alpha, weights, note = _combine_alpha(
        fe_df, feature_names, panel, fwd_days, method=method,
        train_frac=getattr(body, "combine_train_frac", 0.6), member_labels=_labels)
    if alpha is None or alpha.empty:
        return _fail_factor(universe, "多因子合成: 合成因子为空 (特征全 NaN)", method="compose",
                            start=start, end=end, feature_names=feature_names)
    if direction < 0:
        alpha = -alpha

    alpha_full = alpha.reindex(panel.df.index)
    cfg = EvalConfig(universe=universe, freq=freq, start=start, end=end, fwd_days=fwd_days,
                     n_groups=n_groups, winsorize_q=float(body.winsorize_q or 0.0),
                     standardize=bool(body.standardize))
    label = (" + ".join(members))[:120]
    try:
        report = build_report(panel, lambda p: alpha_full, cfg, label, "compose")
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"build_report_error: {type(exc).__name__}: {exc}",
                            method="compose", start=start, end=end)
    if getattr(report, "status", "") != "ok":
        return _fail_factor(universe, f"report_{report.status}: {getattr(report, 'error', '') or '评测退化'}",
                            method="compose", start=start, end=end)

    rep_dict = _jsonable(_asdict(report))
    fac = alpha.dropna()
    fwd = forward_simple_returns(panel, fwd_days)
    ic_head = ic_analysis(fac, fwd.reindex(fac.index))
    _inject_rank_ic_series(rep_dict.get("ic"), fac, fwd, panel, freq)   # #4 逐期 rank-IC 序列进 ic 块(口径修真)
    n_dates = int(report.meta.n_dates)
    warnings: List[str] = list(report.warnings or [])
    if note:
        warnings.append(note)
    if n_dates < 12:
        warnings.append(f"有效调仓期太短 ({n_dates} < 12), 结论不稳健。")
    oos_block = _oos_ic_block(fac, fwd, getattr(body, "oos_frac", 0.0))
    wf_block = _wf_factor(fac, fwd)

    # CSCV/PBO:对成员候选(≥2)做组合对称交叉验证 → 选优过拟合概率(挑最优成员是否过拟合)。
    pbo_block: Dict[str, Any] = {"enabled": False}
    if len(feature_names) >= 2:
        try:
            S = 8
            _dts = pd.DatetimeIndex(sorted(set(fe_df.index.get_level_values("datetime"))))
            if len(_dts) >= 16:   # 每块至少 ~2 日
                bnds = [int(round(len(_dts) * k / S)) for k in range(S + 1)]
                _dtl = fe_df.index.get_level_values("datetime")
                mat: List[List[float]] = []
                for nm in feature_names:
                    col = fe_df[nm]
                    row: List[float] = []
                    for b in range(S):
                        blk = set(_dts[bnds[b]:bnds[b + 1]])
                        cb = col[_dtl.isin(blk)].dropna()
                        v = float(ic_analysis(cb, fwd.reindex(cb.index)).rank_ic_mean) if len(cb) else float("nan")
                        row.append(v)
                    mat.append(row)
                pbo_block = _cscv_pbo(mat, n_blocks=S)
                if isinstance(pbo_block, dict) and pbo_block.get("enabled"):
                    pbo_block["candidates"] = list(members)
            else:
                pbo_block = {"enabled": False, "reason": f"调仓/交易日太少做不了 CSCV(n={len(_dts)} < 16)"}
        except Exception as exc:  # noqa: BLE001 — PBO 失败不阻断合成报告
            pbo_block = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}"}

    composite = {
        "ok": True, "status": "ok", "method": method, "_compose": True,
        "members": members, "weights": weights, "combine": method, "combine_weights": weights,
        "combine_note": note,
        "n_dates": n_dates, "n_codes": int(report.meta.n_codes),
        "headline_ic": {
            "rank_ic": _num(ic_head.rank_ic_mean), "rank_icir": _num(ic_head.rank_icir),
            "ic": _num(ic_head.ic_mean), "icir": _num(ic_head.icir),
        },
        "meta": rep_dict.get("meta"), "ic": rep_dict.get("ic") or {},
        "quantile": rep_dict.get("quantile") or {}, "portfolio": rep_dict.get("portfolio") or {},
        "characteristics": rep_dict.get("characteristics") or {},
        "oos": oos_block, "walkforward": wf_block, "pbo": pbo_block,
        "warnings": warnings, "report": rep_dict,
    }
    return JSONResponse({"ok": True, "universe": universe, "method": "compose",
                         "members": members, "combine": method, "weights": weights,
                         "n_dates": n_dates, "pbo": pbo_block, "composite": composite})


def _factor_report2(body: "FactorReport2In") -> JSONResponse:
    """可配单因子报告:注册名/zoo 表达式 → 真 OOS IC/分层/多空/特征,freq/分组/前向窗口/方向
    全可配。复用 ``build_report``(同 /factor/report 链),补引擎端点缺的入参。数据只经
    ``get_default_loader``(不碰 engine/)。诚实失败 ok:False + reason(HTTP 200)。"""
    import pandas as pd
    from dataclasses import asdict as _asdict
    from financial_analyst.factors.eval.report import build_report, forward_simple_returns
    from financial_analyst.factors.eval.config import EvalConfig
    from financial_analyst.factors.eval.ic import ic_analysis

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    expr = (body.expr_or_name or "").strip()
    if not expr:
        return _fail_factor(universe, "因子表达式/名称不能为空", method="report2")

    freq = (body.freq or "month").strip() or "month"
    if freq not in ("day", "week", "month"):
        freq = "month"
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)
    ).isoformat()
    n_groups = max(2, int(body.n_groups or 10))
    fwd_days = int(body.fwd_days or 0) or _FWD_BY_REBAL.get(freq, 21)   # 0 → 按 freq;>0 → 精确周期
    direction = int(body.direction or 0)

    # —— universe → panel(同 _materialize_xy 链,面板按日频加载)。自选股池 codes 优先(#5)——
    custom = _norm_codes(getattr(body, "codes", None))
    if custom:
        codes = custom
    else:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail_factor(universe, f"universe_error: {type(exc).__name__}: {exc}", method="report2")
    if not codes:
        return _fail_factor(universe, f"empty_universe: '{universe}' 解析为空", method="report2")
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:  # noqa: BLE001
            ind_loader = None
        panel = load_panel_cached(loader, codes, start, end, freq="day", industry_loader=ind_loader)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"load_error: {type(exc).__name__}: {exc}", method="report2", start=start, end=end)

    # B2:注入对标指数/龙头日收益(idx_ret/ref_ret)→ 公式可做个股 vs 大盘/龙头共振(给定才动);warning 并入报告。
    panel, _ref_warn = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                           getattr(body, "leader", None), start, end, "day")

    # —— 编译因子(注册名优先,否则 zoo 表达式)+ 方向 ——
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from financial_analyst.factors.zoo.registry import get as _get_alpha
        try:
            factor_s = _get_alpha(expr).compute(panel)
        except KeyError:
            validate_expr(expr)
            factor_s = compile_factor(expr)(panel)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"{type(exc).__name__}: {exc}", method="report2", start=start, end=end)
    if not isinstance(factor_s, pd.Series):
        return _fail_factor(universe, f"因子求值返回 {type(factor_s).__name__}, 期望 pd.Series",
                            method="report2", start=start, end=end)
    if direction < 0:
        factor_s = -factor_s

    # —— #1 因子中性化(行业 + 市值):请求时对原始因子做逐日截面 OLS 残差化,残差替代原值。
    #    残差既流入 build_report(报告 ic/分层/多空)又喂外层 headline/ic_decay → 两处口径天然一致。
    #    诚实降级:无市值且行业全未知 → 跳过(沿用原值);只有一侧 → 单控制项中性化;均在 warnings/响应显形。
    neutralize_info: Dict[str, Any] = {
        "requested": bool(getattr(body, "neutralize", False)),
        "applied": False, "by": None, "kept": None, "raw": None, "note": None,
    }
    if neutralize_info["requested"]:
        try:
            from guanlan_v2.workflow.factor_neutralize import neutralize_factor
            _mv = panel.total_mv
            _has_size = bool(pd.to_numeric(_mv, errors="coerce").gt(0).sum())
            _ind = panel.industry
            _ind_s = pd.Series(_ind)
            _n_ind = int(_ind_s[_ind_s != "未知"].nunique(dropna=True))   # 真行业数(排除「未知」哨兵;未分类股不算一族)
            if not _has_size and _n_ind <= 1:
                neutralize_info["note"] = "中性化:面板无可用市值且行业全未知 → 已跳过(沿用原始因子)。"
            else:
                _raw_n = int(factor_s.notna().sum())
                factor_s = neutralize_factor(
                    factor_s,
                    industry=(_ind if _n_ind > 1 else None),
                    mktcap=(_mv if _has_size else None),
                )
                _by = "+".join((["行业"] if _n_ind > 1 else []) + (["市值"] if _has_size else []))
                _kept = int(factor_s.notna().sum())
                neutralize_info.update({
                    "applied": True, "by": _by, "kept": _kept, "raw": _raw_n,
                    "note": f"已对原始因子做截面{_by}中性化(残差替代原值;有效点 {_kept}/{_raw_n})。",
                })
        except Exception as _e:  # noqa: BLE001 — 中性化失败诚实降级为原值,不拖垮报告
            neutralize_info["note"] = f"中性化失败已跳过:{type(_e).__name__}: {_e}"

    factor_full = factor_s.reindex(panel.df.index)
    cfg = EvalConfig(
        universe=universe, freq=freq, start=start, end=end, fwd_days=fwd_days, n_groups=n_groups,
        winsorize_q=float(body.winsorize_q or 0.0), standardize=bool(body.standardize),
    )
    try:
        report = build_report(panel, lambda p: factor_full, cfg, expr, "report2")
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"build_report_error: {type(exc).__name__}: {exc}",
                            method="report2", start=start, end=end)
    if getattr(report, "status", "") != "ok":
        return _fail_factor(universe, f"report_{report.status}: {getattr(report, 'error', '') or '评测退化'}",
                            method="report2", start=start, end=end)

    rep_dict = _jsonable(_asdict(report))
    fac = factor_s.dropna()
    fwd = forward_simple_returns(panel, fwd_days)
    ic_head = ic_analysis(fac, fwd.reindex(fac.index))
    _inject_rank_ic_series(rep_dict.get("ic"), fac, fwd, panel, freq)   # #4 逐期 rank-IC 序列进 ic 块(口径修真)
    # #2 截面 IC 衰减谱:同一因子对 1/3/5/10/20 日前向收益的 rank-IC 谱(判最优持有期/信息半衰期)。
    #    引擎 forward_simple_returns 各 horizon 真算、同 fac 同截面;PIT 前向收益无前视;单 horizon 失败不拖垮主报告。
    ic_decay = []
    for _h in _IC_DECAY_HORIZONS:
        try:
            _rh = ic_analysis(fac, forward_simple_returns(panel, int(_h)).reindex(fac.index))
            ic_decay.append({"h": int(_h), "rank_ic": _num(_rh.rank_ic_mean),
                             "rank_icir": _num(_rh.rank_icir), "ic": _num(_rh.ic_mean)})
        except Exception:  # noqa: BLE001 — 单 horizon 失败 → 该点 None,不拖垮主报告
            ic_decay.append({"h": int(_h), "rank_ic": None, "rank_icir": None, "ic": None})
    _icd_valid = [d for d in ic_decay if d.get("rank_ic") is not None]
    ic_decay_peak = (lambda pk: {"h": pk["h"], "rank_ic": pk["rank_ic"]})(
        max(_icd_valid, key=lambda d: abs(d["rank_ic"]))) if _icd_valid else None
    n_dates = int(report.meta.n_dates)
    warnings: List[str] = list(report.warnings or [])
    warnings.extend(_ref_warn or [])   # B2:对标指数/龙头取数告警(如某宽基无真实指数→引导用 csmean 代理)
    if neutralize_info.get("note"):    # #1 中性化结果/降级显形(应用成功或诚实跳过都进 warnings)
        warnings.append(neutralize_info["note"])
    if n_dates < 12:
        warnings.append(f"OOS 有效调仓期太短 ({n_dates} < 12), 结论不稳健。")
    # W7 样本外体检:IS/OOS RankIC 并排 + 衰减判定(尾段法;oos_frac>0 才切)。
    oos_block = _oos_ic_block(fac, fwd, getattr(body, "oos_frac", 0.0))
    wf_block = _wf_factor(fac, fwd)   # W7-2 walk-forward:逐段 OOS RankIC + 汇总(始终算,够期数才出)
    if oos_block.get("verdict") in ("overfit", "degraded"):
        warnings.append(f"样本外体检:{oos_block.get('note', '')}(样本内 RankIC "
                        f"{_num((oos_block.get('is') or {}).get('rank_ic'))} → 样本外 "
                        f"{_num((oos_block.get('oos') or {}).get('rank_ic'))})。")

    out: Dict[str, Any] = {
        "ok": True, "universe": universe, "method": "report2",
        "expr_or_name": expr, "freq": freq, "n_groups": n_groups, "fwd_days": fwd_days, "direction": direction,
        "neutralize": neutralize_info,   # #1 中性化:requested/applied/by(行业/市值)/kept/raw/note
        "n_dates": n_dates, "n_codes": int(report.meta.n_codes),
        "oos": oos_block,   # W7 样本外体检块(enabled/is/oos/decay_ratio/verdict/note)
        "walkforward": wf_block,   # W7-2 walk-forward 逐段 RankIC + 汇总
        "ic_decay": ic_decay,            # #2 截面 IC 衰减谱 [{h,rank_ic,rank_icir,ic}](1/3/5/10/20 日)
        "ic_decay_peak": ic_decay_peak,  # |rank-IC| 峰值 horizon = 最优持有期参考
        "headline_ic": {
            "rank_ic": _num(ic_head.rank_ic_mean), "rank_icir": _num(ic_head.rank_icir),
            "ic": _num(ic_head.ic_mean), "icir": _num(ic_head.icir), "ic_tstat": _num(ic_head.ic_tstat),
        },
        "meta": rep_dict.get("meta"), "ic": rep_dict.get("ic") or {},
        "quantile": rep_dict.get("quantile") or {}, "portfolio": rep_dict.get("portfolio") or {},
        "characteristics": rep_dict.get("characteristics") or {},
        "warnings": warnings, "status": "ok",
        "report": rep_dict, "composite": True,
        "_label": expr, "_universe": universe,
    }
    return JSONResponse(out)


# ───────── 单票时序因子评测 · 标准工具箱(P1)— 纯函数,入 pandas Series/标量,出 JSON 安全 dict ─────────
# tsic 逐股评测从「仅 Spearman IC + 重叠校正 t + 衰减剖面」扩到学界标准全集:
#   ① _rolling_icir       时序ICIR(滚动窗 IC 稳定性)+ IC胜率
#   ② _predictive_reg     预测回归 β + Newey-West(HAC)t + 样本内R²(重叠 h 日收益必做 HAC)
#   ③ _pesaran_timmermann 命中率 + Pesaran-Timmermann(1992)择时检验(抽稀到不重叠样本)
#   ④ _quantile_monotone  Pearson 线性IC + 单票自身因子历史的分位单调性
#   ⑤ _ic_halflife        IC半衰期(从池均衰减剖面线性插值)
# 重叠诚实:只有 ② 在全样本跑 t/z→用 Newey-West HAC(lag=fwd_days-1)修正;③ 先按每 fwd_days 行抽稀;
# ① 只报跨窗稳定性(不报窗内 t);④⑤ 只出点估计不做 n-based t/z。全部 _num 兜底(NaN/Inf→None),永不抛。

def _rolling_icir(f, r, fwd_days, win=63, step=21):
    """M1 时序 ICIR(滚动窗口 IC 稳定性)+ IC 胜率。逐窗 Spearman IC 的 均值/标准差 →
    ICIR=mean/std(预测力跨时间稳定性),ic_win_rate=IC>0 窗占比。需≥4 窗才报 ICIR/ic_std。"""
    import numpy as np
    import pandas as pd
    out = {"icir": None, "ic_mean": None, "ic_std": None,
           "ic_win_rate": None, "n_windows": 0}
    try:
        n = len(f)
        win = int(win)
        step = max(1, int(step))
        min_rows = max(20, 2 * int(fwd_days))
        ics = []
        i = 0
        while i + win <= n:                       # 仅保留完整窗,不足尾段窗丢弃
            fw = f.iloc[i:i + win]
            rw = r.iloc[i:i + win]
            if len(fw) >= min_rows:
                fr = fw.rank()
                rr = rw.rank()
                if fr.std(ddof=0) > 0 and rr.std(ddof=0) > 0:   # 常数因子/收益 → 跳过该窗
                    ic = fr.corr(rr)
                    if ic == ic:
                        ics.append(float(ic))
            i += step
        nw = len(ics)
        out["n_windows"] = int(nw)
        if nw >= 1:
            out["ic_mean"] = _num(float(np.mean(ics)))
            out["ic_win_rate"] = _num(sum(1 for v in ics if v > 0) / float(nw))
        if nw >= 4:
            sd = float(pd.Series(ics).std(ddof=1))
            out["ic_std"] = _num(sd)
            if sd > 0:
                out["icir"] = _num(float(np.mean(ics)) / sd)
        return out
    except Exception:  # noqa: BLE001 —— 绝不抛出,降级为全 None
        return {"icir": None, "ic_mean": None, "ic_std": None,
                "ic_win_rate": None, "n_windows": 0}


def _predictive_reg(f, r, fwd_days):
    """M2 预测回归 r_t=alpha+beta*f_t+e_t:beta(方向/量级)、alpha、样本内 R²、斜率的
    Newey-West(HAC, Bartlett 核, lag=fwd_days-1)t 值 nw_t 与双侧正态 p。重叠 h 日收益使残差自相关,
    普通 OLS 的 t 高估 2~4 倍,故 HAC 必做(Newey-West 1987)。nw_p 用渐近正态,小样本偏乐观(见 warnings)。"""
    import numpy as np
    import pandas as pd
    out = {"beta": None, "alpha": None, "r2": None, "nw_t": None,
           "nw_p": None, "nw_lag": None, "nw_sig": None}
    try:
        x = pd.to_numeric(f, errors="coerce").to_numpy(dtype="float64")
        y = pd.to_numeric(r, errors="coerce").to_numpy(dtype="float64")
        m = np.isfinite(x) & np.isfinite(y)
        x = x[m]; y = y[m]
        n = int(x.size)
        if n <= 3:
            return out
        if not np.isfinite(np.std(x)) or np.std(x) == 0.0:    # 常数因子 → 设计阵奇异
            return out
        X = np.column_stack([np.ones(n), x])                  # [1, f]
        XtX = X.T @ X
        try:
            XtXinv = np.linalg.inv(XtX)                       # bread = (X'X)^-1
        except np.linalg.LinAlgError:
            return out
        beta_vec = XtXinv @ (X.T @ y)                         # [alpha, beta]
        alpha = float(beta_vec[0]); beta = float(beta_vec[1])
        e = y - X @ beta_vec
        ss_res = float(np.sum(e * e))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
        # HAC 长程方差 S = g0 + Σ_{l=1..L}(1-l/(L+1))(gl+gl');得分 u_t = x_t·e_t(含截距行)
        U = X * e[:, None]
        L = int(min(int(fwd_days) - 1, n - 2, max(0, n // 2)))
        if L < 0:
            L = 0
        S = U.T @ U                                           # g0
        for lag in range(1, L + 1):
            w = 1.0 - lag / (L + 1.0)                          # Bartlett 权
            G = U[lag:].T @ U[:n - lag]
            S = S + w * (G + G.T)
        V = XtXinv @ S @ XtXinv                                # 三明治
        var_beta = float(V[1, 1])
        nw_t = None; nw_p = None
        if math.isfinite(var_beta) and var_beta > 0:
            se_beta = math.sqrt(var_beta)
            if se_beta > 0:
                nw_t = beta / se_beta
                z = abs(nw_t)
                nw_p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
        out["beta"] = _num(beta)
        out["alpha"] = _num(alpha)
        out["r2"] = _num(r2)
        out["nw_t"] = _num(nw_t)
        out["nw_p"] = _num(nw_p)
        out["nw_lag"] = int(L)
        out["nw_sig"] = (bool(abs(nw_t) >= 2.0) if nw_t is not None else None)
        return out
    except Exception:
        return {"beta": None, "alpha": None, "r2": None, "nw_t": None,
                "nw_p": None, "nw_lag": None, "nw_sig": None}


def _pesaran_timmermann(f, r, fwd_days):
    """M3 方向命中率 + Pesaran-Timmermann(1992)择时检验。x_t=sign(f_t-median(f)),y_t=sign(r_t)。
    重叠修正:检验前按每 fwd_days 行抽稀成不重叠子样本(PT 假设 iid)。逆向因子 hit<0.5 但仍有信息,
    故除 hit 另报 pt_stat(检验方向独立性,不预设方向)。返回 {hit, pt_stat, pt_p, n}。"""
    import numpy as np
    import pandas as pd
    out = {"hit": None, "pt_stat": None, "pt_p": None, "n": 0}
    try:
        fw = int(fwd_days)
        if fw < 1:
            fw = 1
        thin = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()
        if thin.empty:
            return out
        thin = thin.iloc[::fw]                                # 每 fw 行抽稀 → 不重叠
        fmed = float(thin["f"].median())
        x = np.sign(thin["f"].to_numpy(dtype="float64") - fmed)
        y = np.sign(thin["r"].to_numpy(dtype="float64"))
        keep = (x != 0.0) & (y != 0.0)                        # 丢弃零方向行
        x = x[keep]; y = y[keep]
        n = int(x.size)
        out["n"] = n
        if n < 20:                                            # 抽稀后样本不足 → 降级
            return out
        hit = float(np.mean(x == y))
        out["hit"] = _num(hit)
        Py = float(np.mean(y > 0.0))
        Px = float(np.mean(x > 0.0))
        if Px <= 0.0 or Px >= 1.0 or Py <= 0.0 or Py >= 1.0:  # 退化 → PT 无定义
            return out
        P = hit
        Pstar = Py * Px + (1.0 - Py) * (1.0 - Px)
        var_P = Pstar * (1.0 - Pstar) / n
        var_Pstar = ((1.0 / n) * ((2.0 * Py - 1.0) ** 2) * Px * (1.0 - Px)
                     + (1.0 / n) * ((2.0 * Px - 1.0) ** 2) * Py * (1.0 - Py)
                     + (4.0 / (n * n)) * Py * Px * (1.0 - Py) * (1.0 - Px))
        denom = var_P - var_Pstar
        if denom <= 0.0:
            return out
        pt = (P - Pstar) / math.sqrt(denom)
        out["pt_stat"] = _num(pt)
        out["pt_p"] = _num(2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(pt) / math.sqrt(2.0)))))
    except Exception:
        return {"hit": None, "pt_stat": None, "pt_p": None, "n": out.get("n", 0)}
    return out


def _quantile_monotone(f, r, fwd_days, q=5):
    """M4 Pearson 线性 IC + 单票自身因子历史分位单调性。pearson_ic=corr(f,r);把该股因子历史按 f 值
    切 q 分位桶,每桶取未来收益均值,spread=top-bottom,mono=Spearman(桶序号,桶均值)∈[-1,1]。
    单一对齐源(concat+dropna)避免 index/positional 错配。单票短历史下桶薄,仅作方向参考(由池均化承担显著性)。"""
    import pandas as pd
    out = {"pearson_ic": None, "buckets": None, "spread": None,
           "mono": None, "q_used": None}
    try:
        j2 = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()   # 唯一对齐源
        if len(j2) < 6:
            return out
        fj = j2["f"]; rj = j2["r"]
        try:                                                  # Pearson 线性 IC(与秩 IC 互补)
            if float(fj.std()) > 0.0 and float(rj.std()) > 0.0:
                out["pearson_ic"] = _num(fj.corr(rj))
        except Exception:  # noqa: BLE001
            out["pearson_ic"] = None
        try:
            qn = int(q) if int(q) >= 3 else 5
        except Exception:  # noqa: BLE001
            qn = 5
        std_f = float(fj.std())
        if not (std_f == std_f) or std_f <= 0.0:              # 常数因子 → 无法分桶
            return out
        try:
            cats = pd.qcut(fj, qn, labels=False, duplicates="drop")
        except Exception:  # noqa: BLE001
            return out
        df = pd.DataFrame({"b": cats.values, "r": rj.values}).dropna()    # 同源 positional
        if df.empty:
            return out
        means = df.groupby("b")["r"].mean().sort_index()      # 按桶序号(=因子升序)
        k = int(len(means))
        out["q_used"] = k
        if k < 3:
            return out
        out["buckets"] = [[i + 1, _num(float(mv))] for i, mv in enumerate(means.tolist())]
        out["spread"] = _num(float(means.iloc[-1]) - float(means.iloc[0]))
        rank_idx = pd.Series(range(k), dtype=float).rank()
        rank_mu = pd.Series(means.values).rank()
        if float(rank_mu.std()) > 0.0:                        # 桶均值全相等 → 单调性未定义
            out["mono"] = _num(rank_idx.corr(rank_mu))
        else:
            out["mono"] = None
        return out
    except Exception:  # noqa: BLE001
        return {"pearson_ic": None, "buckets": None, "spread": None,
                "mono": None, "q_used": None}


def _ic_halflife(decay):
    """M5 IC 半衰期:从池均衰减剖面 decay=[[h, pool_mean_tsic, pos_ratio], ...] 求 |IC| 峰值 horizon
    与衰减到峰值一半的 horizon(峰后线性插值)。decayed=观测窗内确已减半;still_rising=峰在最大 horizon。
    用 |IC| 量级(逆向负 IC 也衰减);不读面板、不做重叠修正(消费已聚合 IC)。"""
    out = {"peak_h": None, "peak_ic": None, "half_life": None,
           "decayed": False, "still_rising": False}
    try:
        pts = []
        for row in (decay or []):
            if not row or len(row) < 2:
                continue
            h = row[0]; ic = row[1]
            if h is None or ic is None or h != h or ic != ic:
                continue
            pts.append((float(h), float(ic)))
        pts.sort(key=lambda p: p[0])
        if len(pts) < 2:
            return out
        mags = [abs(ic) for _, ic in pts]
        pk = max(range(len(pts)), key=lambda i: mags[i])      # |IC| 峰值位置
        peak_h, peak_ic = pts[pk]
        peak_mag = mags[pk]
        out["peak_h"] = int(round(peak_h))
        out["peak_ic"] = _num(peak_ic)                        # 带符号
        if peak_mag == 0.0:
            return out
        if pk == len(pts) - 1:                                # 峰在最大 horizon → 仍上升
            out["still_rising"] = True
            return out
        target = 0.5 * peak_mag
        for i in range(pk + 1, len(pts)):                     # 仅峰后找首次跨越
            if mags[i] <= target:
                ha, a = pts[i - 1][0], mags[i - 1]
                hb, b = pts[i][0], mags[i]
                if a <= target:
                    hl = ha
                elif a == b:
                    hl = hb
                else:
                    hl = ha + ((a - target) / (a - b)) * (hb - ha)   # 线性插值
                out["half_life"] = _num(hl)
                out["decayed"] = True
                return out
        return out                                            # 观测窗内未减半 → decayed=False
    except Exception:
        return {"peak_h": None, "peak_ic": None, "half_life": None,
                "decayed": False, "still_rising": False}


def _oos_predictive(f, r, fwd_days, oos_frac=0.4, min_train=40):
    """P2 样本外预测力(可信度金标准):扩展窗(expanding)逐点向前预测的 Campbell-Thompson 样本外
    R²_OS + Clark-West(2007)MSPE-adjusted 检验。样本前段训练、尾段逐点 OOS:预测第 t 点时**只用
    结果已实现的训练点 [0 : t-fwd_days]**(滞后 fwd_days 防重叠前视),拟合预测回归 r̂=α+β·f,
    基准=该训练集历史均值 r̄(prevailing mean)。
      r2_os = 1 - Σ(r-r̂)²/Σ(r-r̄)²   (>0 = 因子样本外胜「历史均值」基准 → 真可信,非样本内拟合)
      cw_stat = Clark-West:f̂=(r-r̄)²-(r-r̂)²+(r̄-r̂)²,对常数回归取 mean/NW-se(单侧,lag=fwd-1 修重叠)
      cw_p = 单侧正态 p;n_oos = OOS 点数;oos_beta = 末次 OOS 训练得到的 β。
    运行累加 O(n);任何异常 / 样本不足 → 全 None。"""
    import numpy as np
    import pandas as pd
    out = {"r2_os": None, "cw_stat": None, "cw_p": None, "n_oos": 0, "oos_beta": None}
    try:
        x = pd.to_numeric(f, errors="coerce").to_numpy(dtype="float64")
        y = pd.to_numeric(r, errors="coerce").to_numpy(dtype="float64")
        m = np.isfinite(x) & np.isfinite(y)
        x = x[m]; y = y[m]
        n = int(x.size)
        fw = max(1, int(fwd_days))
        mt = max(int(min_train), 20)
        k0 = max(mt + fw, int(round(n * (1.0 - float(oos_frac)))))   # 首个 OOS 点
        if k0 >= n:
            return out
        ub = k0 - fw                       # 训练集上界(含):用 indices 0..ub 预测点 k0
        if ub < mt - 1:                    # 训练点不足
            return out
        Sx = float(np.sum(x[:ub + 1])); Sy = float(np.sum(y[:ub + 1]))
        Sxx = float(np.sum(x[:ub + 1] * x[:ub + 1])); Sxy = float(np.sum(x[:ub + 1] * y[:ub + 1]))
        cnt = ub + 1
        se_model = 0.0; se_bench = 0.0
        fhats = []; last_beta = None
        for t in range(k0, n):
            denom = cnt * Sxx - Sx * Sx
            mean_y = Sy / cnt
            if denom != 0.0 and math.isfinite(denom):
                beta = (cnt * Sxy - Sx * Sy) / denom
                alpha = mean_y - beta * (Sx / cnt)
            else:                          # 训练因子无方差 → 退化为历史均值预测
                beta = 0.0; alpha = mean_y
            rhat = alpha + beta * x[t]; rbar = mean_y
            e_m = y[t] - rhat; e_b = y[t] - rbar
            se_model += e_m * e_m; se_bench += e_b * e_b
            fhats.append(e_b * e_b - e_m * e_m + (rbar - rhat) * (rbar - rhat))
            last_beta = beta
            add = t - fw + 1               # 新实现的训练点(滞后 fw 纳入,防前视)
            if 0 <= add < n:
                Sx += x[add]; Sy += y[add]
                Sxx += x[add] * x[add]; Sxy += x[add] * y[add]; cnt += 1
        n_oos = len(fhats)
        out["n_oos"] = int(n_oos)
        out["oos_beta"] = _num(last_beta)
        if se_bench > 0:
            out["r2_os"] = _num(1.0 - se_model / se_bench)
        fa = np.asarray(fhats, dtype="float64")
        nf = int(fa.size)
        if nf >= 8:                        # Clark-West:f̂ 对常数回归的 NW-t(单侧)
            mu = float(np.mean(fa))
            u = fa - mu
            L = int(min(fw - 1, nf - 2, max(0, nf // 2)))
            if L < 0:
                L = 0
            S = float(np.dot(u, u)) / nf                  # γ0
            for lag in range(1, L + 1):
                w = 1.0 - lag / (L + 1.0)
                gl = float(np.dot(u[lag:], u[:nf - lag])) / nf
                S += 2.0 * w * gl                          # γ0 + 2Σ w·γl
            var_mean = S / nf
            if math.isfinite(var_mean) and var_mean > 0:
                cw = mu / math.sqrt(var_mean)
                out["cw_stat"] = _num(cw)
                out["cw_p"] = _num(1.0 - 0.5 * (1.0 + math.erf(cw / math.sqrt(2.0))))   # 单侧
        return out
    except Exception:
        return {"r2_os": None, "cw_stat": None, "cw_p": None, "n_oos": 0, "oos_beta": None}


def _timing_backtest(f, r1, cost_bps=5.0, win=60, gamma=3.0, ann=252):
    """P3 经济价值层:把连续因子变成单票【做多/空仓】择时,诚实对比买入持有(成本公平)。
    信号 z_t=(f_t-trailing均)/trailing标准差(窗口截至 t,右对齐 → 无前视);pos_t=1.0 当 z_t>0 否则 0.0。
    绝不用全样本统计量/IC符号选方向(方向翻转交上游 direction)。A股个股做空受限 → long/flat。
    P&L: strat_t=pos_t·r1_t - cost·|pos_t-pos_{t-1}|(pos_{-1}=0)。
    买入持有 bh_t=r1_t - cost·|1-pos^bh_{t-1}|(pos^bh 始终=1,仅 bar0 一次 0→1 进场计费 → 成本公平)。
    r1 须为 t→t+1 已实现收益(forward_simple_returns(panel,1) 在 t 已是 close(t+1)/close(t)-1);
    调用方对齐去 NaN、升序 DatetimeIndex,pos·r1 不再加任何 shift。返回 JSON 安全标量(全过 _num)
    + 私有键 _ret(net 策略日收益 Series)+ _bh_ret(net 买入持有日收益 Series),供池级净值聚合,逐股不下发。
    纯函数,绝不抛出:任何异常 / n<max(win,60) → 全 None 标量 + 空 _ret/_bh_ret。"""
    import numpy as np
    import pandas as pd
    import math
    EMPTY = pd.Series(dtype="float64")
    NONE = {
        "sharpe": None, "sortino": None, "calmar": None, "maxdd": None,
        "ann_ret": None, "ann_vol": None, "win_rate": None, "turnover": None,
        "exposure": None, "cer": None, "n_days": 0,
        "bh_sharpe": None, "bh_ann_ret": None, "bh_maxdd": None, "bh_cer": None,
        "beat_bh": None, "cer_gain": None, "_ret": EMPTY, "_bh_ret": EMPTY,
    }
    try:
        win = max(2, int(win))
        ann = float(ann) if (ann and float(ann) > 0) else 252.0
        cost = float(cost_bps) / 1e4

        fj = pd.to_numeric(f, errors="coerce")
        rj = pd.to_numeric(r1, errors="coerce")
        j = pd.concat([fj.rename("f"), rj.rename("r")], axis=1).dropna()
        j = j[np.isfinite(j["f"]) & np.isfinite(j["r"])]
        n = int(len(j))
        if n < max(win, 60):
            return dict(NONE)

        # 信号:trailing 滚动标准化(右对齐,f_t 是窗口最后一个元素 → z_t 只见 f≤t → 无前视)
        fcol = j["f"]
        mp = max(2, win // 2)
        mu = fcol.rolling(win, min_periods=mp).mean()
        sd = fcol.rolling(win, min_periods=mp).std(ddof=1)
        z = (fcol - mu) / sd
        z = z.replace([np.inf, -np.inf], np.nan)
        pos = (z > 0).astype("float64")          # NaN z → False → 0.0(warmup/常数因子 → 空仓)
        prev = pos.shift(1).fillna(0.0)          # pos_{-1}=0;仅用于换手成本,不与 r 相乘
        dpos = (pos - prev).abs()

        r = j["r"].astype("float64")
        strat = pos * r - cost * dpos            # 策略 net 日收益(扣换手成本)

        # 买入持有成本公平:pos_bh≡1,仅 bar0 一次进场换手 → 与 strat 同口径计费
        bh_pos = pd.Series(1.0, index=j.index)
        bh_prev = bh_pos.shift(1).fillna(0.0)
        bh_dpos = (bh_pos - bh_prev).abs()       # 仅首行=1,其余=0
        bh = r - cost * bh_dpos                  # 买入持有 net 日收益(单次进场成本)

        def _stats(daily, pos_mask=None):
            d = pd.to_numeric(daily, errors="coerce")
            d = d[np.isfinite(d)]
            if int(len(d)) < 2:
                return {"sharpe": None, "sortino": None, "calmar": None, "maxdd": None,
                        "ann_ret": None, "ann_vol": None, "win_rate": None, "cer": None}
            arr = d.to_numpy(dtype="float64")
            ann_ret = float(np.mean(arr)) * ann
            vol = float(np.std(arr, ddof=1))
            ann_vol = vol * math.sqrt(ann)
            sharpe = (ann_ret / ann_vol) if (ann_vol > 0 and math.isfinite(ann_vol)) else None
            down = np.minimum(arr, 0.0)            # 下行偏差:RMS of min(daily,0)
            dd_std = math.sqrt(float(np.mean(down * down)))
            down_ann = dd_std * math.sqrt(ann)
            sortino = (ann_ret / down_ann) if (down_ann > 0 and math.isfinite(down_ann)) else None
            eq = np.cumprod(1.0 + arr)             # 净值 = cumprod(1+daily)
            peak = np.maximum.accumulate(eq)
            maxdd = float(np.min(eq / peak - 1.0)) if eq.size else None
            calmar = (ann_ret / abs(maxdd)) if (maxdd is not None and maxdd < 0) else None
            if pos_mask is not None:               # 胜率仅在持仓日(pos=1)统计
                pm = pos_mask.to_numpy(dtype="float64") > 0.5
                on = arr[pm]
                win_rate = (float(np.mean(on > 0.0)) if on.size else None)
            else:
                win_rate = float(np.mean(arr > 0.0))
            cer = ann_ret - 0.5 * float(gamma) * ann_vol * ann_vol   # 确定性等价(均值-方差)
            return {"sharpe": sharpe, "sortino": sortino, "calmar": calmar, "maxdd": maxdd,
                    "ann_ret": ann_ret, "ann_vol": ann_vol, "win_rate": win_rate, "cer": cer}

        s = _stats(strat, pos_mask=pos)
        b = _stats(bh, pos_mask=None)

        turnover = float(dpos.mean()) * ann
        exposure = float(pos.mean())

        beat_bh = None
        if s["sharpe"] is not None and b["sharpe"] is not None:
            beat_bh = bool(s["sharpe"] > b["sharpe"])
        cer_gain = None
        if s["cer"] is not None and b["cer"] is not None:
            cer_gain = s["cer"] - b["cer"]

        return {
            "sharpe": _num(s["sharpe"]), "sortino": _num(s["sortino"]),
            "calmar": _num(s["calmar"]), "maxdd": _num(s["maxdd"]),
            "ann_ret": _num(s["ann_ret"]), "ann_vol": _num(s["ann_vol"]),
            "win_rate": _num(s["win_rate"]), "turnover": _num(turnover),
            "exposure": _num(exposure), "cer": _num(s["cer"]), "n_days": int(n),
            "bh_sharpe": _num(b["sharpe"]), "bh_ann_ret": _num(b["ann_ret"]),
            "bh_maxdd": _num(b["maxdd"]), "bh_cer": _num(b["cer"]),
            "beat_bh": beat_bh, "cer_gain": _num(cer_gain),
            "_ret": strat, "_bh_ret": bh,           # 私有 pandas Series,池级净值用,不进 JSON
        }
    except Exception:  # noqa: BLE001 —— 绝不抛出
        return {"sharpe": None, "sortino": None, "calmar": None, "maxdd": None,
                "ann_ret": None, "ann_vol": None, "win_rate": None, "turnover": None,
                "exposure": None, "cer": None, "n_days": 0,
                "bh_sharpe": None, "bh_ann_ret": None, "bh_maxdd": None, "bh_cer": None,
                "beat_bh": None, "cer_gain": None,
                "_ret": EMPTY, "_bh_ret": EMPTY}


class FactorTsicIn(ModelTrainIn):
    """``POST /factor/tsic`` 入参:**个股时序 IC** —— 每只票的因子值 vs 它**自己**未来收益的
    时序相关(Spearman),专为单票/小池设计(截面 IC 在单票上退化 → 无截面无法求相关)。
    因子取 RAW 值(不做截面 zscore,否则单票恒为常数)。继承 ModelTrainIn 的 codes/universe/
    start/end/benchmark(注 idx_ret 做共振)/leader 等。"""

    expr_or_name: str = ""        # 因子表达式 / 注册名
    fwd_days: int = 20            # 未来收益窗口(交易日)
    direction: int = 0           # 0 原始 / -1 取反


def _factor_tsic(body: "FactorTsicIn") -> JSONResponse:
    """逐股票算时序 IC = Spearman(因子值ₜ, 该股未来 fwd_days 日收益ₜ) over time。单票/小池口径,
    截面 IC 退化时的正确替代。复用 report2 的 universe→panel→inject_refs→compile 链,不动引擎。"""
    import pandas as pd
    import numpy as np
    import math
    from financial_analyst.factors.eval.report import forward_simple_returns

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    expr = (body.expr_or_name or "").strip()
    if not expr:
        return _fail_factor(universe, "因子表达式/名称不能为空", method="tsic")
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)).isoformat()
    fwd_days = max(1, int(getattr(body, "fwd_days", 20) or 20))
    direction = int(getattr(body, "direction", 0) or 0)

    custom = _norm_codes(getattr(body, "codes", None))
    if custom:
        codes = custom
    else:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail_factor(universe, f"universe_error: {type(exc).__name__}: {exc}", method="tsic")
    if not codes:
        return _fail_factor(universe, f"empty_universe: '{universe}' 解析为空", method="tsic")
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:  # noqa: BLE001
            ind_loader = None
        panel = load_panel_cached(loader, codes, start, end, freq="day", industry_loader=ind_loader)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"load_error: {type(exc).__name__}: {exc}", method="tsic", start=start, end=end)

    panel, _ref_warn = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                           getattr(body, "leader", None), start, end, "day")
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from financial_analyst.factors.zoo.registry import get as _get_alpha
        try:
            factor_s = _get_alpha(expr).compute(panel)
        except KeyError:
            validate_expr(expr)
            factor_s = compile_factor(expr)(panel)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"{type(exc).__name__}: {exc}", method="tsic", start=start, end=end)
    if not isinstance(factor_s, pd.Series):
        return _fail_factor(universe, "因子求值非 Series", method="tsic", start=start, end=end)
    if direction < 0:
        factor_s = -factor_s

    fwd = forward_simple_returns(panel, fwd_days)
    r1_pool = forward_simple_returns(panel, 1)        # P3 择时:t→t+1 已实现收益(在 t 已知,不再 shift)
    r1_c = r1_pool.index.get_level_values("code")
    pool_strat: Dict[Any, List[float]] = {}           # datetime -> [各股 net 策略日收益](池级净值)
    pool_bh: Dict[Any, List[float]] = {}              # datetime -> [各股 net 买入持有日收益]
    fac_c = factor_s.index.get_level_values("code")
    fwd_c = fwd.index.get_level_values("code")
    rows: List[Dict[str, Any]] = []
    period_acc: Dict[int, List[float]] = {}   # 分时段:{自然年: [逐股该年 Spearman IC]}
    for code in [str(c) for c in panel.codes()]:
        fc = factor_s[fac_c == code].dropna()
        rc = fwd[fwd_c == code]
        if fc.empty or rc.empty:
            continue
        fa = fc.copy(); fa.index = fa.index.get_level_values("datetime")
        ra = rc.copy(); ra.index = ra.index.get_level_values("datetime")
        j = pd.concat([fa.rename("f"), ra.rename("r")], axis=1).dropna()
        if len(j) < 30:
            rows.append({"code": code, "tsic": None, "n": int(len(j))})
            continue
        rho = j["f"].rank().corr(j["r"].rank())   # Spearman 时序 IC
        tval = None
        if rho == rho and abs(rho) < 1.0:
            n_eff = max(2.0, len(j) / float(fwd_days))   # 重叠校正:有效样本≈N/窗口
            tval = float(rho * math.sqrt((n_eff - 2.0) / (1.0 - rho * rho)))
        # 标准工具箱(P1):逐股算 ICIR/预测回归/PT择时/分位单调(纯函数,永不抛,降级 None)
        jf, jr = j["f"], j["r"]
        m_icir = _rolling_icir(jf, jr, fwd_days)
        m_reg = _predictive_reg(jf, jr, fwd_days)
        m_pt = _pesaran_timmermann(jf, jr, fwd_days)
        m_qm = _quantile_monotone(jf, jr, fwd_days)
        m_oos = _oos_predictive(jf, jr, fwd_days)
        for _yr, _g in j.groupby(j.index.year):   # 分时段:逐自然年 Spearman IC,累进池
            if len(_g) >= 20:
                _icy = _g["f"].rank().corr(_g["r"].rank())
                if _icy == _icy:
                    period_acc.setdefault(int(_yr), []).append(float(_icy))
        # P3 经济价值:单票 long/flat 择时回测(成本公平 vs 买入持有,无前视)。
        # 用整段因子 fa 对齐 t→t+1 收益(非 j 的 fwd_days 口径),pos·r1 不再 shift。
        r1c = r1_pool[r1_c == code]
        r1a = r1c.copy(); r1a.index = r1a.index.get_level_values("datetime")
        jt = pd.concat([fa.rename("f"), r1a.rename("r1")], axis=1).dropna()
        m_tb = _timing_backtest(jt["f"], jt["r1"])
        _sr = m_tb.pop("_ret", None); _br = m_tb.pop("_bh_ret", None)
        if _sr is not None and len(_sr) > 0 and _br is not None and len(_br) > 0:
            for _dt, _v in _sr.items():
                pool_strat.setdefault(_dt, []).append(float(_v))
            for _dt, _v in _br.items():
                pool_bh.setdefault(_dt, []).append(float(_v))
        rows.append({
            "code": code,
            "tsic": (_num(round(float(rho), 4)) if rho == rho else None),
            "t": (_num(round(tval, 2)) if tval is not None else None), "n": int(len(j)),
            "pearson": m_qm.get("pearson_ic"),
            "icir": m_icir.get("icir"), "ic_mean": m_icir.get("ic_mean"),
            "ic_std": m_icir.get("ic_std"), "ic_win": m_icir.get("ic_win_rate"),
            "n_win": m_icir.get("n_windows"),
            "beta": m_reg.get("beta"), "nw_t": m_reg.get("nw_t"), "nw_p": m_reg.get("nw_p"),
            "r2": m_reg.get("r2"), "nw_lag": m_reg.get("nw_lag"), "nw_sig": m_reg.get("nw_sig"),
            "hit": m_pt.get("hit"), "pt": m_pt.get("pt_stat"), "pt_p": m_pt.get("pt_p"),
            "pt_n": m_pt.get("n"),
            "spread": m_qm.get("spread"), "mono": m_qm.get("mono"),
            "q_used": m_qm.get("q_used"), "buckets": m_qm.get("buckets"),
            "r2_os": m_oos.get("r2_os"), "cw": m_oos.get("cw_stat"),
            "cw_p": m_oos.get("cw_p"), "n_oos": m_oos.get("n_oos"),
            "tb_sharpe": m_tb.get("sharpe"), "tb_sortino": m_tb.get("sortino"),
            "tb_calmar": m_tb.get("calmar"), "tb_maxdd": m_tb.get("maxdd"),
            "tb_ann": m_tb.get("ann_ret"), "tb_turnover": m_tb.get("turnover"),
            "tb_exposure": m_tb.get("exposure"), "tb_win": m_tb.get("win_rate"),
            "tb_beat_bh": m_tb.get("beat_bh"), "tb_cer_gain": m_tb.get("cer_gain"),
            "tb_bh_sharpe": m_tb.get("bh_sharpe"),
        })

    valid = [r for r in rows if r.get("tsic") is not None]
    if not valid:
        return _fail_factor(universe, f"无足够样本算时序IC(每股需≥30个对齐点;fwd={fwd_days})",
                            method="tsic", start=start, end=end)
    vs = [r["tsic"] for r in valid]
    rows_sorted = sorted(rows, key=lambda r: (r.get("tsic") is None, -(r.get("tsic") or 0.0)))

    # 多滞后衰减剖面:池均时序IC 随前向窗口 h 的变化(看预测力随 horizon 衰减/反转)。
    def _pool_tsic_at(h: int):
        fwdh = forward_simple_returns(panel, h)
        fwdh_c = fwdh.index.get_level_values("code")
        vlist: List[float] = []
        for _code in [str(c) for c in panel.codes()]:
            fc2 = factor_s[fac_c == _code].dropna()
            rc2 = fwdh[fwdh_c == _code]
            if fc2.empty or rc2.empty:
                continue
            fa2 = fc2.copy(); fa2.index = fa2.index.get_level_values("datetime")
            ra2 = rc2.copy(); ra2.index = ra2.index.get_level_values("datetime")
            j2 = pd.concat([fa2.rename("f"), ra2.rename("r")], axis=1).dropna()
            if len(j2) < 30:
                continue
            rr = j2["f"].rank().corr(j2["r"].rank())
            if rr == rr:
                vlist.append(float(rr))
        return [_num(float(np.mean(vlist))), _num(sum(1 for v in vlist if v > 0) / len(vlist))] if vlist else None

    decay = []
    for h in sorted(set([1, 5, 10, 20, 40] + [fwd_days])):
        pt = _pool_tsic_at(h)
        if pt is not None:
            decay.append([int(h), pt[0], pt[1]])
    ts = [r.get("t") for r in valid if r.get("t") is not None]

    def _avg(key):   # 池均化:对非 None 的逐股值取均值(标准工具箱各指标)
        xs = [r.get(key) for r in valid if r.get(key) is not None]
        return _num(float(np.mean(xs))) if xs else None

    nw_sigs = [r.get("nw_sig") for r in valid if r.get("nw_sig") is not None]
    pts_v = [r.get("pt") for r in valid if r.get("pt") is not None]
    r2os_v = [r.get("r2_os") for r in valid if r.get("r2_os") is not None]
    cw_v = [r.get("cw") for r in valid if r.get("cw") is not None]
    period_ic = [[yr, _num(float(np.mean(v))), len(v)]   # 分时段:逐自然年池均 IC
                 for yr, v in sorted(period_acc.items())]
    hl = _ic_halflife(decay)   # IC 半衰期(池均衰减剖面)
    summary = {
        "n_codes": len(valid),
        "mean_tsic": _num(float(np.mean(vs))),
        "median_tsic": _num(float(np.median(vs))),
        "pos_ratio": _num(sum(1 for v in vs if v > 0) / len(vs)),
        "sig_ratio": (_num(sum(1 for t in ts if abs(t) >= 2) / len(ts)) if ts else None),  # |t|≥2 占比
        "fwd_days": fwd_days,
        "avg_n": int(np.mean([r["n"] for r in valid])),
        "decay": decay,   # [[h, 池均tsic, 正占比], ...] 衰减剖面
        # ── 标准工具箱 · 池级汇总 ──
        "mean_icir": _avg("icir"),            # 时序ICIR 池均(>0.5 尚可、>1 强)
        "mean_pearson": _avg("pearson"),      # 线性 IC 池均
        "ic_win_pool": _avg("ic_win"),        # 滚动窗 IC 胜率池均
        "mean_hit": _avg("hit"),              # 方向命中率池均(0.5=无信息)
        "mean_mono": _avg("mono"),            # 分位单调性池均
        "mean_spread": _avg("spread"),        # 分位多空价差池均
        "nw_sig_ratio": (_num(sum(1 for s in nw_sigs if s) / len(nw_sigs)) if nw_sigs else None),  # 预测回归 |NW-t|≥2 占比
        "pt_sig_ratio": (_num(sum(1 for p in pts_v if abs(p) >= 1.96) / len(pts_v)) if pts_v else None),  # PT |z|≥1.96 占比
        # ── P2 样本外严谨 ──
        "mean_r2os": _avg("r2_os"),           # 样本外 R²_OS 池均(>0=胜「历史均值」基准,真可信)
        "r2os_pos_ratio": (_num(sum(1 for v in r2os_v if v > 0) / len(r2os_v)) if r2os_v else None),  # R²_OS>0 占比
        "cw_sig_ratio": (_num(sum(1 for c in cw_v if c >= 1.645) / len(cw_v)) if cw_v else None),  # Clark-West 单侧5% 占比
        "period_ic": period_ic,               # [[自然年, 池均IC, 股数], ...] 分时段稳定性
        "peak_h": hl.get("peak_h"), "peak_ic": hl.get("peak_ic"),
        "half_life": hl.get("half_life"), "decayed": hl.get("decayed"),
        "still_rising": hl.get("still_rising"),
    }

    # ── P3 池级净值 + 池级择时指标(等权日度再平衡:当日 present 股票均值;成本公平)──
    def _pool_daily(acc):
        items = [(d, float(np.mean(v))) for d, v in acc.items() if v]
        items.sort(key=lambda kv: kv[0])
        return items

    def _curve_stats(arr):                # 日收益 np 数组 → (sharpe, maxdd, ann)
        if arr.size < 2:
            return (None, None, None)
        ann_r = float(np.mean(arr)) * 252.0
        av = float(np.std(arr, ddof=1)) * math.sqrt(252.0)
        sh = (ann_r / av) if (av > 0 and math.isfinite(av)) else None
        eq = np.cumprod(1.0 + arr)
        mdd = float(np.min(eq / np.maximum.accumulate(eq) - 1.0))
        return (_num(sh), _num(mdd), _num(ann_r))

    _s_items = _pool_daily(pool_strat)
    _b_items = _pool_daily(pool_bh)
    timing_nav: List[List[Any]] = []
    timing_pool: Dict[str, Any] = {
        "pool_sharpe": None, "bh_sharpe": None, "delta_sharpe": None,
        "pool_maxdd": None, "bh_maxdd": None, "pool_ann": None, "bh_ann": None,
        "ann_excess": None, "frac_beat_bh": None, "n_days": 0,
    }
    if _s_items and _b_items:
        _sdate = [d for d, _ in _s_items]
        _sval = np.array([v for _, v in _s_items], dtype="float64")
        _bmap = {d: v for d, v in _b_items}
        _bval = np.array([_bmap.get(d, 0.0) for d in _sdate], dtype="float64")   # bh 对齐 strat 日期
        _ssh, _smdd, _sann = _curve_stats(_sval)
        _bsh, _bmdd, _bann = _curve_stats(_bval)
        _frac = _num(float(np.mean(_sval > _bval))) if _sval.size else None
        timing_pool = {
            "pool_sharpe": _ssh, "bh_sharpe": _bsh,
            "delta_sharpe": (_num(_ssh - _bsh) if (_ssh is not None and _bsh is not None) else None),
            "pool_maxdd": _smdd, "bh_maxdd": _bmdd, "pool_ann": _sann, "bh_ann": _bann,
            "ann_excess": (_num(_sann - _bann) if (_sann is not None and _bann is not None) else None),
            "frac_beat_bh": _frac, "n_days": int(_sval.size),
        }
        _snav = np.cumprod(1.0 + _sval); _bnav = np.cumprod(1.0 + _bval)
        _k = min(120, len(_sdate))
        _idx = (np.unique(np.linspace(0, len(_sdate) - 1, _k).astype(int)) if len(_sdate) > 1 else [0])
        for _i in _idx:
            _i = int(_i)
            timing_nav.append([str(pd.Timestamp(_sdate[_i]).date()),
                               _num(float(_snav[_i])), _num(float(_bnav[_i]))])
    summary["timing_nav"] = timing_nav
    summary["timing_pool"] = timing_pool

    warnings = list(_ref_warn or [])
    warnings.append(f"时序IC=每股因子值 vs 自身未来{fwd_days}日收益的 Spearman 相关(单票口径);"
                    "窗口重叠致有效样本远小于 N,|IC| 偏小属常态,勿过度解读。"
                    "t 值用重叠校正有效样本(N/窗口)算,|t|≥2 约显著;衰减剖面=池均时序IC 随前向窗口的变化。")
    warnings.append("时序体检扩展(逐股+池均):ICIR=滚动窗 IC 的均值/标准差(跨时间稳定性,|ICIR|>0.5 尚可、>1 强);"
                    "预测回归 β/样本内R² 配 Newey-West HAC t(lag=窗-1,修重叠致残差自相关;nw_p 用渐近正态,"
                    "单票小样本偏乐观,|t|≥2 仅作粗阈非精确检验);命中率+PT(Pesaran-Timmermann 1992)检验先按每窗抽稀到"
                    "不重叠样本,PT|z|≥1.96 约显著(双向,逆向因子命中<50% 仍可显著);分位单调=按自身因子分位的未来收益单调性"
                    "(短历史桶薄,看池均);半衰期=|IC| 衰减到峰值一半的前向窗口(稀疏网格线性插值,取数量级)。")
    warnings.append("P2 样本外:R²_OS(Campbell-Thompson)=因子样本外预测胜「历史均值」基准的程度,扩展窗逐点预测、"
                    "训练集滞后 fwd 防重叠前视,>0 才算真可信(样本内 IC 高但 R²_OS≤0=过拟合/数据挖掘);"
                    "Clark-West 单侧 z≥1.645 约 5% 显著(MSPE 调整 + NW 修重叠);分时段=逐自然年池均 IC,看跨行情稳定性。")
    warnings.append("P3 经济价值:单票 long/flat 择时(z_t=因子 trailing 标准化>0 才满仓,A股做空受限故只做多/空仓),"
                    "成本 5bps 按 |Δ仓位| 计、买入持有同口径仅首日进场计费(成本公平);单票日频高换手成本可吞噬信号属真实发现非bug。"
                    "池级净值=等权日度再平衡(当日 present 股票均值,名单内无生存者偏差校正);"
                    "delta_sharpe>0 / cer_gain>0 才算择时真有经济价值。逆向因子做多必跑输 → 用 direction=-1 翻转。")
    out: Dict[str, Any] = {
        "ok": True, "universe": universe, "method": "tsic",
        "expr_or_name": expr, "fwd_days": fwd_days, "direction": direction,
        "codes_tsic": rows_sorted, "summary": summary,
        "warnings": warnings, "status": "ok",
        "_label": expr, "_universe": universe,
    }
    return JSONResponse(out)


class FactorEventIn(ModelTrainIn):
    """``POST /factor/event`` 入参:**事件研究(event study)** —— 把一个 >0 的触发表达式当离散事件,
    统计每次触发后 horizon 日的前向收益(原始 + 市场调整异常收益 CAR)、命中率、t 值、逐年稳定、
    盈亏比/期望值。离散触发(反弹/异动/放量/跳空/消息面)的**正确口径**——截面 IC 对稀疏布尔信号
    是错口径。继承 ModelTrainIn 的 codes/universe/start/end/benchmark/leader 等。事件研究天生是
    **股票池**操作(市场调整=池内等权均值);单票时市场调整退化(市场=自身),只看原始收益。"""

    trigger: str = ""                       # >0 的触发表达式(DSL)/注册因子名
    horizons: Optional[List[int]] = None    # 前向窗口(交易日),默认 [1,5,10,20]
    direction: int = 0                      # 看多/看空解读(仅元数据,不改触发的 firing 判定)


def _factor_event(body: "FactorEventIn") -> JSONResponse:
    """事件研究:对股票池跑触发表达式,收集所有 firing 的 (date,code),算 CAR/命中率/t值/逐年/期望值。
    复用 tsic 的 universe→panel→inject_refs→compile 链 + 引擎 build_event_report(只 import 不改),
    节点层再补盈亏比/期望值。诚实失败 ok:False + reason(HTTP 200)。"""
    import pandas as pd  # noqa: F401
    from financial_analyst.factors.eval.report import forward_simple_returns

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    expr = (body.trigger or "").strip()
    if not expr:
        return _fail_factor(universe, "触发表达式/名称不能为空", method="event")
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)).isoformat()
    hz = sorted({int(h) for h in (getattr(body, "horizons", None) or [1, 5, 10, 20]) if int(h) >= 1})
    if not hz:
        hz = [1, 5, 10, 20]
    direction = int(getattr(body, "direction", 0) or 0)

    custom = _norm_codes(getattr(body, "codes", None))
    if custom:
        codes = custom
    else:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail_factor(universe, f"universe_error: {type(exc).__name__}: {exc}", method="event")
    if not codes:
        return _fail_factor(universe, f"empty_universe: '{universe}' 解析为空", method="event")
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:  # noqa: BLE001
            ind_loader = None
        panel = load_panel_cached(loader, codes, start, end, freq="day", industry_loader=ind_loader)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"load_error: {type(exc).__name__}: {exc}", method="event", start=start, end=end)

    panel, _ref_warn = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                           getattr(body, "leader", None), start, end, "day")
    # 触发表达式 → compute callable(注册名优先,否则编译 DSL)。
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from financial_analyst.factors.zoo.registry import get as _get_alpha
        try:
            compute = _get_alpha(expr).compute
        except KeyError:
            validate_expr(expr)
            compute = compile_factor(expr)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"{type(exc).__name__}: {exc}", method="event", start=start, end=end)

    # 引擎事件研究(零引擎改动:只 import 调用 build_event_report)。
    try:
        from financial_analyst.factors.eval.event import build_event_report
        from financial_analyst.factors.eval.config import EvalConfig
        cfg = EvalConfig(universe=universe, freq="day", start=start, end=end)
        rpt = build_event_report(panel, compute, cfg, factor_label=expr, horizons=tuple(hz))
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"event_error: {type(exc).__name__}: {exc}", method="event", start=start, end=end)
    if getattr(rpt, "status", "ok") != "ok":
        return _fail_factor(universe, f"{rpt.status}: {rpt.error or '事件研究无结果'}", method="event", start=start, end=end)

    # 节点层补盈亏比/期望值(EventReport 只给聚合,不给逐事件序列 → 这里重算一次 firing 事件,廉价)。
    pf_by_h: Dict[int, Dict[str, Any]] = {}
    try:
        sig = compute(panel).astype(float).dropna()
        ev_idx = sig[sig > 0].index
        for h in hz:
            fwd = forward_simple_returns(panel, h).reindex(ev_idx).dropna()
            if fwd.empty:
                continue
            wins, losses = fwd[fwd > 0], fwd[fwd < 0]
            wr = float((fwd > 0).mean())
            avg_w = float(wins.mean()) if len(wins) else 0.0
            avg_l = float(losses.mean()) if len(losses) else 0.0   # 负值
            lsum = float(losses.sum())
            pf = (float(wins.sum()) / abs(lsum)) if lsum != 0 else None
            pf_by_h[h] = {"profit_factor": (_num(pf) if pf is not None else None),
                          "expectancy": _num(wr * avg_w + (1.0 - wr) * avg_l)}
    except Exception:  # noqa: BLE001 — 盈亏比为辅助,失败不阻断主结果
        pf_by_h = {}

    horizons_out: List[Dict[str, Any]] = []
    for eh in rpt.horizons:
        d = {"h": int(eh.h), "n": int(eh.n),
             "mean_ret": _num(eh.mean_ret), "mean_excess": _num(eh.mean_excess),
             "win_rate": _num(eh.win_rate), "t_stat": _num(eh.t_stat)}
        d.update(pf_by_h.get(int(eh.h), {}))
        horizons_out.append(d)

    head_h = 5 if 5 in hz else hz[len(hz) // 2]
    head = next((d for d in horizons_out if d["h"] == head_h), horizons_out[0] if horizons_out else {})
    n_codes = panel.n_codes()
    warnings = list(_ref_warn or []) + list(rpt.warnings or [])  # rpt.warnings 已含 beta调整+NW 说明
    if n_codes <= 1 and "idx_ret" not in getattr(panel, "df").columns:
        warnings.append("单票且未设对标指数:市场=自身→异常收益退化(mean_excess≈0),"
                        "请看原始收益 mean_ret,或设 benchmark=沪深300 用真实大盘做市场模型调整。")

    out: Dict[str, Any] = {
        "ok": True, "universe": universe, "method": "event",
        "trigger": expr, "direction": direction, "horizons": horizons_out,
        "n_events": int(rpt.n_events), "event_rate": _num(rpt.event_rate),
        "n_codes": int(n_codes), "n_dates": int(rpt.n_dates),
        "car_curve": [[int(dd), _num(cc)] for dd, cc in rpt.car_curve],
        "by_year": [[str(yy), int(nn), _num(mm)] for yy, nn, mm in rpt.by_year],
        "summary": {"n_events": int(rpt.n_events), "event_rate": _num(rpt.event_rate),
                    "n_codes": int(n_codes), "head_h": int(head_h),
                    "head_excess": head.get("mean_excess"), "head_win_rate": head.get("win_rate"),
                    "head_t": head.get("t_stat"), "head_ret": head.get("mean_ret")},
        "warnings": warnings, "status": "ok",
        "_label": expr, "_universe": universe,
    }
    return JSONResponse(out)


class FactorRelstatIn(ModelTrainIn):
    """``POST /workflow/relstat`` 入参:**关系稳定度** —— 对一个"关系型"时序因子(共振相关/beta/
    跟随强度等,本身是每股一条时间序列)做描述性体检:均值水平、波动、lag-1 自相关(粘性)、正占比
    (符号持续)。这是 tsic(预测力)的**描述性补充**——回答"这只票的共振到底有多强、稳不稳",
    不是"能不能预测收益"。继承 codes/universe/start/end/benchmark/leader。"""

    expr_or_name: str = ""        # 关系因子表达式 / 注册名(如 correlation(returns,idx_ret,20))


def _factor_relstat(body: "FactorRelstatIn") -> JSONResponse:
    """关系稳定度:逐股算该关系因子时序的 均值/波动/lag1自相关/正占比。复用 tsic 的
    universe→panel→inject_refs→compile 链,不动引擎。诚实失败 ok:False + reason(HTTP 200)。"""
    import pandas as pd
    import numpy as np

    universe = (body.universe or "csi_fast").strip() or "csi_fast"
    expr = (body.expr_or_name or "").strip()
    if not expr:
        return _fail_factor(universe, "关系因子表达式/名称不能为空", method="relstat")
    end = (body.end or "").strip() or date.today().isoformat()
    start = (body.start or "").strip() or (
        date.today() - timedelta(days=365 * _MODEL_DEFAULT_YEARS)).isoformat()

    custom = _norm_codes(getattr(body, "codes", None))
    if custom:
        codes = custom
    else:
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return _fail_factor(universe, f"universe_error: {type(exc).__name__}: {exc}", method="relstat")
    if not codes:
        return _fail_factor(universe, f"empty_universe: '{universe}' 解析为空", method="relstat")
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:  # noqa: BLE001
            ind_loader = None
        panel = load_panel_cached(loader, codes, start, end, freq="day", industry_loader=ind_loader)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"load_error: {type(exc).__name__}: {exc}", method="relstat", start=start, end=end)

    panel, _ref_warn = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                           getattr(body, "leader", None), start, end, "day")
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from financial_analyst.factors.zoo.registry import get as _get_alpha
        try:
            fac = _get_alpha(expr).compute(panel)
        except KeyError:
            validate_expr(expr)
            fac = compile_factor(expr)(panel)
    except Exception as exc:  # noqa: BLE001
        return _fail_factor(universe, f"{type(exc).__name__}: {exc}", method="relstat", start=start, end=end)
    if not isinstance(fac, pd.Series):
        return _fail_factor(universe, "关系因子求值非 Series", method="relstat", start=start, end=end)

    fac_c = fac.index.get_level_values("code")
    rows: List[Dict[str, Any]] = []
    for code in [str(c) for c in panel.codes()]:
        s = fac[fac_c == code].dropna()
        s.index = s.index.get_level_values("datetime")
        s = s.sort_index()
        if len(s) < 20:
            rows.append({"code": code, "level": None, "n": int(len(s))})
            continue
        ac = s.autocorr(lag=1)
        rows.append({
            "code": code,
            "level": _num(float(s.mean())),
            "vol": _num(float(s.std(ddof=1))),
            "stickiness": _num(float(ac) if ac == ac else float("nan")),  # lag-1 自相关=粘性
            "pos_ratio": _num(float((s > 0).mean())),
            "n": int(len(s)),
        })
    valid = [r for r in rows if r.get("level") is not None]
    if not valid:
        return _fail_factor(universe, "无足够样本算关系稳定度(每股需≥20个点)", method="relstat", start=start, end=end)
    rows_sorted = sorted(rows, key=lambda r: (r.get("level") is None, -(r.get("level") or 0.0)))
    summary = {
        "n_codes": len(valid),
        "mean_level": _num(float(np.mean([r["level"] for r in valid]))),
        "mean_stickiness": _num(float(np.nanmean([r.get("stickiness") for r in valid if r.get("stickiness") is not None] or [float("nan")]))),
        "mean_pos_ratio": _num(float(np.mean([r["pos_ratio"] for r in valid]))),
        "avg_n": int(np.mean([r["n"] for r in valid])),
    }
    warnings = list(_ref_warn or [])
    warnings.append("关系稳定度=该关系因子时序的描述统计(均值水平/波动/lag1自相关粘性/正占比);"
                    "是 tsic 预测力的描述性补充,不衡量预测收益的能力。")
    out: Dict[str, Any] = {
        "ok": True, "universe": universe, "method": "relstat",
        "expr_or_name": expr, "codes_relstat": rows_sorted, "summary": summary,
        "warnings": warnings, "status": "ok", "_label": expr, "_universe": universe,
    }
    return JSONResponse(out)


class FactorRiskIn(BacktestVectorIn):
    """``POST /workflow/risk`` 入参:**风险度量**(VaR/CVaR/EVT/Kupiec)。继承回测全字段
    (因子/topn/加权/成本…),仅默认调仓频率改**周频**——VaR 需更密的组合收益样本才有意义
    (月频 1 年仅 ~12 期远不够);窗口默认拉到 **3 年**(在 ``_factor_risk`` 里补)。节点内复用
    ``_backtest_vector`` 建组合,取其 ``portfolio.risk`` 块重打包;零重复造组合逻辑、零改回测。"""

    rebalance: str = "week"        # 风险度量默认周频(~52/yr;月频 1 年仅 12 期不够)


def _factor_risk(body: "FactorRiskIn") -> JSONResponse:
    """风险度量节点:复用 ``_backtest_vector`` 建 TopN 组合 → 取其已算好的 ``portfolio.risk``
    块(VaR/CVaR/EVT/Kupiec)重打包成风险视图。窗口缺省拉到 3 年保收益样本足。诚实失败原样透传。"""
    import json as _json
    # 窗口默认 3 年(VaR 样本足);用户显式给 start 则尊重
    upd: Dict[str, Any] = {}
    if not (getattr(body, "start", "") or "").strip():
        upd["start"] = (date.today() - timedelta(days=365 * 3)).isoformat()
    bt_body = body.model_copy(update=upd) if upd else body
    resp = _backtest_vector(bt_body)          # FactorRiskIn 是 BacktestVectorIn 子类,直接复用
    try:
        data = _json.loads(resp.body)
    except Exception:  # noqa: BLE001
        return resp
    if not data.get("ok"):
        return resp                            # 诚实失败原样透传(已带 ok:False/reason)
    pf = data.get("portfolio") or {}
    risk = pf.get("risk") or {"enabled": False, "warnings": ["回测未产出风险块(组合为空或样本不足)"]}
    feats = data.get("feature_names") or []
    label = "风险[" + str(data.get("universe") or "") + "·top" + str(getattr(body, "topn", "") or 30) + "]"
    out: Dict[str, Any] = {
        "ok": True, "universe": data.get("universe"), "method": "risk",
        "risk": risk,
        "nav_series": pf.get("nav_series") or [],
        "bench300_nav": pf.get("bench300_nav") or [],
        "portfolio_kpi": data.get("portfolio_kpi") or {},
        "feature_names": feats,
        "warnings": (data.get("warnings") or []) + list(risk.get("warnings") or []),
        "status": "ok", "_label": label, "_universe": data.get("universe"),
    }
    return JSONResponse(out)


class FactorGarchIn(BacktestVectorIn):
    """``POST /workflow/garch`` 入参:**条件波动预测**(EWMA + GARCH(1,1))。继承回测全字段
    (因子/topn/加权/成本…),仅默认调仓频率改**周频**——GARCH 需更密的组合收益样本才稳健
    (月频 1 年仅 ~12 期);窗口默认拉到 **3 年**(在 ``_garch`` 里补)。节点内复用
    ``_backtest_vector`` 建组合,取其 ``portfolio.nav_series`` 算期收益 → 拟合条件波动模型;
    零重复造组合逻辑、零改回测。``horizon`` = 向前多步预测的步数(单位=调仓频率)。"""

    rebalance: str = "week"        # 条件波动默认周频(~52/yr;GARCH 需足够密+长样本)
    horizon: int = 12              # 多步预测步数(单位=调仓频率;周频 12≈一季度)


_GARCH_PPY = {"day": 252, "week": 52, "month": 12}


def _garch(body: "FactorGarchIn") -> JSONResponse:
    """条件波动预测节点:复用 ``_backtest_vector`` 建 TopN 组合 → 取其净值序列算期收益 →
    ``volmodel.fit_vol_models`` 拟合 EWMA + GARCH(1,1),出条件波动路径 + 多步向前预测。
    窗口缺省拉到 3 年保收益样本足。诚实失败原样透传 / 样本不足留 reason(HTTP 200)。"""
    import json as _json

    import numpy as _np

    from guanlan_v2.workflow.volmodel import fit_vol_models

    # 窗口默认 3 年(GARCH 样本足);用户显式给 start 则尊重
    upd: Dict[str, Any] = {}
    if not (getattr(body, "start", "") or "").strip():
        upd["start"] = (date.today() - timedelta(days=365 * 3)).isoformat()
    bt_body = body.model_copy(update=upd) if upd else body
    resp = _backtest_vector(bt_body)          # FactorGarchIn 是 BacktestVectorIn 子类,直接复用
    try:
        data = _json.loads(resp.body)
    except Exception:  # noqa: BLE001
        return resp
    if not data.get("ok"):
        return resp                            # 诚实失败原样透传(已带 ok:False/reason)

    pf = data.get("portfolio") or {}
    nav_series = pf.get("nav_series") or []
    rebal = (getattr(body, "rebalance", "") or "week")
    ppy = _GARCH_PPY.get(rebal, 52)
    horizon = int(getattr(body, "horizon", 12) or 12)
    label = "条件波动[" + str(data.get("universe") or "") + "·top" + str(getattr(body, "topn", "") or 30) + "]"

    def _honest(reason: str, n: int = 0) -> JSONResponse:
        return JSONResponse({
            "ok": True, "ok_model": False, "universe": data.get("universe"), "method": "garch",
            "garch": {"enabled": False}, "reason": reason, "n": n, "freq": rebal, "ppy": ppy,
            "warnings": (data.get("warnings") or []) + [reason],
            "status": "ok", "_label": label, "_universe": data.get("universe"),
        })

    # 净值 [date,nav] → 期收益(逐位算,保日期对齐;剔非有限)
    navv = _np.array([float(v) for _, v in nav_series], dtype=float)
    rdates = [str(d) for d, _ in nav_series]
    if navv.size < 2:
        return _honest("组合净值点不足,无法算收益序列")
    rets = navv[1:] / navv[:-1] - 1.0
    rdates = rdates[1:]
    fin = _np.isfinite(rets)
    rets = rets[fin]
    rdates = [d for d, keep in zip(rdates, fin.tolist()) if keep]

    vm = fit_vol_models(rets, periods_per_year=ppy, horizon=horizon)
    if not vm.get("ok"):
        return _honest(vm.get("reason") or "条件波动拟合失败", n=int(vm.get("n", 0)))

    g = vm["garch"]
    gvol = vm["garch_vol"]
    evol = vm["ewma_vol"]
    m = min(len(gvol), len(evol), len(rdates))
    vol_path = [[rdates[i], round(float(gvol[i]), 6), round(float(evol[i]), 6)] for i in range(m)]
    fc_vol = vm["forecast_vol"]
    fc_ann = vm["forecast_vol_annual"]
    forecast = [[i + 1, round(float(fc_vol[i]), 6), round(float(fc_ann[i]), 6)] for i in range(len(fc_vol))]

    out: Dict[str, Any] = {
        "ok": True, "ok_model": True, "universe": data.get("universe"), "method": "garch",
        "n": vm["n"], "freq": rebal, "ppy": ppy,
        "ewma_lambda": vm["ewma_lambda"], "horizon": vm["horizon"],
        "garch": {
            "omega": g["omega"], "alpha": g["alpha"], "beta": g["beta"],
            "persistence": g["persistence"], "converged": g["converged"], "loglik": g["loglik"],
            "uncond_vol": g["uncond_vol"], "uncond_vol_annual": g["uncond_vol_annual"],
        },
        "current_vol": vm["garch_vol_last"], "current_vol_annual": vm["garch_vol_annual_last"],
        "ewma_vol_last": vm["ewma_vol_last"], "ewma_vol_annual": vm["ewma_vol_annual"],
        "next_vol": round(float(fc_vol[0]), 6), "next_vol_annual": round(float(fc_ann[0]), 6),
        "vol_path": vol_path, "forecast": forecast,
        "feature_names": data.get("feature_names") or [],
        "warnings": data.get("warnings") or [],
        "status": "ok", "_label": label, "_universe": data.get("universe"),
    }
    return JSONResponse(out)


class FactorAttribIn(BacktestVectorIn):
    """``POST /workflow/attrib`` 入参:**风格归因**(Fama-French 风格因子回归)。继承回测全字段;
    默认调仓频率改 **月频**(风格因子按月读更干净,β 不被微结构噪声主导);窗口默认拉到 **3 年**。
    复用 ``_backtest_vector`` 建组合取净值算期收益(被解释变量),另载面板构建四风格因子收益(解释变量),
    OLS + Newey-West HAC 回归。零重复造组合逻辑、零改回测、不碰引擎。"""

    rebalance: str = "month"       # 风格归因默认月频(β 不被微结构噪声主导)


def _attrib(body: "FactorAttribIn") -> JSONResponse:
    """风格归因节点:复用 ``_backtest_vector`` 建 TopN 组合取净值期收益 → 对 市场/规模/价值/动量
    四风格因子收益做 OLS + Newey-West HAC,出因子暴露 β / alpha / R² / 各因子收益贡献。
    面板按 freq=day 与回测同 LRU 缓存键复用构建因子。窗口缺省 3 年保期数足。诚实失败原样透传 /
    样本不足留 reason(HTTP 200)。不碰引擎。"""
    import json as _json

    import pandas as _pd

    from financial_analyst.factors.eval.report import (
        _restrict, forward_simple_returns, rebalance_dates,
    )

    from guanlan_v2.workflow import attrib as _attrib_mod

    # 窗口默认 3 年(归因期数足);用户显式给 start 则尊重
    upd: Dict[str, Any] = {}
    if not (getattr(body, "start", "") or "").strip():
        upd["start"] = (date.today() - timedelta(days=365 * 3)).isoformat()
    bt_body = body.model_copy(update=upd) if upd else body

    resp = _backtest_vector(bt_body)          # FactorAttribIn 是 BacktestVectorIn 子类,直接复用
    try:
        data = _json.loads(resp.body)
    except Exception:  # noqa: BLE001
        return resp
    if not data.get("ok"):
        return resp                            # 诚实失败原样透传

    universe = data.get("universe") or (bt_body.universe or "csi_fast")
    rebal = (getattr(bt_body, "rebalance", "") or "month")
    ppy = _GARCH_PPY.get(rebal, 12)
    fwd_days = _FWD_BY_REBAL.get(rebal, 21)
    label = "风格归因[" + str(universe) + "·top" + str(getattr(body, "topn", "") or 30) + "]"

    pf = data.get("portfolio") or {}
    nav_series = pf.get("nav_series") or []

    def _honest(reason: str, n: int = 0) -> JSONResponse:
        return JSONResponse({
            "ok": True, "ok_model": False, "universe": universe, "method": "attrib",
            "attrib": {"enabled": False}, "reason": reason, "n": n, "freq": rebal, "ppy": ppy,
            "warnings": (data.get("warnings") or []) + [reason],
            "status": "ok", "_label": label, "_universe": universe,
        })

    if len(nav_series) < 2:
        return _honest("组合净值点不足,无法算策略期收益")
    navv = _pd.Series([float(v) for _, v in nav_series],
                      index=_pd.to_datetime([d for d, _ in nav_series]))
    strat_ret = navv.pct_change().dropna()
    if len(strat_ret) < 12:
        return _honest(f"策略期收益样本不足(n={len(strat_ret)};风格归因需 ≥12 期,请加宽窗口/更密调仓)", n=int(len(strat_ret)))

    # 载面板(freq=day,与 _backtest_vector 同 LRU 缓存键)→ 构建四风格因子收益
    try:
        features = _normalize_features(bt_body)  # type: ignore[arg-type]
        start = (bt_body.start or "").strip() or (date.today() - timedelta(days=365 * 3)).isoformat()
        end = (bt_body.end or "").strip() or date.today().isoformat()
        load_body = bt_body.model_copy(update={"freq": "day"})
        mat = _materialize_xy(load_body, universe, features, start, end)
        if isinstance(mat, JSONResponse):
            return _honest("面板物化失败,无法构建风格因子收益")
        panel = mat[0]
        reb = rebalance_dates(list(panel.dates()), rebal)
        fwd = forward_simple_returns(panel, fwd_days)
        fwd_r = _restrict(fwd, reb)
        factor_df = _attrib_mod.build_style_factor_returns(panel.df, reb, fwd_r, min_leg=8, q=0.3)
    except Exception as exc:  # noqa: BLE001
        return _honest(f"风格因子收益构建失败:{type(exc).__name__}: {exc}")

    res = _attrib_mod.attribute_returns(strat_ret, factor_df, predictive_reg=_predictive_reg,
                                        hac_lag=1, ppy=ppy, min_periods=12)
    if not res.get("ok_model"):
        return _honest(res.get("reason") or "风格归因拟合失败", n=int(res.get("n", 0)))

    warns = list(data.get("warnings") or [])
    if int(res.get("n", 0)) < 24:
        warns.append(f"归因期数仅 {res['n']}(<24):HAC t 值小样本偏乐观,显著性请谨慎看。")
    if res.get("dropped_factors"):
        warns.append(f"风格因子 {'/'.join(res['dropped_factors'])} 因数据不足整列丢弃(如动量列缺/股票池腿不够)。")
    warns.append("alpha = 风格无法解释的超额(A股无干净无风险利率,未减 rf,非严格 CAPM α);风格因子在所选股票池内构建,大盘票池 SMB 偏弱。")

    out: Dict[str, Any] = {
        "ok": True, "ok_model": True, "universe": universe, "method": "attrib",
        "n": res["n"], "freq": rebal, "ppy": ppy,
        "alpha": res["alpha"], "alpha_annual": res["alpha_annual"],
        "alpha_t": res["alpha_t"], "alpha_sig": res["alpha_sig"],
        "r2": res["r2"], "nw_lag": res["nw_lag"],
        "exposures": res["exposures"],
        "strategy_mean": res["strategy_mean"], "strategy_mean_annual": res["strategy_mean_annual"],
        "residual_mean": res["residual_mean"],
        "factor_returns": res["factor_returns"], "factor_cols": res["factor_cols"],
        "feature_names": data.get("feature_names") or [],
        "warnings": warns,
        "status": "ok", "_label": label, "_universe": universe,
    }
    return JSONResponse(out)


class FactorTVBetaIn(BacktestVectorIn):
    """``POST /workflow/tvbeta`` 入参:**时变β(Kalman)**(策略市场β 随时间演化)。继承回测全字段;
    默认调仓频率改 **周频**(β 路径点更密、看得出漂移),窗口默认拉到 **3 年**。复用 ``_backtest_vector``
    建组合取净值期收益(被解释变量 r_t),另载面板取市场期收益(全池等权,= ``attrib`` 的 MKT)做
    时变参数回归 r_t=α_t+β_t·m_t(系数随机游走,Kalman 滤波+RTS 平滑)。零重复造组合、零改回测、不碰引擎。"""

    rebalance: str = "week"        # 时变β默认周频(路径点更密,看得出β漂移)


def _tvbeta(body: "FactorTVBetaIn") -> JSONResponse:
    """时变β节点:复用 ``_backtest_vector`` 建 TopN 组合取净值期收益 → 对市场期收益(全池等权,与
    ``attrib`` 的 MKT 同口径,无前视)做时变参数回归 r_t=α_t+β_t·m_t(Kalman 滤波因果 + RTS 平滑
    全样本),出 β(t) 路径 + 静态β对照 + summary。面板按 freq=day 与回测同 LRU 缓存键复用。窗口缺省
    3 年保路径点足。诚实失败原样透传 / 样本不足留 reason(HTTP 200)。不碰引擎。"""
    import json as _json

    import pandas as _pd

    from financial_analyst.factors.eval.report import (
        _restrict, forward_simple_returns, rebalance_dates,
    )

    from guanlan_v2.workflow import attrib as _attrib_mod
    from guanlan_v2.workflow import tvbeta as _tvbeta_mod

    # 窗口默认 3 年(β 路径点足);用户显式给 start 则尊重
    upd: Dict[str, Any] = {}
    if not (getattr(body, "start", "") or "").strip():
        upd["start"] = (date.today() - timedelta(days=365 * 3)).isoformat()
    bt_body = body.model_copy(update=upd) if upd else body

    resp = _backtest_vector(bt_body)          # FactorTVBetaIn 是 BacktestVectorIn 子类,直接复用
    try:
        data = _json.loads(resp.body)
    except Exception:  # noqa: BLE001
        return resp
    if not data.get("ok"):
        return resp                            # 诚实失败原样透传

    universe = data.get("universe") or (bt_body.universe or "csi_fast")
    rebal = (getattr(bt_body, "rebalance", "") or "week")
    ppy = _GARCH_PPY.get(rebal, 52)
    fwd_days = _FWD_BY_REBAL.get(rebal, 5)
    label = "时变β[" + str(universe) + "·top" + str(getattr(body, "topn", "") or 30) + "]"

    pf = data.get("portfolio") or {}
    nav_series = pf.get("nav_series") or []

    def _honest(reason: str, n: int = 0) -> JSONResponse:
        return JSONResponse({
            "ok": True, "ok_model": False, "universe": universe, "method": "tvbeta",
            "tvbeta": {"enabled": False}, "reason": reason, "n": n, "freq": rebal, "ppy": ppy,
            "warnings": (data.get("warnings") or []) + [reason],
            "status": "ok", "_label": label, "_universe": universe,
        })

    if len(nav_series) < 2:
        return _honest("组合净值点不足,无法算策略期收益")
    try:   # nav 点含 None/非数值 → 诚实 200 降级,不抛 500(契约)
        navv = _pd.Series([float(v) for _, v in nav_series],
                          index=_pd.to_datetime([d for d, _ in nav_series]))
    except (TypeError, ValueError) as exc:
        return _honest(f"组合净值序列含非数值点,无法算策略期收益({type(exc).__name__})")
    strat_ret = navv.pct_change().dropna()
    if len(strat_ret) < 24:
        return _honest(f"策略期收益样本不足(n={len(strat_ret)};时变β需 ≥24 期,请加宽窗口/更密调仓)", n=int(len(strat_ret)))

    # 市场口径:两种都按 reb 调仓日的【前向】期收益对齐(= attrib 的 MKT 同向同索引,与 strat_ret 对齐)。
    #   ① 真 benchmark 指数(默认沪深300):组合对大盘的 β 随风格轮动真漂移,且是真投资标的。
    #   ② 回退全池等权(= attrib 的 MKT):等权子集对等权全池 β≈1 近常数(显形 warning)。
    # 先载面板拿 reb(freq=day 与回测同 LRU 缓存键);指数在 reb 日 ffill≤日期(PIT 无前视)取前向期收益。
    start = (bt_body.start or "").strip() or (date.today() - timedelta(days=365 * 3)).isoformat()
    end = (bt_body.end or "").strip() or date.today().isoformat()
    try:
        features = _normalize_features(bt_body)  # type: ignore[arg-type]
        load_body = bt_body.model_copy(update={"freq": "day"})
        mat = _materialize_xy(load_body, universe, features, start, end)
        if isinstance(mat, JSONResponse):
            return _honest("面板物化失败,无法构建市场期收益")
        panel = mat[0]
        reb = rebalance_dates(list(panel.dates()), rebal)
    except Exception as exc:  # noqa: BLE001
        return _honest(f"面板/调仓日构建失败:{type(exc).__name__}: {exc}")

    reb_idx = _pd.DatetimeIndex(_pd.to_datetime(list(reb)))
    market_ret = None
    market_src = None
    bench_warn = None
    bench_id = (getattr(bt_body, "benchmark", None) or "csi300")
    try:
        bench_daily, bench_warn = _benchmark_ret_series(bench_id, start, end)
    except Exception:  # noqa: BLE001
        bench_daily = None
    if bench_daily is not None and len(bench_daily) > 2:
        lvl = (1.0 + bench_daily.fillna(0.0)).cumprod()
        lvl_reb = (lvl.reindex(lvl.index.union(reb_idx)).sort_index().ffill().reindex(reb_idx))  # ffill≤日期
        bfwd = lvl_reb.pct_change().shift(-1).dropna()   # 前向:bfwd[d_t]=指数[d_t→d_{t+1}] 收益,索引 d_t(同 MKT)
        if len(bfwd) >= 24:
            market_ret = bfwd
            market_src = "沪深300" if str(bench_id) == "csi300" else str(bench_id)
    if market_ret is None:
        # 回退:全池等权(= attrib 的 MKT 口径)
        try:
            fwd = forward_simple_returns(panel, fwd_days)
            fwd_r = _restrict(fwd, reb)
            factor_df = _attrib_mod.build_style_factor_returns(panel.df, reb, fwd_r, min_leg=8, q=0.3)
            mk = factor_df["MKT"].dropna() if "MKT" in factor_df.columns else _pd.Series(dtype="float64")
            if len(mk) >= 24:
                market_ret = mk
                market_src = "全池等权代理(沪深300指数不可用)"
        except Exception as exc:  # noqa: BLE001
            return _honest(f"市场期收益构建失败:{type(exc).__name__}: {exc}")
    if market_ret is None or len(market_ret) < 24:
        return _honest("市场期收益样本不足(沪深300指数与全池等权代理均 <24 期)")

    res = _tvbeta_mod.time_varying_beta(strat_ret, market_ret, min_periods=24)
    if not res.get("ok_model"):
        return _honest(res.get("reason") or "时变β拟合失败", n=int(res.get("n", 0)))

    warns = list(data.get("warnings") or [])
    if int(res.get("n", 0)) < 40:
        warns.append(f"时变β路径点仅 {res['n']}(<40):路径估计偏粗,趋势看大不看小。")
    warns.append(f"市场 = {market_src}(策略对其做时变β回归);平滑 β(t) 用全样本信息(双边·非因果,看历史演化),滤波 β(t) 单边因果(无前视)。")
    if bench_warn and market_src and str(market_src).startswith("沪深300"):
        warns.append(str(bench_warn))
    if market_src and "全池等权" in str(market_src):
        warns.append("注:回退口径下市场=股票池全池等权,等权 TopN 子集对它的 β 近常数(≈1),漂移看不出属正常;设 benchmark=沪深300 可看对真大盘的择时β。")
    warns.append("β 路径平滑度由浓缩似然在信噪比 qr 上自动 MLE 选(qr 越大越允许 β 游走);静态β=全样本 OLS 单值对照。")

    out: Dict[str, Any] = {
        "ok": True, "ok_model": True, "universe": universe, "method": "tvbeta",
        "n": res["n"], "freq": rebal, "ppy": ppy, "market": market_src,
        "static_beta": res["static_beta"], "static_alpha": res["static_alpha"], "r2": res["r2"],
        "qr": res["qr"], "rhat": res["rhat"],
        "beta_mean": res["beta_mean"], "beta_start": res["beta_start"], "beta_end": res["beta_end"],
        "beta_min": res["beta_min"], "beta_max": res["beta_max"], "beta_drift": res["beta_drift"],
        "alpha_mean": res["alpha_mean"],
        "beta_path": res["beta_path"], "alpha_path": res["alpha_path"],
        "feature_names": data.get("feature_names") or [],
        "warnings": warns,
        "status": "ok", "_label": label, "_universe": universe,
    }
    return JSONResponse(out)


# ════════════════════════════════════════════════════════════════════════════
# AI 自动搭图(POST /workflow/generate)—— LLM 把一句话目标编译成 graph JSON。
# grounding 真值源 = docs/workflow_api.md §1-§8(节点目录/dt 规则/DSL 白名单/配方)。
# 模块级 helper(_CATALOG / SYSTEM_PROMPT / _llm_complete / _parse_graph /
# validate_graph)纯函数 + 延迟 import,**不碰 engine**;端点诚实失败 {ok:false,reason}
# HTTP 200(对齐 _fail / _fail_model / _fail_factor),绝不抛 500。
# ════════════════════════════════════════════════════════════════════════════

# 节点目录(24 类)——逐字搬入调研 catalog_json,作 LLM grounding + validate_graph
# 唯一真值表。键序与 docs/workflow_api.md §2、workflow.jsx SPECS 一致;每类含
# cat(分组)/in([端口id, dt] 列表)/out([端口id, dt] 列表)/params(参数名列表)。
_CATALOG: Dict[str, Dict[str, Any]] = {
    "source":    {"cat": "io", "in": [],                                                  "out": [["data", "series"]],   "params": ["scope", "code", "codes", "universe", "benchmark", "leader"]},
    "formula":   {"cat": "io", "in": [],                                                  "out": [["out", "series"]],    "params": ["expr"]},
    "factorlib": {"cat": "io", "in": [],                                                  "out": [["out", "series"]],    "params": ["query", "name"]},
    "feature":   {"cat": "fe", "in": [["feat", "series"], ["label", "series"], ["src", "series"]], "out": [["fe", "fe"]], "params": ["tag"]},
    "xgb":       {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["trees", "depth", "lr", "sub"]},
    "lgbm":      {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["leaves", "lr"]},
    "svm":       {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["c"]},
    "rf":        {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["trees"]},
    "nn":        {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["hidden", "layers", "lr", "epochs", "alpha"]},
    "lstm":      {"cat": "ml", "in": [["fe", "fe"]],                                      "out": [["model", "model"]],   "params": ["seq_len", "hidden", "layers", "lr", "epochs"]},
    "pca":       {"cat": "mf", "in": [["fe", "fe"]],                                      "out": [["factor", "factor"]], "params": ["k"]},
    "spearman":  {"cat": "mf", "in": [["fe", "fe"]],                                      "out": [["factor", "factor"]], "params": []},
    "iccalc":    {"cat": "mf", "in": [["factor", "factor"]],                              "out": [["ic", "ic"]],         "params": ["period"]},
    "mf":        {"cat": "mf", "in": [["m1", "model"], ["f1", "fe"], ["m2", "model"], ["f2", "fe"]], "out": [["factor", "factor"]], "params": ["combine"]},
    "analysis":  {"cat": "fa", "in": [["factor", "factor"]],                              "out": [["report", "report"]], "params": ["rebal", "groups", "dir"]},
    "tsic":      {"cat": "fa", "in": [["factor", "series"], ["src", "series"]],            "out": [["tsic", "tsic"]],     "params": ["fwd_days"]},
    "event":     {"cat": "fa", "in": [["trigger", "series"], ["src", "series"]],           "out": [["event", "event"]],   "params": ["horizons", "direction"]},
    "relstat":   {"cat": "fa", "in": [["factor", "series"], ["src", "series"]],             "out": [["relstat", "relstat"]], "params": []},
    "backtest":  {"cat": "bt", "in": [["factor", "factor"], ["pf", "portfolio"]],         "out": [["result", "result"]], "params": ["cash", "topn", "combine", "weighting", "vol_forecast"]},
    "portfolio": {"cat": "bt", "in": [["factor", "factor"]],                              "out": [["pf", "portfolio"]],  "params": ["topn", "weighting", "max_weight", "industry_neutral", "vol_forecast"]},
    "risk":      {"cat": "bt", "in": [["factor", "series"]],                              "out": [["risk", "risk"]],     "params": ["topn"]},
    "garch":     {"cat": "bt", "in": [["factor", "series"]],                              "out": [["garch", "garch"]],   "params": ["topn", "horizon"]},
    "attrib":    {"cat": "bt", "in": [["factor", "series"]],                              "out": [["attrib", "attrib"]], "params": ["topn"]},
    "tvbeta":    {"cat": "bt", "in": [["factor", "series"]],                              "out": [["tvbeta", "tvbeta"]], "params": ["topn"]},
}

# 终端节点(产出 report/ic/result/portfolio/risk/garch/attrib/tvbeta → 结果抽屉);至少 1 个图才合法(docs §8.3)。
_TERMINAL_TYPES = {"analysis", "iccalc", "backtest", "portfolio", "tsic", "event", "relstat", "risk", "garch", "attrib", "tvbeta"}

# formula.expr 禁含的危险子串(受限 eval,docs §4)。
_DSL_FORBIDDEN = ("__", "import", "lambda")

# universe 枚举(docs §6 + source 允许 '自动' 按 code 推断)。
_UNIVERSE_OK = {
    "csi300_active", "csi_fast", "csi300_2024h2", "csi500", "csi800",
    "all", "etf", "sample30", "自动",
}


# ── LLM system prompt(grounding) ───────────────────────────────────────────
# 内嵌 docs/workflow_api.md §1-§8 浓缩:节点目录 + dt 铁律 + 终端要求 + model-via-mf +
# fe/series 来源 + DSL 白名单 + universe 枚举 + 3 配方模板 + 「只输出 JSON {nodes,edges}」。
SYSTEM_PROMPT = """你是「观澜」A 股量化研究工作台的工作流编排器。把用户一句话目标**编译成一张可运行的节点图(graph JSON)**,供用户在画布上审阅、调参、再手动运行。你**只选目录里的节点、按 dt 规则连边、按白名单写表达式**;绝不执行任何代码。

【输出契约 · 最重要】
只输出**一个 JSON 对象** `{"nodes":[...], "edges":[...]}`。不要任何解释、前后缀文字、Markdown 代码块(不要 ```json 围栏)。
- 每个 node 必含:`id`(图内唯一字符串,如 "n1")、`type`(下方 24 类之一)、`x`(整数像素)、`y`(整数像素)、`params`(对象,可空 {})。
- 每条 edge:`{"from":[源节点id, 源输出端口id], "to":[目标节点id, 目标输入端口id]}`。
- 布局:链从左到右 x 每级 +280;y 取 120-500;并行支路用不同 y。坐标只影响观感不影响运行。

【节点目录 · 只能从这 24 类里选】格式 = type | 入端口:dt | 出端口:dt | 参数
- source    | (无入口) | data:series | scope,code,universe — 设 universe/股票池;**其 data 口必须连到某个 feature 的 src 口**,否则股票池/样本外占比不下传(会退回默认池)。图里放一个即可
- formula   | (无入口) | out:series | expr — **造因子主入口**,expr 写一条 DSL 表达式
- factorlib | (无入口) | out:series | query,name — 选一个已注册因子
- feature   | feat:series, label:series(可选), src:series(接 source.data,下传股票池) | fe:fe | tag — 把上游公式物化成真 X/y;fe 块是 ML/PCA 入口。**tag 只能留空或写 "IC"/"fwd_ret"(展示标注,默认标签=未来收益);写任何其他文本会被当作 label DSL 表达式执行(如 tag:"ML" 会炸 ML 训练)**;自定义预测标签用 formula 连 label 口,绝不写进 tag
- xgb       | fe:fe | model:model | trees,depth,lr,sub
- lgbm      | fe:fe | model:model | leaves,lr
- svm       | fe:fe | model:model | c
- rf        | fe:fe | model:model | trees
- nn        | fe:fe | model:model | hidden,layers,lr,epochs,alpha
- lstm      | fe:fe | model:model | seq_len,hidden,layers,lr,epochs
- pca       | fe:fe | factor:factor | k
- spearman  | fe:fe | factor:factor | (无参)
- iccalc    | factor:factor | ic:ic(终端) | period
- mf        | m1:model, f1:fe, m2:model, f2:fe | factor:factor | combine(equal/ic/icir) — 两用桥:① model→factor 的唯一桥(ML);② **多因子合成**:图里放≥2 个 formula(各写一条因子表达式),并把其中一条经 feature 接到 mf.f1,mf 会把图内所有 formula 表达式按 combine 合成(ic/icir=按样本内 rank-IC/ICIR 加权,负腿自动反向;equal=等权)。combine 默认 equal,多因子合成优先用 icir
- analysis  | factor:factor | report:report(终端) | rebal,groups,dir — **截面IC**(一篮子股票横比),需 universe;单票会退化别用
- tsic      | factor:series(接 formula 因子), src:series(接 source.data) | tsic:tsic(终端) | fwd_days — **个股/单票时序IC**:每股因子值 vs 自身未来收益的相关。单票/小池(scope=个股/自选)选它,**不要套 rank**(用时序/共振/财务因子)
- event     | trigger:series(接 formula 触发式), src:series(接 source.data) | event:event(终端) | horizons,direction — **事件研究**:把 >0 触发式当离散事件,统计触发后 CAR/命中率/t值/逐年/盈亏比。反弹/异动/放量/跳空/消息面等**稀疏布尔触发**选它(截面IC对布尔信号是错口径);**跑股票池**(市场调整=池内均值,设 benchmark 用真大盘),单票需设 benchmark 才不退化
- relstat   | factor:series(接 formula 关系因子), src:series(接 source.data) | relstat:relstat(终端) | (无参) — **关系稳定度**:共振/跟随等关系因子的描述体检(均值水平/波动/lag1自相关粘性/正占比),是 tsic 预测力的描述补充。想"看这关系强不强、稳不稳"(而非预测收益)用它
- backtest  | factor:factor(或 pf:portfolio) | result:result(终端) | cash,topn,weighting,vol_forecast — weighting=持仓定权(equal/mktcap/inv_vol/risk_parity/min_var/max_sharpe/true_risk_parity);vol_forecast=波动口径(hist 历史[默认]/ewma/garch 预测),garch 让 inv_vol/min_var 等用「预测波动」定权(回测较慢)
- portfolio | factor:factor | pf:portfolio(终端) | topn,weighting,max_weight,industry_neutral,vol_forecast — 出最新期目标持仓权重,可再连「回测」的 pf 口;vol_forecast 同回测(hist/ewma/garch)
- risk      | factor:series(接 formula 因子) | risk:risk(终端) | topn — **风险度量**:建 TopN 多头组合算其损失分布尾部风险:VaR/CVaR(历史·参数·蒙特卡罗三法)+ EVT(POT+GPD 极值尾部估超高分位罕见巨亏)+ Kupiec VaR 回测。默认周频+3年窗(VaR 需更密更长收益样本)。想"这组合最大潜在亏损/尾部风险多大"用它
- garch     | factor:series(接 formula 因子) | garch:garch(终端) | topn,horizon — **条件波动预测**:建 TopN 多头组合算其净值期收益 → 拟合 EWMA + GARCH(1,1) 出**条件波动路径**(捕捉波动聚集)+ 向前 horizon 步**波动预测**(均值回复到无条件波动)。默认周频+3年窗。想"这组合下一步波动会放大还是收敛/预测未来波动"用它(与 risk 互补:risk 看尾部巨亏,garch 看波动随时间的演化与预测)
- attrib    | factor:series(接 formula 因子) | attrib:attrib(终端) | topn — **风格归因**:建 TopN 多头组合算其期收益 → 对四个风格因子收益(市场 MKT / 规模 SMB 小盘减大盘 / 价值 HML 高BM减低BM / 动量 WML 赢家减输家)做 OLS + Newey-West HAC 回归,出**因子暴露 β**(各风格载荷与显著性)+ **alpha**(风格无法解释的超额)+ **R²**(风格解释力)+ 各因子**收益贡献**(β×因子均值)。默认月频+3年窗。想"我这策略的收益来自小盘/价值/动量哪种风格、有没有真 alpha"用它(与 risk/garch 同样吃 series 直连)
- tvbeta    | factor:series(接 formula 因子) | tvbeta:tvbeta(终端) | topn — **时变β(Kalman)**:建 TopN 多头组合算其期收益 → 对市场期收益(全池等权,= attrib 的 MKT)做**时变参数回归** r_t=α_t+β_t·m_t(系数随机游走,Kalman 滤波因果 + RTS 平滑全样本,平滑度由浓缩似然自动 MLE 选)→ 出 **β(t) 演化路径**(滤波单边 + 平滑双边 + ±2se 置信带)+ 静态β对照 + β漂移幅度。默认周频+3年窗。想"我这策略对大盘的暴露随时间怎么漂(择时/风格切换)、现在是高β还是低β"用它(attrib 看静态β,tvbeta 看 β 随时间演化;同样吃 series 直连)

【连线铁律 dt】每条边 `from端口.dt` 必须 === `to端口.dt`(series→series、fe→fe、model→model、factor→factor)。跨 dt 的边非法。dt 产出/接收:
- series:source.data/formula.out/factorlib.out → feature.feat / feature.label / feature.src / tsic.factor / tsic.src / event.trigger / event.src / relstat.factor(formula 关系因子) / relstat.src / risk.factor(formula 因子;risk 像 backtest 但吃 series 直连) / garch.factor(formula 因子;garch 像 risk 吃 series 直连) / attrib.factor(formula 因子;attrib 像 risk 吃 series 直连) / tvbeta.factor(formula 因子;tvbeta 像 risk 吃 series 直连) / source.data 专接 feature.src / tsic.src / event.src / relstat.src 下传股票池/代码
- event(dt):仅 event.event 产出 → 终端;relstat(dt):仅 relstat.relstat 产出 → 终端;risk(dt):仅 risk.risk 产出 → 终端;garch(dt):仅 garch.garch 产出 → 终端;attrib(dt):仅 attrib.attrib 产出 → 终端;tvbeta(dt):仅 tvbeta.tvbeta 产出 → 终端;均不接下游
- fe:仅 feature.fe 产出 → xgb/lgbm/svm/rf/nn/lstm.fe、pca/spearman.fe、mf.f1、mf.f2
- model:仅 ML 节点.model 产出 → 只能进 mf.m1 / mf.m2
- factor:仅 pca/spearman/mf.factor 产出 → iccalc/analysis/backtest/portfolio.factor
- portfolio:仅 portfolio.pf 产出 → backtest.pf(可选,不连也能回测)

【硬约束 · 出图前自查】
1. 每个 node.type ∈ 上述 24 类;每个 node.id 全图唯一非空。
2. 每条边端口 id 准确,且 from.dt === to.dt。
3. **至少一个终端节点**(analysis / iccalc / backtest / portfolio / tsic / event / relstat / risk / garch / attrib / tvbeta),否则跑不出结果。
4. ML/PCA/Spearman 的入口是 fe → 上游**必须先有 feature 节点**(formula 不能直连模型/PCA)。
5. **model 要分析或回测必须先经 mf**(model 不能直连 factor 口):路径 model→mf.m1(或 m2)→ mf.factor → analysis/iccalc/backtest;且同时把对应 feature 接 mf.f1(或 f2),mf 才能消费 OOS 报告。
6. feature.feat 由 formula/factorlib(因子)喂;**feature.src 必须接 source.data**(下传股票池/样本外占比,缺则退回默认池 csi_fast);feature.label 可选。每张图必须有一个 source 且其 data 连到某 feature.src。**feature.params.tag 只能空/"IC"/"fwd_ret"**,其余文本会被当 label 表达式执行;自定义标签走 label 口。
7. 同一个(节点, 入端口)最多一条入边;**唯一例外:feature.feat 可接多条边**——多个 formula/factorlib 各连一条边到同一 feature 的 feat 口,聚合为多特征(保序去重)供 ML/PCA/Spearman 训练(多特征 ML 首选此法)。不要自连(from 节点 === to 节点)。
8. formula.expr 不得含 `__` / `import` / `lambda`。
9. 任一 node.params.universe 若给出,必 ∈ {csi300_active, csi_fast, csi300_2024h2, csi500, csi800, all, etf, sample30, 自动};省略则全图默认 csi_fast。

【因子表达式 DSL 白名单(formula.expr)】只用下列字段 + 算子,或一个已注册因子名:
字段(量价估值):close open high low volume vwap amount returns industry pe_ttm pb ps_ttm dv_ttm total_mv circ_mv turnover_rate;字段(技术·精算,缺则NaN):rsi_14(Wilder RSI) macd_signal(MACD信号线) amihud_20(Amihud非流动性) mom_20 mom_60 mom_120(动量);字段(财务,季频·公告日PIT对齐):roe(净资产收益率) roa(总资产收益率) net_margin(净利率) rev_yoy(营收同比) np_yoy(净利同比) debt_ratio(资产负债率) eps net_income revenue total_equity cfo(净利/营收/净资产/经营现金流,元,原始量);参照字段(选配,需数据源填「对标指数」/「龙头代码」才有):idx_ret(对标宽基指数日收益,如沪深300) ref_ret(龙头股日收益)
常用财务因子写法:高质量 `rank(roe)`;高成长 `rank(np_yoy)`;盈利收益率(价值)`rank(net_income/total_mv)`;账面市值比 BM `rank(total_equity/total_mv)`;低杠杆 `rank(-debt_ratio)`;现金含量 `rank(cfo/net_income)`(注:不要把「账面市值比」写成 -pe_ttm,二者不同)。
算子:rank scale indneutralize csmean(截面/篮子均值,配 correlation 做共振) indmean(x,industry)(所在行业均值,配 correlation 做行业共振) ts_mean ts_sum stddev ts_max ts_min ts_argmax ts_argmin ts_rank delta delay product correlation covariance regbeta(y,x,n)=滚动β(cov/var,共振/跟随弹性) regresi(y,x,n)=回归残差 rsqr(y,x,n)=拟合优度R²(共振强度) sequence(x,n)=时间序号(配 regbeta 算趋势斜率) decay_linear wma sma signedpower power log sign abs max_pair min_pair filter_where cross,以及 + - * / ** 比较 ()
例:5 日反转 `rank(-delta(close,5))`;20 日动量 `rank(ts_sum(returns,20))`;量价背离 `-correlation(rank(close),rank(volume),10)`;低换手 `-rank(turnover_rate)`;均线突破 `cross(close,ts_mean(close,20))`;篮子共振 `correlation(returns,csmean(returns),20)`(配自选股池);大盘共振 `correlation(returns,idx_ret,20)`(数据源选「对标指数·沪深300」);行业共振 `correlation(returns,indmean(returns,industry),20)`;龙头共振 `correlation(returns,ref_ret,20)`(数据源填「龙头代码」)。

【配方模板(照搬结构,按目标替换 type/params)】
A · ML 预测因子 → 分析(目标如「反转因子+XGBoost 看报告 沪深300」):
{"nodes":[
 {"id":"s","type":"source","x":60,"y":250,"params":{"universe":"csi300_active"}},
 {"id":"f","type":"formula","x":60,"y":150,"params":{"expr":"rank(-delta(close,5))"}},
 {"id":"fe","type":"feature","x":342,"y":150,"params":{"tag":"IC"}},
 {"id":"m","type":"xgb","x":624,"y":150,"params":{"trees":120,"depth":3,"lr":0.08}},
 {"id":"mf","type":"mf","x":906,"y":190,"params":{}},
 {"id":"an","type":"analysis","x":1188,"y":210,"params":{"groups":10}}
],"edges":[
 {"from":["s","data"],"to":["fe","src"]},
 {"from":["f","out"],"to":["fe","feat"]},
 {"from":["fe","fe"],"to":["m","fe"]},
 {"from":["m","model"],"to":["mf","m1"]},
 {"from":["fe","fe"],"to":["mf","f1"]},
 {"from":["mf","factor"],"to":["an","factor"]}
]}
B · LSTM 序列 → 回测(把 A 的 xgb 换 lstm(params {seq_len:10,hidden:32,layers:1,epochs:40}),末端 analysis 换 backtest(params {topn:30,cash:1000000}),入口仍是 factor;别忘 source.data→feature.src)。
C · 纯因子 → 分析(无 ML,目标如「换手率因子做 IC 体检」):formula(`-rank(turnover_rate)`) → feature → spearman → analysis(单特征 pca/spearman 退化为该因子自身报告;source.data→feature.src)。
D · **多因子合成**(目标如「ROE质量+账面市值比+净利同比 ICIR 合成 沪深300」):
{"nodes":[
 {"id":"s","type":"source","x":60,"y":300,"params":{"universe":"csi300_active"}},
 {"id":"f1","type":"formula","x":60,"y":120,"params":{"expr":"rank(roe)"}},
 {"id":"f2","type":"formula","x":60,"y":210,"params":{"expr":"rank(total_equity/total_mv)"}},
 {"id":"f3","type":"formula","x":60,"y":390,"params":{"expr":"rank(np_yoy)"}},
 {"id":"fe","type":"feature","x":342,"y":120,"params":{"tag":"IC"}},
 {"id":"mf","type":"mf","x":624,"y":200,"params":{"combine":"icir"}},
 {"id":"an","type":"analysis","x":906,"y":200,"params":{"rebal":"month","groups":10}}
],"edges":[
 {"from":["s","data"],"to":["fe","src"]},
 {"from":["f1","out"],"to":["fe","feat"]},
 {"from":["fe","fe"],"to":["mf","f1"]},
 {"from":["mf","factor"],"to":["an","factor"]}
]}
(要点:N 个 formula 各写一条因子;只需把其中一条经 feature 接进 mf.f1,mf 会把图内**所有** formula 按 combine=icir 合成;f2/f3 不必再连边。末端 analysis 看报告 / 换 backtest 回测。)
E · **个股/单票时序IC**(目标如「立昂微 大盘共振 时序IC」「单只票 605358 测动量时序IC」):单票没有截面 → 用 tsic,不用 analysis、不套 rank。
{"nodes":[
 {"id":"s","type":"source","x":60,"y":210,"params":{"scope":"自选","codes":"605358","benchmark":"csi300"}},
 {"id":"f","type":"formula","x":60,"y":110,"params":{"expr":"correlation(returns,idx_ret,20)"}},
 {"id":"t","type":"tsic","x":342,"y":160,"params":{"fwd_days":20}}
],"edges":[
 {"from":["s","data"],"to":["t","src"]},
 {"from":["f","out"],"to":["t","factor"]}
]}
(单票/小池用 时序因子(ts_sum(returns,n)/stddev/correlation(close,volume,n))、共振(correlation(returns,idx_ret,20),记得 source.benchmark=csi300)、财务(roe/np_yoy);绝不用 rank/截面 z。)
F · **离散触发 → 事件研究**(目标如「连跌反弹 事件研究 沪深300」「放量异动后怎么走」):触发式 >0 即 firing,用 event,**不用 analysis/rank**,跑股票池(别用单票)。
{"nodes":[
 {"id":"s","type":"source","x":60,"y":210,"params":{"universe":"csi300_active"}},
 {"id":"f","type":"formula","x":60,"y":110,"params":{"expr":"(returns>0)*(delay(ts_sum((returns<0)*1.0,4),1)>=4)"}},
 {"id":"ev","type":"event","x":342,"y":160,"params":{"horizons":[1,5,10,20]}}
],"edges":[
 {"from":["s","data"],"to":["ev","src"]},
 {"from":["f","out"],"to":["ev","trigger"]}
]}
(触发式举例:连跌反弹 `(returns>0)*(delay(ts_sum((returns<0)*1.0,4),1)>=4)`;距均线反弹 `cross(close,sma(close,5))*(((delay(close,1)-sma(close,20))/sma(close,20))<=-0.08)`;3σ异动 `abs((returns-ts_mean(returns,60))/stddev(returns,60))>3`;放量 `(volume/ts_mean(volume,20)>3)*(abs(returns)>0.03)`;跳空 `(open/delay(close,1)-1)>0.05`。)
G · **情绪/连续单票因子 → 时序IC**(目标如「换手突增 情绪 时序IC」「立昂微 振幅强度」):连续值用 tsic,**别套 rank**。
{"nodes":[
 {"id":"s","type":"source","x":60,"y":210,"params":{"scope":"自选","codes":"605358"}},
 {"id":"f","type":"formula","x":60,"y":110,"params":{"expr":"turnover_rate/ts_mean(turnover_rate,20)"}},
 {"id":"t","type":"tsic","x":342,"y":160,"params":{"fwd_days":10}}
],"edges":[
 {"from":["s","data"],"to":["t","src"]},
 {"from":["f","out"],"to":["t","factor"]}
]}
(情绪连续举例:换手突增 `turnover_rate/ts_mean(turnover_rate,20)`;振幅强度 `((high-low)/delay(close,1))/ts_mean((high-low)/delay(close,1),20)`;量价情绪 `sign(delta(close,1))*(turnover_rate/ts_mean(turnover_rate,20))`。)
H · **共振/跟随 → 时序IC**(目标如「立昂微 大盘共振β 时序IC」「行业共振」「跟随大盘强度」):连续关系因子用 tsic,**必须 source 设 benchmark=csi300(才有 idx_ret)或 leader=龙头代码(才有 ref_ret)**,别套 rank。
{"nodes":[
 {"id":"s","type":"source","x":60,"y":210,"params":{"scope":"自选","codes":"605358","benchmark":"csi300"}},
 {"id":"f","type":"formula","x":60,"y":110,"params":{"expr":"regbeta(returns,idx_ret,60)"}},
 {"id":"t","type":"tsic","x":342,"y":160,"params":{"fwd_days":10}}
],"edges":[{"from":["s","data"],"to":["t","src"]},{"from":["f","out"],"to":["t","factor"]}]}
(共振举例:市场相关 `correlation(returns,idx_ret,20)`、市场β `regbeta(returns,idx_ret,60)`、共振强度 `rsqr(returns,idx_ret,60)`、共振变化 `delta(correlation(returns,idx_ret,20),5)`、行业共振 `correlation(returns,indmean(returns,industry),20)`、龙头共振 `correlation(returns,ref_ret,20)`(需 source.leader);跟随:`correlation(returns,delay(idx_ret,1),20)`、方向 `correlation(returns,delay(idx_ret,1),20)-correlation(delay(returns,1),idx_ret,20)`。**idx_ret 需 source.benchmark=csi300、ref_ret 需 source.leader**。)

再次强调:**只输出一个 JSON 对象 {nodes,edges},不要解释、不要围栏。**"""


async def _llm_complete(system: str, user: str, temperature: float = 0.2) -> str:
    """LLM 单次补全(延迟 import,engine 只 import 不改)。镜像 cards/refine.py:74-79。

    运行时 server.py 强制 FA_CONFIG_DIR→仓内 config/llm.yaml(default_provider=deepseek/
    deepseek-chat,agent_overrides={}),故 for_agent("workflow") 解析为 deepseek/deepseek-chat。
    deepseek 走 OpenAI 兼容 response_format={"type":"json_object"} 强约束出 JSON。
    网络/key/超时异常照常 raise,由端点 try/except 包成 {ok:false,reason} HTTP 200。
    """
    from financial_analyst.llm.client import LLMClient  # 延迟 import,engine 只 import 不改
    client = LLMClient.for_agent("workflow")
    resp = await client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    return (resp["choices"][0]["message"]["content"]) or ""


def _parse_graph(text: str) -> Dict[str, Any]:
    """LLM 文本 → graph dict。复用 refine.py:54-65 剥围栏法:去 ```json``` 围栏、从环绕
    文字里抠最外层 {...} → json.loads。解析失败 raise(由端点包成诚实失败)。"""
    import json

    s = str(text or "").strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        s = parts[1] if len(parts) > 1 else s
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip().rstrip("`").strip()
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        s = s[i:j + 1]
    return json.loads(s)


def _autowire_source(graph) -> None:
    """P3 自愈:LLM 偶尔漏连 source.data→feature.src(股票池/样本外占比脐带)。若图里有 source +
    feature 节点、但无任何连到 feature.src 的边,则就地补一条 source.data→第一个 feature.src,
    保证用户选的股票池真下传(不再静默退回默认池)。LLM 已连则不动。"""
    try:
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        src = next((n for n in nodes if n.get("type") == "source"), None)
        sinks = [n for n in nodes if n.get("type") in ("feature", "tsic", "event", "relstat")]   # 都有 src 口
        if not src or not sinks:
            return
        wired = any(isinstance(e.get("to"), list) and len(e["to"]) >= 2 and e["to"][1] == "src"
                    for e in edges)
        if wired:
            return
        edges.append({"from": [src.get("id"), "data"], "to": [sinks[0].get("id"), "src"]})
        graph["edges"] = edges
    except Exception:  # noqa: BLE001 — 自愈失败不阻断,原图照返
        pass


def validate_graph(graph: Any) -> "tuple[bool, List[str]]":
    """服务端权威校验(纯函数,无 engine 依赖)→ (ok, errors)。对齐 docs §8 + validation_rules。

    逐条核:type∈目录 / id 唯一非空 / edge 结构 / 无自环 / 端口存在 + dt 匹配 / 单入边 /
    ≥1 终端 / model 只接 mf.m1·m2 / fe 口只由 feature.fe 供 / series 口只由 series 产出者供 /
    formula 表达式禁词 / universe 枚举。返回去重 errors;空列表即合法。
    """
    errors: List[str] = []

    # 1. 顶层结构 —— 必须 dict 且含 nodes(list)/edges(list)。
    if not isinstance(graph, dict):
        return False, ["图必须是含 nodes/edges 的 JSON 对象"]
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return False, ["图必须含 nodes/edges 列表"]

    # 2/3. 逐 node 校验 type、id;建 id→type map。
    id_to_type: Dict[str, str] = {}
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"第 {idx} 个节点不是对象")
            continue
        nid = node.get("id")
        ntype = node.get("type")
        if not isinstance(nid, str) or not nid.strip():
            errors.append(f"第 {idx} 个节点 id 为空或非字符串")
        elif nid in id_to_type:
            errors.append(f"节点 id '{nid}' 重复")
        if not isinstance(ntype, str) or ntype not in _CATALOG:
            errors.append(f"节点 {nid if isinstance(nid, str) else idx} type '{ntype}' 不在目录")
        if isinstance(nid, str) and nid.strip() and nid not in id_to_type:
            id_to_type[nid] = ntype if isinstance(ntype, str) else ""

    # 端口字典缓存:type → {portId: dt}(out / in 各一份)。
    def _out_map(t: str) -> Dict[str, str]:
        return {p: d for p, d in _CATALOG.get(t, {}).get("out", [])}

    def _in_map(t: str) -> Dict[str, str]:
        return {p: d for p, d in _CATALOG.get(t, {}).get("in", [])}

    seen_in_ports: set = set()  # (dstId, dstPort) 去重 → 单入边
    has_terminal = any(
        isinstance(n, dict) and n.get("type") in _TERMINAL_TYPES for n in nodes
    )

    # 4-11. 逐 edge 校验。
    for eidx, edge in enumerate(edges):
        if not isinstance(edge, dict):
            errors.append(f"第 {eidx} 条边不是对象")
            continue
        frm = edge.get("from")
        to = edge.get("to")
        if (not isinstance(frm, (list, tuple)) or len(frm) != 2
                or not isinstance(to, (list, tuple)) or len(to) != 2):
            errors.append(f"第 {eidx} 条边的 from/to 必须是 [节点id, 端口id]")
            continue
        src_id, src_port = frm[0], frm[1]
        dst_id, dst_port = to[0], to[1]

        # 4. 端点 id 必须存在于 id-map。
        if src_id not in id_to_type or dst_id not in id_to_type:
            errors.append(f"边 {src_id}->{dst_id} 引用了不存在的节点")
            continue

        # 5. no-self-loop(对齐 workflow.jsx:596)。
        if src_id == dst_id:
            errors.append(f"边 {src_id} 自连(from/to 同一节点)非法")
            continue

        src_type = id_to_type[src_id]
        dst_type = id_to_type[dst_id]
        out_ports = _out_map(src_type)
        in_ports = _in_map(dst_type)

        # 6. 端口存在 + dt 匹配。
        if src_port not in out_ports:
            errors.append(f"端口 {src_port} 不是 {src_type} 的合法出口")
            continue
        if dst_port not in in_ports:
            errors.append(f"端口 {dst_port} 不是 {dst_type} 的合法入口")
            continue
        out_dt = out_ports[src_port]
        in_dt = in_ports[dst_port]
        if out_dt != in_dt:
            errors.append(
                f"边 {src_id}.{src_port}({out_dt}) → {dst_id}.{dst_port}({in_dt}) dt 不匹配"
            )
            # dt 不匹配则跳过后续语义校验(已是确定性错误)。
            continue

        # 7. single-in-edge(对齐 workflow.jsx 单入口);例外:feature.feat 多边聚合为多特征
        #    (执行器 run_graph 保序去重,镜像 deriveRecipeForNode)。
        key = (dst_id, dst_port)
        if key in seen_in_ports:
            if not (dst_type == "feature" and dst_port == "feat"):
                errors.append(f"入端口 {dst_id}.{dst_port} 被多条边指向(单入口)")
        else:
            seen_in_ports.add(key)

        # 9. model-via-mf:model 出边只能进 mf.m1/m2。
        if out_dt == "model" and not (dst_type == "mf" and dst_port in ("m1", "m2")):
            errors.append(f"边 {src_id}.{src_port} 的 model 只能接 mf.m1/m2")

        # 10. fe 来源:fe 入口只能由 feature.fe 供给。
        if in_dt == "fe" and not (src_type == "feature" and src_port == "fe"):
            errors.append(f"入端口 {dst_id}.{dst_port} 的 fe 只能由 feature.fe 供给")

        # 11. series 来源:series 入口只能由 series 产出者供给。
        if in_dt == "series" and src_type not in ("source", "formula", "factorlib"):
            errors.append(
                f"入端口 {dst_id}.{dst_port} 的 series 只能由 source/formula/factorlib 供给"
            )

    # 8. terminal>=1。
    if not has_terminal:
        errors.append("缺终端节点(analysis/iccalc/backtest)")

    # 12/13. 逐 node 的表达式禁词 + universe 枚举。
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        params = node.get("params")
        if not isinstance(params, dict):
            params = {}
        nid = node.get("id")
        # 12. formula.expr 禁含 __/import/lambda。
        if ntype == "formula":
            expr = params.get("expr")
            expr = str(expr or "")
            if any(bad in expr for bad in _DSL_FORBIDDEN):
                errors.append(f"节点 {nid} 表达式禁含 __/import/lambda")
        # 13. universe 枚举。
        uni = params.get("universe")
        if uni is not None and str(uni).strip() and str(uni).strip() not in _UNIVERSE_OK:
            errors.append(f"节点 {nid} universe '{uni}' 不在白名单")

    # 去重(保序)。
    deduped: List[str] = []
    seen: set = set()
    for e in errors:
        if e not in seen:
            seen.add(e)
            deduped.append(e)
    return (len(deduped) == 0), deduped


class GenIn(BaseModel):
    """``POST /workflow/generate`` 入参:一句话目标 + 重试上限(钳 ≤2)。"""

    goal: str
    max_retries: int = 2


# ── W8b AI 闭环:评审-改进(/workflow/critique)──────────────────────────────
# rd-agent 式闭环的「critique」环:前端把 目标 + 当前图 + 真实回测指标 POST 来,LLM 据指标
# 诊断问题并产出**改进后的** graph(同 generate 契约);LLM 不可用/产图不合法 → 规则兜底
# (确定性:RankIC<0 取反方向、样本外塌缩换稳健因子、Sharpe 低改月频)。绝不抛 500。
_CRITIQUE_SYS = SYSTEM_PROMPT + """

【评审-改进模式(本次任务,覆盖上面的"只生成"语气)】
你现在是量化策略评审官。用户给:① 目标 ② 当前工作流 graph ③ 该工作流的**真实回测指标**。
请据指标诊断问题、产出**改进后的**工作流。常见诊断→改法:
- RankIC 为负 → 因子方向反了:把因子表达式整体取负(如 rank(...) → -(rank(...)))或「因子分析」节点 dir 设 "-1"(二选一,勿同时,否则抵消)。
- 样本外(OOS)塌缩 / 疑似过拟合 → 换更稳健因子(低波 rank(-stddev(returns,20)) / 动量 rank(ts_sum(returns,20)))或加大调仓周期。
- Sharpe / 年化偏低、换手过高 → 回测/分析改月频(rebalance/rebal: "month")降成本。
- 指标尚可 → 小幅微调或维持。
**输出一个 JSON 对象**:{"diagnosis":"一句话中文诊断+改法","nodes":[...],"edges":[...]};graph 契约同上(节点目录 / dt 端口规则 / DSL 白名单 / 终端要求)。除该 JSON 外不要任何解释或围栏。"""


def _rule_critique(goal: str, metrics: "Dict[str, Any]", graph: "Dict[str, Any]") -> "Dict[str, Any]":
    """规则兜底自评(LLM 不可用 / 产图不合法时):据真实指标确定性诊断 + 打补丁改进图。"""
    import copy
    g = copy.deepcopy(graph or {})
    nodes = g.get("nodes") if isinstance(g.get("nodes"), list) else []
    edges = g.get("edges") if isinstance(g.get("edges"), list) else []
    m = metrics or {}
    ric, shp, oosv = m.get("rank_ic"), m.get("sharpe"), m.get("oos_verdict")
    _ok = lambda x: isinstance(x, (int, float)) and x == x
    diag: "List[str]" = []
    changed = False
    if _ok(ric) and ric < 0:
        flipped = False
        for n in nodes:
            if n.get("type") == "analysis":
                n.setdefault("params", {})["dir"] = "-1"; flipped = True; changed = True
        if not flipped:
            for n in nodes:
                if n.get("type") == "formula":
                    e = str((n.get("params") or {}).get("expr") or "").strip()
                    if e:
                        n.setdefault("params", {})["expr"] = "-(" + e + ")"; changed = True
        diag.append(f"RankIC 为负({round(float(ric), 4)})→ 因子方向反了,已对因子取负")
    elif oosv in ("overfit", "degraded"):
        for n in nodes:
            if n.get("type") == "formula":
                n.setdefault("params", {})["expr"] = "rank(-stddev(returns,20))"; changed = True
        diag.append("样本外塌缩(疑似过拟合)→ 换更稳健的低波因子 rank(-stddev(returns,20))")
    elif _ok(shp) and shp < 0.3:
        for n in nodes:
            if n.get("type") == "backtest":
                n.setdefault("params", {})["rebalance"] = "month"; changed = True
            elif n.get("type") == "analysis":
                n.setdefault("params", {})["rebal"] = "month"; changed = True
        diag.append("收益偏弱 → 回测/分析改月频,降换手成本")
    if not changed:
        diag.append("指标尚可,未见明显问题,维持当前工作流")
    return {"ok": True, "diagnosis": ";".join(diag), "graph": {"nodes": nodes, "edges": edges}, "source": "rule"}


class CritiqueIn(BaseModel):
    """``POST /workflow/critique`` 入参:目标 + 当前图 + 该图真实回测指标。"""

    goal: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)   # {rank_ic,sharpe,ann_return,oos_verdict,n_dates,factor}
    graph: Dict[str, Any] = Field(default_factory=dict)     # {nodes,edges}
    constraints: str = ""    # 调用方求值环境约束(研究回路声明"求值只读表达式";空=prompt 逐字不变,画布/帷幄零行为变化)


class WorkflowSaveIn(BaseModel):
    """``POST /workflow/save`` 入参:工作流名 + 全图 ``{nodes, edges}``。"""

    name: str = ""
    graph: dict = Field(default_factory=dict)   # {nodes:[...], edges:[...]}


class WorkflowRunIn(BaseModel):
    """``POST /workflow/run`` 入参:服务端整图执行(P4 执行器门面;供 e2e 与 P5 复用)。"""

    graph: Dict[str, Any] = Field(default_factory=dict)
    universe: Optional[str] = None
    freq: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    oos_frac: Optional[float] = None


def build_workflow_router() -> APIRouter:
    """工作流自有节点路由组(无 prefix,P2-P5 共用)。随 cards/seats/factorlib 工厂式。"""
    router = APIRouter(tags=["workflow"])
    # 工作流服务端持久化 store(照 cards/factorlib 工厂头部 new 一次)。
    wf_store = WorkflowStore()

    @router.post("/feature/build")
    def feature_build(body: FeatureBuildIn):
        """物化特征工程(真 X/y)→ 真统计 + 可复算 fe spec。

        流程(全部引擎 primitive,函数体内延迟 import):
          1. 归一特征列表(空 → ``ok:False``)。
          2. ``resolve_universe_codes`` 解析股票池(空 → ``ok:False``)。
          3. ``get_default_loader`` + ``load_panel_cached`` 加载面板(失败 → ``ok:False``)。
          4. 逐特征 ``compile_factor`` 求值 → 截面 ``winsorize`` / ``zscore``;并列拼成 fe。
          5. 标签:``label`` 留空 / IC / fwd_ret → ``forward_simple_returns(fwd_days)``;
             否则 ``label`` 当 zoo 表达式编译求值(formula 标签)。
          6. ``ic_analysis`` 逐特征 vs 标签算 RankIC;统计 coverage/n_dates/n_codes。
          7. 物化后全 NaN → ``ok:False``;否则返回真统计 + 预览 + ``fe`` 块。

        失败一律 ``ok:False`` + ``reason``(HTTP 200),不抛 500。
        """
        import pandas as pd

        universe = (body.universe or "csi_fast").strip() or "csi_fast"

        # —— 1. 归一特征列表 ——
        features = _normalize_features(body)
        if not features:
            return _fail(universe, "因子不能为空")

        freq = (body.freq or "day").strip() or "day"
        fwd_days = int(body.fwd_days or 5)
        if fwd_days < 1:
            fwd_days = 1
        preview_rows = max(0, min(int(body.preview_rows or 0), _PREVIEW_CAP))

        # 标签分支判定:留空 / IC / fwd_ret 等 → 前向收益;否则按 zoo 表达式当 formula 标签。
        label_raw = (body.label or "").strip()
        use_fwd = label_raw.lower() in _LABEL_FWD_TOKENS
        label_kind = "forward_return" if use_fwd else "formula"
        label_desc = f"fwd_ret({fwd_days})" if use_fwd else label_raw
        spec_label = None if use_fwd else label_raw   # fe.label:formula 才放表达式串

        # —— 2. universe 解析(空 → 诚实失败)。自选股池 codes 给定则优先(#5)——
        custom = _norm_codes(getattr(body, "codes", None))
        if custom:
            codes = custom
        else:
            try:
                from financial_analyst.data.universe import resolve_universe_codes
                codes = resolve_universe_codes(universe)
            except Exception as exc:  # noqa: BLE001
                return _fail(universe, f"universe_error: {type(exc).__name__}: {exc}")
        if not codes:
            return _fail(universe, f"empty_universe: universe '{universe}' 解析为空")

        # —— 窗口缺省:end=今天, start=今天−2y(逐字对齐 report.py:183-184)——
        end = (body.end or "").strip() or date.today().isoformat()
        start = (body.start or "").strip() or (date.today() - timedelta(days=365 * 2)).isoformat()

        # —— 3. 面板加载(逐字同 report.py:186-200 链;失败 → 诚实失败)——
        try:
            from financial_analyst.data.loader_factory import get_default_loader
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            loader = get_default_loader()
            try:
                from financial_analyst.data.loaders.industry import (
                    IndustryLoader,
                    industry_map_path,
                )
                ind_loader = IndustryLoader() if industry_map_path().exists() else None
            except Exception:  # noqa: BLE001  —— 行业图缺失只是少 industry 列,不阻断
                ind_loader = None
            panel = load_panel_cached(loader, codes, start, end, freq=freq,
                                      industry_loader=ind_loader)
        except Exception as exc:  # noqa: BLE001
            return _fail(universe, f"load_error: {type(exc).__name__}: {exc}",
                         start=start, end=end)

        # B2:注入对标指数/龙头日收益(idx_ret/ref_ret)→ 公式可做个股 vs 大盘/龙头共振(给定才动)。
        panel, _ = _inject_market_refs(panel, getattr(body, "benchmark", None),
                                       getattr(body, "leader", None), start, end, freq)

        # —— 4. 逐特征 compile_factor 求值 + 截面预处理 ——
        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            from financial_analyst.factors.zoo.registry import get as _get_alpha
        except Exception as exc:  # noqa: BLE001
            return _fail(universe, f"engine_import_error: {type(exc).__name__}: {exc}",
                         start=start, end=end)
        try:
            from financial_analyst.factors.eval.preprocess import winsorize, zscore
        except Exception as exc:  # noqa: BLE001
            return _fail(universe, f"engine_import_error: {type(exc).__name__}: {exc}",
                         start=start, end=end)

        feat_series: List[pd.Series] = []
        feature_names: List[str] = []
        for i, expr in enumerate(features):
            name = f"f{i + 1}"
            try:
                # 注册名优先(借引擎 zoo registry),否则当 zoo-DSL 表达式编译(同 report.py:204-209)。
                try:
                    s = _get_alpha(expr).compute(panel)
                except KeyError:
                    validate_expr(expr)
                    s = compile_factor(expr)(panel)
            except Exception as exc:  # noqa: BLE001  —— 逐字对齐 factorlib/api.py:91
                return _fail(universe, f"{type(exc).__name__}: {exc}",
                             start=start, end=end)
            if not isinstance(s, pd.Series):
                return _fail(universe,
                             f"特征 '{expr}' 求值返回 {type(s).__name__}, 期望 pd.Series",
                             start=start, end=end)
            if body.winsorize and body.winsorize_q and body.winsorize_q > 0:
                s = winsorize(s, body.winsorize_q)
            if body.standardize:
                s = zscore(s)
            feat_series.append(s.rename(name))
            feature_names.append(name)

        # —— 5. 标签物化 ——
        try:
            from financial_analyst.factors.eval.report import forward_simple_returns
            if use_fwd:
                label_s = forward_simple_returns(panel, fwd_days)
            else:
                try:
                    label_s = _get_alpha(label_raw).compute(panel)
                except KeyError:
                    validate_expr(label_raw)
                    label_s = compile_factor(label_raw)(panel)
        except Exception as exc:  # noqa: BLE001
            return _fail(universe, f"label_error: {type(exc).__name__}: {exc}",
                         start=start, end=end)
        if not isinstance(label_s, pd.Series):
            return _fail(universe,
                         f"标签求值返回 {type(label_s).__name__}, 期望 pd.Series",
                         start=start, end=end, label_kind=label_kind, label_desc=label_desc)
        label_s = label_s.rename("label")

        # —— 6. 拼 fe(MultiIndex 行)、统计、IC ——
        fe = pd.concat(feat_series + [label_s], axis=1)
        n_rows = int(len(fe))
        fe_valid = fe.dropna()
        if fe_valid.empty:
            return _fail(universe, "特征矩阵为空 (窗口内无有效截面)",
                         start=start, end=end, n_rows=n_rows,
                         n_features=len(feature_names), feature_names=feature_names,
                         label_kind=label_kind, label_desc=label_desc)

        dt_idx = fe_valid.index.get_level_values("datetime")
        code_idx = fe_valid.index.get_level_values("code")
        n_dates = int(pd.Index(dt_idx).nunique())
        n_codes = int(pd.Index(code_idx).nunique())

        # coverage:平均每日截面覆盖率(对齐 report.py:84-85 算法,分母用面板全 code 数)。
        panel_codes = max(1, panel.n_codes())
        per_date_cov = fe_valid.groupby(level="datetime").size() / float(panel_codes)
        coverage = _num(per_date_cov.mean())

        # 逐特征 vs 标签 RankIC(复用引擎 ic_analysis;每特征单独 join 标签去 NaN)。
        ic_rows: List[Dict[str, Any]] = []
        try:
            from financial_analyst.factors.eval.ic import ic_analysis
            for name, expr in zip(feature_names, features):
                res = ic_analysis(fe[name], label_s)
                ic_rows.append({
                    "feature": name,
                    "expr": expr,
                    "ic_mean": _num(res.ic_mean),
                    "rank_ic_mean": _num(res.rank_ic_mean),
                    "icir": _num(res.icir),
                })
        except Exception as exc:  # noqa: BLE001  —— IC 失败不否决整次物化,记 reason 进 warnings
            ic_rows = []

        # —— 7. 预览(fe 前 N 行,dropna 前的原始 fe,含 NaN→null)——
        cols = ["datetime", "code"] + feature_names + ["label"]
        prev_rows: List[List[Any]] = []
        if preview_rows > 0:
            head = fe.head(preview_rows)
            for idx, row in zip(head.index, head.itertuples(index=False)):
                dt, cd = idx
                line: List[Any] = [str(pd.Timestamp(dt).date()), str(cd)]
                line.extend(_num(v) for v in row)
                prev_rows.append(line)

        # —— warnings(同 report 风格)——
        warnings: List[str] = []
        if n_dates < 12:
            warnings.append(f"样本太短 (有效日期 {n_dates} < 12), 结论不稳健。")
        if coverage is not None and coverage < 0.5:
            warnings.append(f"特征覆盖率低 ({coverage:.0%})。")
        if not ic_rows:
            warnings.append("IC 计算未产出 (样本或标签退化)。")

        # —— 无状态可复算 fe spec(P3 ML 节点原样 POST 回重建训练集)——
        fe_spec = {
            "features": features,
            "feature_names": feature_names,
            "label": spec_label,             # formula 标签放表达式串;前向收益时为 None
            "fwd_days": fwd_days,
            "universe": universe,
            "start": start,
            "end": end,
            "freq": freq,
            "winsorize": bool(body.winsorize),
            "winsorize_q": float(body.winsorize_q),
            "standardize": bool(body.standardize),
        }

        return JSONResponse({
            "ok": True,
            "universe": universe,
            "n_dates": n_dates,
            "n_codes": n_codes,
            "n_rows": n_rows,
            "n_features": len(feature_names),
            "feature_names": feature_names,
            "coverage": coverage,
            "label_kind": label_kind,
            "label_desc": label_desc,
            "ic": ic_rows,
            "preview": {"columns": cols, "rows": prev_rows},
            "warnings": warnings,
            "fe": fe_spec,
        })

    # ── P3 ML 训练节点 ────────────────────────────────────────────────────
    # 6 条 REST 端点 /model/{kind}(前端 6 个 NODE_EXEC 各 POST 对应路径,kind 由路径锁定),
    # xgboost/lightgbm/svm/rf/mlp 走 _train_eval,lstm 走 _lstm_eval。
    # (原通用门 /model/train「kind 在 body」已删 —— 前端只走专用门,无调用方;ModelTrainIn 仍由专用门复用。)

    @router.post("/model/xgboost")
    def model_xgboost(body: ModelTrainIn):
        """XGBoost 回归(XGBRegressor,hist)训练 + OOS 评测。kind 由路径锁定为 xgboost。"""
        return _train_eval(body, "xgboost")

    @router.post("/model/lightgbm")
    def model_lightgbm(body: ModelTrainIn):
        """LightGBM 回归(LGBMRegressor)训练 + OOS 评测。kind 由路径锁定为 lightgbm。"""
        return _train_eval(body, "lightgbm")

    @router.post("/model/svm")
    def model_svm(body: ModelTrainIn):
        """SVM 回归(sklearn SVR, rbf)训练 + OOS 评测。训练行超上限自动下采样守时延。"""
        return _train_eval(body, "svm")

    @router.post("/model/rf")
    def model_rf(body: ModelTrainIn):
        """随机森林回归(RandomForestRegressor)训练 + OOS 评测。kind 由路径锁定为 rf。"""
        return _train_eval(body, "rf")

    @router.post("/model/mlp")
    def model_mlp(body: ModelTrainIn):
        """MLP 神经网络回归(sklearn MLPRegressor, adam)训练 + OOS 评测。kind 由路径锁定为 mlp。

        前馈多层感知机。MLP **刻意走 sklearn**(轻量、即时,无需每次拉起 torch);要真
        序列网络见 ``/model/lstm``。隐层 layers×hidden、L2 正则 alpha 替 dropout。流程与
        4 ML 节点同形:fe spec 重建 X/y → 时序切 → fit/predict → 预测分截面因子 →
        build_report OOS 评测。训练行超上限自动下采样守时延。
        """
        return _train_eval(body, "mlp")

    @router.post("/model/lstm")
    def model_lstm(body: ModelTrainIn):
        """LSTM 序列网络(PyTorch)训练 + OOS 评测。kind 由路径锁定为 lstm。

        真时间序列建模:复用 /feature/build 物化特征 → 逐 code 按日期滑窗 seq_len 期 →
        nn.LSTM → Linear(hidden→1),adam/MSE 训 epochs 轮 → 预测 test 标签日 → 预测分
        截面因子 → build_report OOS 评测(与 4 ML 节点同形报告)。torch 未装 → 诚实
        ok:False。委托模块级 _lstm_eval。
        """
        return _lstm_eval(body, "lstm")

    # ── P4 多因子构建节点(/factor/pca、/factor/spearman)────────────────────
    # 前端 pca / spearman 节点收上游特征工程(P2 fe spec)→ 原样回灌 → 后端按 fe spec
    # 重建多特征 X → 压成一条截面因子 Series → build_report OOS 评测(同 /factor/report
    # 形,顶层 ic/quantile/portfolio + composite:true 供 analysis 透传)。委托模块级核心。

    @router.post("/factor/pca")
    def factor_pca(body: PCAFactorIn):
        """PCA 因子构建:fe_spec 多特征 X → sklearn PCA 取主成分作截面因子 → OOS 报告。

        复用 /feature/build 物化真 X(已 winsorize/zscore)→ PCA(n_components=k) 降维取
        第 component 主成分得分(方向按载荷自动对齐)→ reindex 整面板 build_report 出
        真 IC/分层/多空 + headline ic_analysis。顶层带 explained_variance_ratio/k +
        /factor/report 同形 ic/quantile/portfolio/characteristics + composite 标记。
        诚实失败 ok:False + reason(sklearn 未装 / 空特征 / 物化失败 / X 不足 / 评测退化,HTTP 200)。
        """
        return _pca_factor(body)

    @router.post("/factor/spearman")
    def factor_spearman(body: SpearmanFactorIn):
        """Spearman 因子构建:fe_spec 多特征 X → 各特征对前向收益 rank-IC 加权合成 → OOS 报告。

        复用 /feature/build 物化真 X → 逐特征对前向收益算 Spearman rank-IC(scipy 优先,
        退 pandas rank+corr)作权重(∑|w|=1,负 IC 特征负权自动对齐方向)→ 截面加权合成
        复合因子 → reindex 整面板 build_report 出真 IC/分层/多空。顶层带 spearman_weights +
        /factor/report 同形 ic/quantile/portfolio/characteristics + composite 标记。
        诚实失败 ok:False + reason(空特征 / 物化失败 / 权重全 0 / 复合空 / 评测退化,HTTP 200)。
        """
        return _spearman_factor(body)

    # ── P5 向量化回测节点(/backtest/vector)──────────────────────────────────
    # 前端 backtest 节点收上游因子(公式因子 .expr / pca·spearman·mf 复合因子 ._label)→
    # 把因子表达式/标签放进 features 回灌 → 后端物化因子截面值 → 逐调仓期 nlargest(topn)
    # 等权 → 真净值 + 年化/Sharpe/回撤/Calmar/换手/胜率(portfolio 块, 进抽屉)+ 诚实
    # 因子视图 ic/quantile/characteristics。委托模块级 _backtest_vector。

    @router.post("/backtest/vector")
    def backtest_vector(body: BacktestVectorIn):
        """TopN 向量化回测:因子 → 逐调仓期 nlargest(topn) 等权 → 真净值 + 组合指标。

        复用 /feature/build 物化因子截面值(多特征等权 z-score 合成)→ _topn_portfolio
        逐调仓日取因子最大 topn 只等权持有(可选多空)→ portfolio_stats 出年化/Sharpe/MDD/
        Calmar/换手/胜率;并 build_report 取诚实因子视图 ic/quantile/characteristics +
        等权全池基准净值。返回 /factor/report 同形顶层(portfolio 用真 TopN 账户覆盖)+
        backtest 块(topn/cash/long_short/trades/portfolio_kpi)+ composite 标记(前端
        终端送 ResultsDrawer)。诚实失败 ok:False + reason(空因子 / 物化失败 / 组合为空 /
        评测退化,HTTP 200)。数据只经 _materialize_xy 内 get_default_loader(不碰 engine/)。
        """
        return _backtest_vector(body)

    # ── W5-1 组合构建节点(/portfolio/build)──────────────────────────────────
    # 前端 portfolio 节点收上游因子 → fe_spec 透传 → 后端在最新截面 nlargest(topn) 按 weighting
    # 定权(+行业中性/单票上限)→ 出最新一期目标持仓权重。dt=portfolio 终端(进抽屉看目标持仓),
    # 亦可再连「向量化回测」的 pf 口。委托模块级 _portfolio_build。
    @router.post("/portfolio/build")
    def portfolio_build(body: PortfolioBuildIn):
        """组合构建:因子 → 最新截面 nlargest(topn) → 按 weighting 定权(+行业中性/单票上限)
        → 最新一期目标持仓权重(下单/展示用)。委托模块级 _portfolio_build。诚实失败 ok:False +
        reason(HTTP 200)。**不回测**该权重(回测节点仍按 TopN 等权)。数据只经 _materialize_xy
        内 get_default_loader(不碰 engine/)。"""
        return _portfolio_build(body)

    # ── #1+#3 可配因子报告(/factor/report2)──────────────────────────────────
    # 补引擎 /factor/report(import-only)缺的 分组/前向窗口/方向 入参。前端 analysis 节点
    # (分组/调仓/方向)+ iccalc(精确周期)接它,使这三个旋钮真生效。委托模块级 _factor_report2。
    @router.post("/factor/report2")
    def factor_report2(body: FactorReport2In):
        """可配单因子报告:freq/分组/前向窗口/方向 全可配(壳内复用 build_report)。
        诚实失败 ok:False + reason(HTTP 200)。数据只经 get_default_loader(不碰 engine/)。"""
        return _factor_report2(body)

    @router.post("/factor/tsic")
    def factor_tsic(body: FactorTsicIn):
        """个股时序 IC(单票/小池:每股因子值 vs 自身未来收益 Spearman 相关)——截面 IC 在单票
        退化时的正确口径。诚实失败 ok:False + reason(HTTP 200)。数据只经 get_default_loader。"""
        return _factor_tsic(body)

    # 注:路径用 /workflow/event(非 /factor/event)—— 引擎 buddy app(server.py:1423 build_app)
    # 已注册 /factor/event(入参 expr_or_name)且先于本 router,FastAPI 命中先注册者会遮蔽本端点
    # (同 /workflow/compose 的处理)。本端点支持自选 codes + 盈亏比/期望值 + 单票退化警告。
    @router.post("/workflow/event")
    def workflow_event(body: FactorEventIn):
        """事件研究(把 >0 触发表达式当离散事件:CAR + 命中率 + t 值 + 逐年 + 盈亏比/期望值)——
        反弹/异动/放量/跳空等稀疏触发的正确口径(截面 IC 对布尔信号是错口径)。复用引擎
        build_event_report(只 import 不改)。诚实失败 ok:False + reason(HTTP 200)。"""
        return _factor_event(body)

    @router.post("/workflow/relstat")
    def workflow_relstat(body: FactorRelstatIn):
        """关系稳定度(共振/跟随等关系因子的描述性体检:均值水平/波动/lag1自相关粘性/正占比)——
        tsic 预测力的描述性补充。诚实失败 ok:False + reason(HTTP 200)。数据只经 get_default_loader。"""
        return _factor_relstat(body)

    @router.post("/workflow/risk")
    def workflow_risk(body: FactorRiskIn):
        """风险度量(VaR/CVaR/EVT/Kupiec):复用回测建 TopN 组合 → 损失分布尾部风险。默认
        周频 + 3 年窗保收益样本足。诚实失败 ok:False + reason(HTTP 200)。不碰引擎。"""
        return _factor_risk(body)

    @router.post("/workflow/garch")
    def workflow_garch(body: FactorGarchIn):
        """条件波动预测(EWMA + GARCH(1,1)):复用回测建 TopN 组合 → 净值期收益 → 拟合条件波动
        路径 + 向前多步预测。默认周频 + 3 年窗。诚实失败 ok:False + reason(HTTP 200)。不碰引擎。"""
        return _garch(body)

    @router.post("/workflow/attrib")
    def workflow_attrib(body: FactorAttribIn):
        """风格归因(Fama-French 风格因子回归):复用回测建 TopN 组合 → 期收益对 市场/规模/价值/动量
        因子收益做 OLS + Newey-West HAC,出因子暴露 β / alpha / R² / 各因子收益贡献。默认月频 + 3 年窗。
        诚实失败 ok:False + reason(HTTP 200)。不碰引擎。"""
        return _attrib(body)

    @router.post("/workflow/tvbeta")
    def workflow_tvbeta(body: FactorTVBetaIn):
        """时变β(Kalman):复用回测建 TopN 组合 → 净值期收益对市场期收益(全池等权)做时变参数回归
        r_t=α_t+β_t·m_t(Kalman 滤波因果 + RTS 平滑全样本),出 β(t) 演化路径 + 静态β对照。默认周频
        + 3 年窗。诚实失败 ok:False + reason(HTTP 200)。不碰引擎。"""
        return _tvbeta(body)

    # 注:路径用 /workflow/compose(非 /factor/compose)—— 引擎 buddy app(server.py:120
    # build_app)已注册 /factor/compose 且先于本 router,FastAPI 命中先注册者会把本端点遮蔽。
    @router.post("/workflow/compose")
    def workflow_compose(body: FactorComposeIn):
        """多因子合成(method=equal|ic|icir 加权)→ 完整报告 + 各腿权重(样本内估权,防前视)。
        诚实失败 ok:False + reason(HTTP 200)。数据只经 get_default_loader(不碰 engine/)。"""
        return _factor_compose(body)

    # ── AI 自动搭图节点(/workflow/generate)──────────────────────────────────
    # 前端「AI 生成工作流」输入框把一句话目标 POST 来 → 后端调 LLM 产出 graph JSON →
    # validate_graph 服务端权威校验 → 不合法把错误回灌 LLM 重生成(≤2 次)→ 合法则只回
    # {ok,graph:{nodes,edges},attempts} 供前端 importJSON 渲染上画布(供审阅,绝不自动运行)。
    # LLM 不可用 / 解析失败 / 重试耗尽 → 诚实失败 {ok:false,reason} HTTP 200,绝不抛 500。
    @router.post("/workflow/generate")
    async def workflow_generate(body: GenIn):
        """一句话目标 → LLM 编译 graph JSON(校验合法,≤2 次错误回灌重生成)。

        async def 直接 await _llm_complete(router 是 FastAPI APIRouter,无需 asyncio.run)。
        把 validate_graph 的 errors 回灌进下一轮 user 消息实现「错误反馈重生成」。任何
        LLM/解析/校验失败均走 JSONResponse({ok:false,reason}) HTTP 200(对齐 _fail/_fail_model/
        _fail_factor),绝不 raise。成功只回 {ok,graph:{nodes,edges},attempts},不夹带其它字段。
        """
        goal = (body.goal or "").strip()
        if not goal:
            return JSONResponse({"ok": False, "reason": "目标不能为空", "attempts": 0})
        max_retries = max(0, min(int(body.max_retries or 0), 2))   # 钳 ≤2
        user = goal
        last_err = ""
        for attempt in range(max_retries + 1):                     # 首次 + ≤2 重试
            try:
                text = await _llm_complete(SYSTEM_PROMPT, user)
            except Exception as exc:  # noqa: BLE001 —— 网络/key/超时 → 诚实失败,不外抛 500
                return JSONResponse({
                    "ok": False,
                    "reason": f"LLM 不可用: {type(exc).__name__}: {exc}",
                    "attempts": attempt,
                })
            try:
                graph = _parse_graph(text)
            except Exception as exc:  # noqa: BLE001 —— 无法解析 → 回灌提示重生成
                last_err = f"JSON 解析失败: {exc}"
                user = (
                    goal
                    + f"\n\n上次输出无法解析为 JSON({exc})。只输出一个 JSON 对象 "
                    + "{nodes,edges},不要任何解释或围栏。"
                )
                continue
            ok, errs = validate_graph(graph)
            if ok:
                _autowire_source(graph)   # P3 自愈:补漏连的 source.data→feature.src(股票池脐带)
                return JSONResponse({
                    "ok": True,
                    "graph": {"nodes": graph["nodes"], "edges": graph["edges"]},
                    "attempts": attempt + 1,
                })
            last_err = "; ".join(errs[:8])
            user = (
                goal
                + "\n\n你上次产出的图有以下校验错误,请修正后重新只输出 JSON {nodes,edges}:\n- "
                + "\n- ".join(errs[:8])
            )
        return JSONResponse({
            "ok": False,
            "reason": f"重试 {max_retries} 次仍不合法: {last_err}",
            "attempts": max_retries + 1,
        })

    # ── W8b AI 闭环 critique 环(/workflow/critique)──────────────────────────
    @router.post("/workflow/critique")
    async def workflow_critique(body: CritiqueIn):
        """目标 + 当前图 + **真实回测指标** → LLM 诊断问题并产改进图(同 generate 契约,
        validate_graph 校验);LLM 不可用 / 产图不合法 → 规则兜底确定性改进。绝不抛 500。"""
        import json as _json
        goal = (body.goal or "").strip()
        cur = body.graph if isinstance(body.graph, dict) else {}
        m = body.metrics or {}
        summary = {k: m.get(k) for k in ("rank_ic", "sharpe", "ann_return", "oos_verdict", "n_dates", "factor")}
        constraints = (body.constraints or "").strip()
        user = (
            f"目标:{goal or '(未给)'}\n"
            f"当前工作流 graph:{_json.dumps(cur, ensure_ascii=False)}\n"
            f"真实回测指标:{_json.dumps(summary, ensure_ascii=False)}\n"
            + (f"求值环境约束(必须遵守,优先于上面的通用改法):{constraints}\n" if constraints else "")
            + "请据指标诊断并只输出改进后的 {diagnosis,nodes,edges}。"
        )
        try:
            text = await _llm_complete(_CRITIQUE_SYS, user, temperature=0.3)
            obj = _parse_graph(text)
        except Exception as exc:  # noqa: BLE001 —— LLM/解析失败 → 规则兜底(闭环不断)
            rc = _rule_critique(goal, m, cur)
            rc["llm_error"] = f"{type(exc).__name__}: {exc}"
            return JSONResponse(rc)
        diag = str(obj.get("diagnosis") or "").strip()
        ok, errs = validate_graph(obj)
        if not ok:
            rc = _rule_critique(goal, m, cur)
            rc["diagnosis"] = (diag + " · " if diag else "") + "(LLM 改进图不合法[" + "; ".join(errs[:3]) + "],改用规则:" + rc["diagnosis"] + ")"
            return JSONResponse(rc)
        _autowire_source(obj)   # P3 自愈:补漏连的 source.data→feature.src
        return JSONResponse({
            "ok": True,
            "diagnosis": diag or "已据指标改进工作流。",
            "graph": {"nodes": obj["nodes"], "edges": obj["edges"]},
            "source": "llm",
        })

    # ===== 工作流服务端执行(P4 执行器 HTTP 门面)及持久化 ======
    # 契约:POST /workflow/run  {graph, universe?, freq?, start?, end?, oos_frac?} → executor 结果
    #      POST /workflow/save {name, graph:{nodes,edges}} → {ok,id,ts,name}
    #       GET  /workflow/list                            → {ok, items:[{id,name,ts,graph}]}
    #       GET  /workflow/get/{wid}                       → {ok, graph:{nodes,edges}, name,id,ts}
    #       DELETE /workflow/delete/{wid}                  → {ok}
    # 诚实失败:一律 {ok:False, reason} HTTP 200,绝不抛 500(对齐本文件 _fail 范式)。

    @router.post("/workflow/run")
    def workflow_run(body: WorkflowRunIn):
        """服务端整图执行(同步 def → FastAPI 线程池;绝不在事件循环里跑重活)。
        诚实失败 {ok:false,reason} HTTP 200;overrides 仅透传显式给出的键。"""
        from guanlan_v2.workflow import executor as _wex
        g = body.graph if isinstance(body.graph, dict) else {}
        if not (isinstance(g.get("nodes"), list) and g.get("nodes")):
            return JSONResponse({"ok": False, "reason": "graph.nodes 缺失或为空"})
        ov = {k: v for k, v in (("universe", body.universe), ("freq", body.freq),
                                ("start", body.start), ("end", body.end),
                                ("oos_frac", body.oos_frac)) if v is not None}
        try:
            return JSONResponse(_wex.run_graph(g, overrides=(ov or None)))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/workflow/save")
    def workflow_save(body: WorkflowSaveIn):
        """保存工作流到服务端 .data/workflows/<id>.json(后端生成 id/ts)。"""
        try:
            g = body.graph or {}
            if not isinstance(g.get("nodes"), list):
                return JSONResponse({"ok": False, "reason": "graph.nodes 缺失或非数组"})
            rec = wf_store.save(body.name, g)
            return JSONResponse({
                "ok": True,
                "id": rec["id"],
                "ts": rec["ts"],
                "name": rec["name"],
            })
        except Exception as exc:  # noqa: BLE001  诚实失败 HTTP 200,不抛 500
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/workflow/list")
    def workflow_list():
        """列出全部已存工作流(回全量含 graph,按 ts 倒序)。"""
        try:
            items = wf_store.list()
            return JSONResponse({"ok": True, "items": items})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "items": [], "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/workflow/get/{wid}")
    def workflow_get(wid: str):
        """取单个工作流全量(id 安全化防穿越;不存在回 ok:False,不抛 404)。"""
        try:
            rec = wf_store.get(wid)
            if rec is None:
                return JSONResponse({"ok": False, "reason": "工作流不存在"})
            return JSONResponse({
                "ok": True,
                "graph": rec["graph"],
                "name": rec.get("name", ""),
                "id": rec.get("id", wid),
                "ts": rec.get("ts", 0),
            })
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.delete("/workflow/delete/{wid}")
    def workflow_delete(wid: str):
        """删除单个工作流(id 安全化防穿越;存在删并回 ok:True,不存在 ok:False)。"""
        try:
            ok = wf_store.delete(wid)
            if not ok:
                return JSONResponse({"ok": False, "reason": "工作流不存在"})
            return JSONResponse({"ok": True, "id": wid})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/workflow/delete")
    def workflow_delete_post(body: Dict[str, Any]):
        """删除别名:前端历史抽屉经 ``POST {id}`` 调用,复用 DELETE 同一 ``wf_store.delete``
        (前端 _post 仅支持 POST,故加此别名让服务端删除真生效;不存在回 ok:False)。"""
        try:
            wid = str((body or {}).get("id", "")).strip()
            if not wid:
                return JSONResponse({"ok": False, "reason": "id 不能为空"})
            ok = wf_store.delete(wid)
            if not ok:
                return JSONResponse({"ok": False, "reason": "工作流不存在"})
            return JSONResponse({"ok": True, "id": wid})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})

    # ════════════ W1a · 数据源真实体检(/data/*)════════════
    @router.get("/data/universes")
    def data_universes():
        """列出真能解析的股票池(逐个 resolve_universe_codes,只回非空者 + 真成分数)。

        数据源节点「股票池」下拉据此渲染:隐晦 id → 中文名 + 真实成分数。
        引擎缺失 / 全空 → ``ok:False`` 或空 universes(前端降级,不抛 500)。
        """
        try:
            from financial_analyst.data.universe import resolve_universe_codes
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False,
                                 "reason": f"engine_import_error: {type(exc).__name__}: {exc}",
                                 "universes": []})
        out: List[Dict[str, Any]] = []
        for uid, name in _UNIVERSE_CATALOG:
            try:
                codes = resolve_universe_codes(uid)
            except Exception:  # noqa: BLE001  —— 单个池解析失败不阻断其它
                codes = []
            if codes:
                out.append({"id": uid, "name": name, "n_codes": len(codes)})
        return JSONResponse({"ok": True, "universes": out})

    @router.post("/data/probe")
    def data_probe(body: DataProbeIn):
        """股票池真实体检:resolve → 加载真面板窗口 → 真实覆盖(票数/交易日/字段/覆盖率)。

        流程(全引擎 primitive,函数体内延迟 import):
          1. ``resolve_universe_codes`` 解析池(空 → ``ok:False``)。
          2. ``get_default_loader`` + ``load_panel_cached`` 加载 [start,end] 窗口真面板。
          3. 从 ``panel.df``(MultiIndex datetime×code)统计真实:有数据票数 / 交易日数 /
             数据起止 / 可用字段(非全空列)/ 平均每日覆盖率。
        默认窗口 = 今天 − 90d(守秒级);失败一律 ``ok:False`` + ``reason``(HTTP 200)。
        """
        import pandas as pd

        universe = (body.universe or "csi300").strip() or "csi300"

        # —— 1. resolve(空 → 诚实失败)——
        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "universe": universe,
                                 "reason": f"universe_error: {type(exc).__name__}: {exc}"})
        if not codes:
            return JSONResponse({"ok": False, "universe": universe,
                                 "reason": f"empty_universe: '{universe}' 解析为空"})
        n_codes_total = len(codes)

        freq = (body.freq or "day").strip() or "day"
        end = (body.end or "").strip() or date.today().isoformat()
        # 体检默认看近 3 年(展示真实数据广度;日线数据实际可到 ~2016)。用户经 start 可拉更早。
        start = (body.start or "").strip() or (date.today() - timedelta(days=365 * 3)).isoformat()

        # —— 2. 面板加载(逐字同 feature/build 链;失败 → 诚实失败)——
        try:
            from financial_analyst.data.loader_factory import get_default_loader
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            loader = get_default_loader()
            try:
                from financial_analyst.data.loaders.industry import (
                    IndustryLoader,
                    industry_map_path,
                )
                ind_loader = IndustryLoader() if industry_map_path().exists() else None
            except Exception:  # noqa: BLE001  —— 行业图缺失只是少 industry 列,不阻断
                ind_loader = None
            panel = load_panel_cached(loader, codes, start, end, freq=freq,
                                      industry_loader=ind_loader)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "universe": universe, "n_codes": n_codes_total,
                                 "start": start, "end": end,
                                 "reason": f"load_error: {type(exc).__name__}: {exc}"})

        # —— 3. 从真面板统计真实覆盖 ——
        try:
            df = panel.df
            dt_idx = df.index.get_level_values("datetime")
            code_idx = df.index.get_level_values("code")
            n_dates = int(pd.Index(dt_idx).nunique())
            n_codes_data = int(pd.Index(code_idx).nunique())
            date_min = str(pd.Timestamp(dt_idx.min()).date()) if n_dates else None
            date_max = str(pd.Timestamp(dt_idx.max()).date()) if n_dates else None
            # 可用字段 = 面板真实非全空列(DSL 可用基础字段)
            fields = [str(c) for c in df.columns if not df[c].isna().all()]
            # 覆盖率 = 平均每日截面票数 / 池子总票数
            coverage = None
            if n_dates:
                per_date = df.groupby(level="datetime").size()
                coverage = _num(float(per_date.mean()) / float(max(1, n_codes_total)))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "universe": universe, "n_codes": n_codes_total,
                                 "start": start, "end": end,
                                 "reason": f"probe_error: {type(exc).__name__}: {exc}"})

        return JSONResponse({
            "ok": True,
            "universe": universe,
            "freq": freq,
            "start": start,
            "end": end,
            "n_codes": n_codes_total,        # 池子解析出的总票数
            "n_codes_data": n_codes_data,    # 窗口内真有数据的票数
            "n_dates": n_dates,              # 窗口内交易日数
            "date_min": date_min,            # 真实数据起
            "date_max": date_max,            # 真实数据止
            "coverage": coverage,            # 平均每日截面覆盖率
            "fields": fields,                # 可用字段(真实非空列)
            "n_fields": len(fields),
        })

    @router.post("/factor/preview")
    def factor_preview(body: FactorPreviewIn):
        """校验 + 真预览一条 zoo-DSL 表达式:validate_expr → 小池面板 compile_factor 求值
        → 最新截面真值样本 + 统计(均值/σ/min/max/覆盖)。供公式节点「校验·预览」。

        两段诚实失败:``stage='validate'``(语法/字段非法)与 ``stage='compute'``(求值报错)
        分别带标记;全引擎 primitive 延迟 import,默认小池 csi_fast 守秒级;失败 ok:False(HTTP 200)。
        """
        import pandas as pd

        expr = (body.expr or "").strip()
        if not expr:
            return JSONResponse({"ok": False, "reason": "表达式为空", "stage": "validate"})

        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            from financial_analyst.factors.zoo.registry import get as _get_alpha
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "stage": "validate",
                                 "reason": f"engine_import_error: {type(exc).__name__}: {exc}"})

        # —— 校验:注册名优先,否则当 zoo-DSL 表达式(同 feature/build 求值前判定)——
        is_alpha = False
        try:
            _get_alpha(expr)
            is_alpha = True
        except KeyError:
            try:
                validate_expr(expr)
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"ok": False, "stage": "validate", "expr": expr,
                                     "reason": f"{type(exc).__name__}: {exc}"})

        universe = (body.universe or "csi_fast").strip() or "csi_fast"
        freq = (body.freq or "day").strip() or "day"
        end = (body.end or "").strip() or date.today().isoformat()
        start = (body.start or "").strip() or (date.today() - timedelta(days=90)).isoformat()

        try:
            from financial_analyst.data.universe import resolve_universe_codes
            codes = resolve_universe_codes(universe)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"universe_error: {type(exc).__name__}: {exc}"})
        if not codes:
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"empty_universe: '{universe}'"})

        try:
            from financial_analyst.data.loader_factory import get_default_loader
            from financial_analyst.factors.zoo.panel_cache import load_panel_cached
            loader = get_default_loader()
            try:
                from financial_analyst.data.loaders.industry import (
                    IndustryLoader,
                    industry_map_path,
                )
                ind_loader = IndustryLoader() if industry_map_path().exists() else None
            except Exception:  # noqa: BLE001
                ind_loader = None
            panel = load_panel_cached(loader, codes, start, end, freq=freq,
                                      industry_loader=ind_loader)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"load_error: {type(exc).__name__}: {exc}"})

        # —— 求值(注册名 .compute 或 DSL 表达式)——
        try:
            s = _get_alpha(expr).compute(panel) if is_alpha else compile_factor(expr)(panel)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"{type(exc).__name__}: {exc}"})
        if not isinstance(s, pd.Series):
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"求值返回 {type(s).__name__}, 期望 Series"})

        s = s.replace([float("inf"), float("-inf")], float("nan")).dropna()
        if s.empty:
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": "求值结果全为空 (窗口内无有效截面)"})

        # —— 最新截面真值样本 + 统计 ——
        try:
            dts = s.index.get_level_values("datetime")
            last = dts.max()
            cs = s[dts == last]
            cs_sorted = cs.sort_values(ascending=False)
            sample: List[Dict[str, Any]] = []
            for idx, v in cs_sorted.head(6).items():
                code = idx[1] if isinstance(idx, tuple) else idx
                sample.append({"code": str(code), "value": _num(v)})
            panel_codes = max(1, panel.n_codes())
            payload = {
                "ok": True,
                "expr": expr,
                "universe": universe,
                "is_alpha": is_alpha,
                "latest_date": str(pd.Timestamp(last).date()),
                "n_total": int(len(s)),
                "n_cross": int(len(cs)),
                "coverage": _num(float(len(cs)) / float(panel_codes)),
                "stats": {
                    "mean": _num(cs.mean()),
                    "std": _num(cs.std()),
                    "min": _num(cs.min()),
                    "max": _num(cs.max()),
                },
                "sample": sample,
            }
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "stage": "compute", "expr": expr,
                                 "reason": f"preview_error: {type(exc).__name__}: {exc}"})
        return JSONResponse(payload)

    @router.get("/factor/catalog")
    def factor_catalog():
        """因子库橱窗:返回预置研报标准 A 股因子(中文名/表达式/大类/方向/说明)。

        静态元数据(不读数据,秒回);全部 442 内置(alpha101/gtja191/qlib158)+ 39
        仓内因子另经引擎 ``/factor/list`` 浏览。表达式已预定向(高分=偏好)。
        """
        factors = [
            {"name": n, "expr": e, "cat": c, "dir": d, "desc": s}
            for (n, e, c, d, s) in _FACTOR_CATALOG
        ]
        return JSONResponse({"ok": True, "n": len(factors), "cats": _FACTOR_CATS, "factors": factors})

    # ── P6 工作流模型「存入模型库」端点 ──────────────────────────────────────────
    @router.post("/model/promote")
    def model_promote(body: dict = Body(default={})):
        """工作流模型「存入模型库」:校验 recipe → 单飞抢锁 → 起子进程生产重训(全市场全窗口)
        → 落 models/<vid>(source=workflow)。立即返回(异步)。"""
        import time as _t, uuid, datetime
        kind = str(body.get("kind") or "").strip()
        recipe = dict(body.get("recipe") or {})
        if not recipe.get("features"):
            return JSONResponse({"ok": False, "reason": "recipe.features 为空"})
        if kind not in ("lightgbm", "xgboost", "rf"):
            return JSONResponse({"ok": False, "reason": f"kind '{kind}' 首期不支持入库(树模型:lightgbm/xgboost/rf)"})
        with _PROMOTE_LOCK:
            already = _PROMOTE_STATE["running"]
            if not already:
                vid = "m_" + uuid.uuid4().hex[:10]
                _PROMOTE_STATE.update({"running": True, "phase": "starting", "label": "启动生产重训…",
                    "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                    "variant_id": vid, "lines": []})
        # NOTE: _promote_public_state() re-acquires _PROMOTE_LOCK — must NOT be called while lock is held
        if already:
            return JSONResponse({"ok": False, "reason": "已有入库在跑", "state": _promote_public_state()})
        spec = {"variant_id": vid, "name": str(body.get("name") or "工作流模型"),
                "kind": kind, "recipe": recipe,
                "created": datetime.datetime.now().isoformat(timespec="seconds")}
        _threading.Thread(target=lambda: _run_promote_subprocess(spec), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "variant_id": vid, "state": _promote_public_state()})

    @router.get("/model/promote/status")
    def model_promote_status():
        return JSONResponse({"ok": True, "state": _promote_public_state()})

    @router.post("/model/publish_dl")
    def model_publish_dl(body: dict = Body(default={})):
        """发布 LSTM 为生产 DL 源:起子进程(训练 → 写 var/dl_pred_lstm.parquet → regen 折进 v4)。
        异步立即返回 + 轮询 /model/publish_dl/status。kind 限 lstm(首期)。"""
        import time as _t, uuid
        kind = str(body.get("kind") or "lstm").strip()
        if kind != "lstm":
            return JSONResponse({"ok": False, "reason": f"kind '{kind}' 暂不支持发布为 DL 源(首期 lstm)"})
        with _PUBLISH_DL_LOCK:
            already = _PUBLISH_DL_STATE["running"]
            if not already:
                vid = "dl_" + uuid.uuid4().hex[:10]
                _PUBLISH_DL_STATE.update({"running": True, "phase": "starting", "label": "启动发布…",
                    "step": 0, "started_at": _t.time(), "ended_at": None, "ok": None, "error": None,
                    "variant_id": vid, "lines": []})
        if already:
            return JSONResponse({"ok": False, "reason": "已有发布在跑", "state": _publish_dl_public_state()})
        spec = {"variant_id": vid, "kind": "lstm",
                "date": str(body.get("date") or "").strip() or None,
                "universe": str((body.get("recipe") or {}).get("universe") or body.get("universe") or "csi800"),
                "params": dict((body.get("recipe") or {}).get("params") or body.get("params") or {})}
        _threading.Thread(target=lambda: _run_publish_dl_subprocess(spec), daemon=True).start()
        return JSONResponse({"ok": True, "started": True, "variant_id": vid,
                             "state": _publish_dl_public_state()})

    @router.get("/model/publish_dl/status")
    def model_publish_dl_status():
        return JSONResponse({"ok": True, "state": _publish_dl_public_state()})

    return router
