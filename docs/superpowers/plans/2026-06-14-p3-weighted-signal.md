# P3 加权混合进信号 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 逐任务执行。步骤用 `- [ ]`。
> 本仓**无 git**——「提交」= pytest 全绿(`G:\financial-analyst\.venv\Scripts\python.exe -m pytest -q`)。G:\stocks 只读。改 python 须重启 9999(杀监听 PID 等 ~10s 看门狗拉新)。改 jsx 必 bump `?v`(Edit 非 sed)。诚实降级不算 mock。GateGuard:每次 Write/Edit 前陈述四事实。

**Goal:** 把因子 z 分按权重 w 真正混进回测决策(非仅喂 prompt):`bias=(1-w)·LLM分+w·因子z分`,回测同记纯 LLM 净值与混合净值做归因。w=0 严格等于现状纯 LLM。

**Architecture:** 复用 P2 的 `var/factor_vintage_tsic.parquet` 的 `fval`(无需新求值器)→ 因子 z 分(date≤asof trailing,vintage IC 符号定向)→ 每因子 clip(dir·z,-1,1) 等权平均成 factor_score → 与 llm_score(sgn(dir)·conf/100)按 w 混合 → hybrid_direction(w=0 或无因子信号透传 LLM 不经死区;w>0 sgn(bias)+死区 τ=0.15)。decide 返回+落盘 hybrid;前端 runBacktest 跑两遍出双线净值。

**Tech Stack:** Python 3.13/pandas;FastAPI 9999;no-build React UMD(`ui/seats/*.jsx`)。

**复用基准(实现时现场 Read)**:
- `guanlan_v2/screen/factor_vintage.py` —— P2 已有 `cs_vintage_from_frame`/`tsic_vintage_from_frame`/`load_tsic_vintage`/`tsic_vintage_asof`/`cs_vintage_asof`(返回 `{ic,n,dir,asof}`);tsic parquet 列 `[code,id,date,fval,fwd,realized_date]`。P3 加 `factor_z_*`。
- `guanlan_v2/seats/api.py` —— P2 已有 `_factor_id_index`/`_resolve_factor_id`/`_rf_vintage_line`(≈432-480);decide seats_decide(≈953-1320),`j`=LLM JSON 结论(direction/confidence,≈1208),响应 return(≈1239-1248),落盘 dict(≈1223-1238)。
- `ui/seats/luozi-data.jsx` —— `runBacktest(runDecs, bars)`(≈1177-1207,返回 `{eq,trades}`)、`metricsOf(eq,trades,freq)`(≈337-359)、策略实体(≈109-150)、`strategySave`(≈135-150)、`recipeForStrategy`(≈185-206)。
- `ui/seats/luozi-app.jsx` —— `runRealThink`(≈201-287)、`repPerf`(≈179-186)、`runDecs` 映射(≈333-346)、净值图消费 `repPerf.eq`。
- `ui/seats/luozi-foundry.jsx` —— 新建/编辑表单 numCell/selCell(≈252-276)。
- `ui/seats/luozi-panels.jsx` —— `RunDecCard`(P2 vintage 字段附近,≈1053-1075)。
- SEATS_POOL_CODES = SZ300750/SH600519/SZ002594/SZ300308/SH601012/SH600036/SH605358。

---

### Task 1: factor_vintage.py `factor_z_asof` — 因子 z 分(PIT)(TDD)

**Files:** Modify `guanlan_v2/screen/factor_vintage.py`;Test `tests/test_factor_vintage.py`(追加)

- [ ] **Step 1: 写失败测试**
```python
from guanlan_v2.screen.factor_vintage import factor_z_from_frame


def _zframe():
    import pandas as pd
    rows = [{"code": "SH605358", "id": "mom_20", "date": f"2026-01-{d:02d}", "fval": v}
            for d, v in zip(range(1, 12), [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 6])]  # 前10个=1,末=6
    rows.append({"code": "SH605358", "id": "mom_20", "date": "2026-02-01", "fval": 99.0})  # 未来,排除
    return pd.DataFrame(rows)


def test_factor_z_pit_and_value():
    r = factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-11", window=60, min_n=5)
    assert r is not None
    assert r["z"] > 1.0 and r["fval"] == 6.0 and r["n"] == 11
    assert r["asof"] == "2026-01-11"   # 不含 2026-02-01


def test_factor_z_excludes_future_date():
    r = factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-05", window=60, min_n=3)
    assert r is not None and r["fval"] == 1.0 and r["n"] == 5


def test_factor_z_min_n_none():
    assert factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-03", window=60, min_n=5) is None


def test_factor_z_constant_std_zero_none():
    import pandas as pd
    flat = pd.DataFrame([{"code": "SH605358", "id": "x", "date": f"2026-01-{d:02d}", "fval": 2.0}
                         for d in range(1, 11)])
    assert factor_z_from_frame(flat, "SH605358", "x", "2026-01-10", min_n=3) is None   # std=0 → None
```

- [ ] **Step 2: 跑确认失败。**

- [ ] **Step 3: 实现**(追加;fval 当日即知,只 date≤asof,无需 realized_date 闸门):
```python
def factor_z_from_frame(df, code: str, factor_id: str, date: str, window: int = 60,
                        min_n: int = 10) -> Optional[dict]:
    """单票本因子 fval 的 trailing z 分(纯函数)。df 列含 [code,id,date,fval]。
    取 date≤date 的最近 window 条 fval,z=(当前fval−mean)/std;<min_n 或 std=0 → None。
    fval 在其 date 当日已知(PIT 安全),无需 realized_date 闸门。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[(df["code"].astype(str) == str(code)) & (df["id"].astype(str) == str(factor_id))].copy()
    if sub.empty:
        return None
    sub = sub[sub["date"].astype(str) <= str(date)].sort_values("date").tail(int(window))
    sub = sub.dropna(subset=["fval"])
    if len(sub) < int(min_n):
        return None
    vals = sub["fval"].astype(float)
    sd = float(vals.std())
    if not (sd > 0):
        return None
    cur = float(vals.iloc[-1])
    return {"z": round((cur - float(vals.mean())) / sd, 4), "fval": cur,
            "n": int(len(sub)), "asof": str(sub["date"].iloc[-1])}


def factor_z_asof(code: str, factor_id: str, date: str, window: int = 60,
                  min_n: int = 10) -> Optional[dict]:
    return factor_z_from_frame(load_tsic_vintage(), code, factor_id, date, window, min_n)
```

- [ ] **Step 4: 跑测试通过 + 全量无回归。commit。**

---

### Task 2: 评分纯函数(seats/api.py)— llm_score / 因子分合成 / hybrid_direction(TDD)

**Files:** Modify `guanlan_v2/seats/api.py`(加模块级纯函数);Test `tests/test_seats_hybrid.py`(新)

- [ ] **Step 1: 写失败测试**
```python
import guanlan_v2.seats.api as api


def test_llm_score_mapping():
    assert api._llm_score("买入", 85) == 0.85
    assert api._llm_score("卖出", 70) == -0.70
    assert api._llm_score("观望", 60) == 0.0
    assert api._llm_score("买入", None) == 0.0


def test_combine_factor_score_clip_equal_dir():
    feats = [{"z": 5.0, "dir": 1}, {"z": 0.5, "dir": -1}]   # clip(+5)=+1、(-1*0.5)=-0.5 → 均值0.25
    assert abs(api._combine_factor_score(feats) - 0.25) < 1e-9


def test_combine_factor_score_none_when_empty():
    assert api._combine_factor_score([]) is None
    assert api._combine_factor_score([{"z": None, "dir": 1}]) is None


def test_hybrid_direction_w0_passthrough_no_deadband():
    assert api._hybrid_direction("买入", 0.10, factor_score=0.9, w=0.0) == ("买入", 0.10)


def test_hybrid_direction_none_factor_passthrough():
    assert api._hybrid_direction("卖出", -0.7, factor_score=None, w=0.5) == ("卖出", -0.7)


def test_hybrid_direction_w_mix_and_deadband():
    d, b = api._hybrid_direction("买入", 0.10, factor_score=0.9, w=0.5)
    assert d == "买入" and abs(b - 0.5) < 1e-9
    d2, _ = api._hybrid_direction("买入", 0.10, factor_score=-0.9, w=0.8)   # bias=-0.70 → 翻成卖
    assert d2 == "卖出"
    d3, _ = api._hybrid_direction("买入", 0.10, factor_score=-0.10, w=0.5)  # bias=0.0 死区 → 观望
    assert d3 == "观望"
```

- [ ] **Step 2: 跑确认失败。**

- [ ] **Step 3: 实现**(模块级,`_rf_vintage_line` 附近):
```python
_HYBRID_TAU = 0.15   # 死区:|bias|≤τ → 观望(仅 w>0 混合路径)


def _llm_score(direction, confidence) -> float:
    """LLM 决策 → [-1,1]:买+ 卖− 观望0,幅度=confidence/100。"""
    d = str(direction or "")
    try:
        c = float(confidence) / 100.0
    except (TypeError, ValueError):
        return 0.0
    if "买" in d:
        return round(c, 4)
    if "卖" in d:
        return round(-c, 4)
    return 0.0


def _combine_factor_score(feats):
    """每因子 clip(dir·z,-1,1) 等权平均 → [-1,1];只纳入有 z 且有 dir 的因子;全无 → None。"""
    vals = []
    for f in (feats or []):
        z = f.get("z")
        dr = f.get("dir")
        if z is None or dr is None:
            continue
        vals.append(max(-1.0, min(1.0, float(dr) * float(z))))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _hybrid_direction(llm_dir, llm_score, factor_score, w):
    """返回 (hybrid_direction, bias)。w<=0 或 factor_score=None → 透传 LLM 方向(不经死区);
    否则 bias=(1-w)·llm+w·factor,sgn+死区 τ。"""
    try:
        w = float(w)
    except (TypeError, ValueError):
        w = 0.0
    if w <= 0 or factor_score is None:
        return (str(llm_dir or "观望"), round(float(llm_score), 4))
    bias = round((1.0 - w) * float(llm_score) + w * float(factor_score), 4)
    if bias > _HYBRID_TAU:
        return ("买入", bias)
    if bias < -_HYBRID_TAU:
        return ("卖出", bias)
    return ("观望", bias)
```

- [ ] **Step 4: 跑测试通过 + 全量无回归。commit。**

---

### Task 3: decide 接线 — w 透传 + 因子分/混合 + 响应/落盘(TDD)

**Files:** Modify `guanlan_v2/seats/api.py`(seats_decide + 扩 `_rf_vintage_line` 带 z/score/dir);Test `tests/test_seats_hybrid.py`(追加)

- [ ] **Step 1: 扩 `_rf_vintage_line` 每因子记录加 `z`/`dir`/`score`**:命中 vintage 后 `_z = factor_z_asof(code, fid, pit_date)`;记录加 `"z": (_z["z"] if _z else None), "dir": r["dir"], "score": (max(-1,min(1,r["dir"]*_z["z"])) if _z else None)`。写测试(monkeypatch `api.factor_z_asof` + tsic/cs_vintage_asof)验证 vint[0] 带 z/dir/score。顶部 import 加 `factor_z_asof`。
- [ ] **Step 2: 跑确认失败 → 实现 → 通过。**
- [ ] **Step 3: decide 主体**:`w = payload.get("w")`;LLM 结论 `j` 出来后(`rf_line, _rf_vint = _rf_vintage_line(...)` 已在前):
```python
            _llm_s = _llm_score(j.get("direction"), j.get("confidence"))
            _factor_s = _combine_factor_score(_rf_vint)
            _hyb_dir, _hyb_bias = _hybrid_direction(j.get("direction"), _llm_s, _factor_s, w)
```
响应 return 加 `"w": w, "llm_score": _llm_s, "factor_score": _factor_s, "hybrid_bias": _hyb_bias, "hybrid_direction": _hyb_dir`;落盘 dict 加同 5 键。
- [ ] **Step 4: decide 纯函数链已覆盖,端到端 live 留收口验。全量 pytest 无回归。commit。**

---

### Task 4: 前端 — w 滑块 + 双线净值 + RunDecCard hybrid(控制端做,浏览器验)

**Files:** `luozi-foundry.jsx`(w 滑块)、`luozi-data.jsx`(strategySave 存 w / runBacktest 双线)、`luozi-app.jsx`(runRealThink 传 w / runDecs 带 hybrid / repPerf 双线 / 净值图两线)、`luozi-panels.jsx`(RunDecCard hybrid)、`观澜 · 落子.html`(bump ?v=20260614e)

- [ ] **Step 1:** Foundry 加「因子权重 w」(numCell 0-1 step 0.05 默认 0)+ 说明「0=纯LLM」。
- [ ] **Step 2:** strategySave 持久化 `w: (o.w != null ? +o.w : 0)`;策略读取暴露 w。
- [ ] **Step 3:** runRealThink payload 加 `w: (strat && strat.w) || 0`。
- [ ] **Step 4:** runDecs 加 `hybrid_direction: r.hybrid_direction || r.direction, factor_score: (r.factor_score==null?null:r.factor_score), hybrid_bias: (r.hybrid_bias==null?null:r.hybrid_bias), w: r.w || 0`。
- [ ] **Step 5:** runBacktest 支持 `useHybrid`(用 `d.hybrid_direction` 派生 side 否则 `d.direction`);repPerf 算 `{eq,trades}`(LLM)+ `{eqHybrid,tradesHybrid}`(hybrid)两套 + metricsOf 各一套。
- [ ] **Step 6:** 净值图两条线(LLM 实线 + 混合异色/虚线)+ 归因小字(混合 total − LLM total);w=0 两线重合标注。
- [ ] **Step 7:** RunDecCard:w>0 显「混合:{hybrid_direction}(bias=.. 因子分=.. w=..)」,否则「纯LLM(w=0)」。
- [ ] **Step 8:** bump ?v=20260614e;浏览器 0 console error。

---

### Task 5: 收口 — pytest + 重启 + live + 浏览器 e2e + 文档 + memory + 评审

- [ ] **Step 1:** 全量 pytest 绿(277 + P3 新测)。
- [ ] **Step 2:** 重启 9999。
- [ ] **Step 3:** live 验证:立昂微 SH605358 绑动量库因子,同决策日 **w=0 vs w=0.6** 两次 decide;读 jsonl:w=0 hybrid_direction==direction(纯LLM 透传)、w=0.6 落盘 factor_score/hybrid_bias/hybrid_direction 真混合。验完删验证 run 行。
- [ ] **Step 4:** 浏览器 e2e:?v=20260614e 0 error;校场设 w>0 真回测 → 复盘双线净值(混合≠纯LLM)+ RunDecCard hybrid;w=0 两线重合。截图。
- [ ] **Step 5:** `ui/seats/README.md` 加 P3 条目。
- [ ] **Step 6:** memory `backtest-cards-design.md` P3 改已交付、三期全收;`MEMORY.md` 索引更新。
- [ ] **Step 7:** 最终 review(python-reviewer 评分纯函数+decide;react-reviewer 前端双线)。

---

## Self-Review
- **spec 覆盖**:因子 z(T1)✓ llm_score/因子分/hybrid(T2)✓ decide w+落盘(T3)✓ 前端 w滑块+双线+RunDecCard(T4)✓ w=0 严格透传不经死区(T2 w<=0 分支)✓ 死区仅 w>0 ✓ 诚实退化 factor_score=None 透传 ✓。
- **类型一致**:`factor_z_asof→{z,fval,n,asof}`、`_llm_score→float`、`_combine_factor_score→float|None`、`_hybrid_direction→(str,float)`;decide 键 `w/llm_score/factor_score/hybrid_bias/hybrid_direction` + 前端 runDecs 对齐。
- **PIT**:factor_z 用 date≤asof(fval 当日已知);decide pit_date 沿用 P2 freq 感知;方向用 vintage IC(P2 已 PIT)。
- **占位扫描**:无 TODO;T4 控制端做+浏览器验(同 P1/P2 前端口径)。
