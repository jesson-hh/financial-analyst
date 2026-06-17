# 因子语义契约层 Implementation Plan(0612演习修复#1)

> **状态:已执行完毕并验收(2026-06-12)** — SC1-SC4 两段审查全过(SC1 质量审查采纳 I1 fallback去科学计数法/I2 负零防御/I3 边界测试,C1 xfail 建议被 controller 驳回;SC5 首轮 Explore「矛盾」判定经 controller 读源裁决推翻=v4 自洽),pytest **168 绿**(基线155+13),9999 已拉新(pid 45708),冒烟:decide 新研判 key_evidence 逐字引用渲染句「反转20=0.217,过去20日下跌21.7%」「20日量比=8.85倍,明显放量」——演习两类误读在同票同模式下不再发生;引擎 PIN 子进程验证过(仓内路径)。坑:/seats/decide 直调必传 date 参数。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立唯一一份「因子字段→中文名/方向/口径/渲染句式」语义字典,所有喂 LLM 的因子值先经确定性代码渲染成带方向语义的中文句子,消灭 0612 演习暴露的两类事故(rev_20=0.217 被读成「20日+20%」实为-21.7%;turnover_20 字段名叫换手实为量比口径)。

**Architecture:** 新模块 `guanlan_v2/factorlib/semantics.py`(纯函数、零依赖)做「数字→语言」唯一出口;`guanlan_v2/seats/api.py` 的研判 fac_line 与条件单 prompt 改为调用它;引擎 fork 内两处(stock_brief 行情行、technical_analyst 英文 prompt)做最小字面量修补(引擎保持自包含,不 import guanlan_v2)。LLM 永远不再见裸字段名。

**Tech Stack:** Python 3.13 / FastAPI 薄壳 / pytest

**硬约束(每个 task 都适用):**
- **本仓无 git 仓库——绝不 git init/commit,所有「提交」步骤替换为「跑全量 pytest」**
- pytest 口径:`& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`(当前基线 **155 绿**)
- 改 python 后 9999 须重启才生效(controller 在收口任务统一做,实现者不要自己杀进程)
- 首次 Write/Edit 某文件会被 GateGuard hook 拦下要求陈述四事实——照要求陈述后重试同一操作即可
- **G:/stocks 只读绝不写**

**口径事实(已核实,engine/financial_analyst/factors/core.py):**
```python
# :80  factors[f"rev_{w}"]  = -_safe_pct_change(close, w)   # 反转 = 负的区间涨跌幅 → 正值=过去w日下跌(超跌)
# :81  factors[f"mom_{w}"]  = _safe_pct_change(close, w)    # 动量 = 区间涨跌幅
# :86  factors[f"turnover_{w}"] = vol[-1]/avg(vol, w)       # ⚠ 字段名叫 turnover,口径实为「量比」(倍数)
# :102 factors[f"ma_diff_{w}"]  = close[-1]/MA(w) - 1
# :108 factors["rsi_14"] = Wilder RSI ∈ [0,100]
# 另:seats/quote 的 vol_ratio = 当日量/10日均量(腾讯实时,与 turnover_20 不同窗口)
```

**0612 演习真实回归样例(中微公司 SH688012, asof 2026-06-11,取自 var/seats_decisions.jsonl):**
`{"rev_20": 0.2170881, "mom_60": -0.0313489, "rsi_14": 22.79383, "ma_diff_20": -0.1907521, "turnover_20": 8.8468891}`
正确读法:过去20日**下跌**21.7%(超跌)、60日累计下跌3.1%、RSI超卖、收盘低于20日均线19.1%、当日量为20日均量8.85倍(放量)。

---

## Task 1: 语义字典+渲染器 `semantics.py`(TDD)

**Files:**
- Create: `tests/test_factor_semantics.py`
- Create: `guanlan_v2/factorlib/semantics.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_factor_semantics.py`,内容如下(完整文件):

```python
"""因子语义契约层(0612演习修复#1)单元测试。

铁律:rev_20 正值=过去20日下跌(字段=负的涨跌幅);turnover_20 是量比口径(倍数)非换手率。
回归样例取自 0612 演习中微公司真实因子值(var/seats_decisions.jsonl)。
"""
from pathlib import Path

from guanlan_v2.factorlib.semantics import FACTOR_SEMANTICS, render_factor, render_factors

# —— 0612 演习真实值(中微公司 SH688012 asof 2026-06-11)——
DRILL_FAC = {"rev_20": 0.2170881, "mom_60": -0.0313489, "rsi_14": 22.79383,
             "ma_diff_20": -0.1907521, "turnover_20": 8.8468891}
DECIDE_FIELDS = ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20")


def test_rev20_positive_means_fell():
    # rev = -pct_change → 0.217 必须渲染成「下跌21.7%」,绝不允许出现「上涨」
    s = render_factor("rev_20", 0.2170881)
    assert "下跌21.7%" in s and "超跌" in s
    assert "上涨" not in s


def test_rev20_negative_means_rose():
    s = render_factor("rev_20", -0.150)
    assert "上涨15.0%" in s and "下跌" not in s


def test_mom60_sign():
    assert "下跌3.1%" in render_factor("mom_60", -0.0313489)
    assert "上涨9.6%" in render_factor("mom_60", 0.096)


def test_rsi_zones():
    assert "超卖" in render_factor("rsi_14", 22.8)
    assert "超买" in render_factor("rsi_14", 78.7)
    s = render_factor("rsi_14", 50.0)
    assert "超卖" not in s and "超买" not in s


def test_ma_diff_direction():
    assert "低于20日均线19.1%" in render_factor("ma_diff_20", -0.1907521)
    assert "高于20日均线10.9%" in render_factor("ma_diff_20", 0.109)


def test_turnover20_is_volume_ratio_not_turnover_rate():
    # 字段名叫 turnover 但口径是量比:8.85 = 当日量为20日均量的8.85倍
    s = render_factor("turnover_20", 8.8468891)
    assert "20日量比" in s and "8.85倍" in s and "放量" in s
    assert "换手" not in s          # 绝不允许再被当成换手率
    assert "缩量" in render_factor("turnover_20", 0.79)


def test_vol_ratio_distinct_from_turnover20():
    # 第二个量比(腾讯实时,10日窗)必须可区分
    s = render_factor("vol_ratio", 1.58)
    assert "实时量比" in s and "10日窗" in s


def test_turnover_rate_labeled():
    s = render_factor("turnover_rate", 2.94)
    assert "换手率" in s and "流通股本" in s


def test_unknown_field_fallback_and_none():
    assert render_factor("mystery_x", 1.5) == "mystery_x=1.5"
    assert render_factor("rev_20", None) == "反转20=—"
    assert render_factor("rev_20", float("nan")) == "反转20=—"


def test_render_factors_drill_regression():
    # 演习整行回归:五字段全渲染、方向全对、无误导词
    line = render_factors(DRILL_FAC, DECIDE_FIELDS)
    for cn in ("反转20", "动量60", "RSI14", "均线乖离20", "20日量比"):
        assert cn in line
    assert "下跌21.7%" in line and "超卖" in line and "8.85倍" in line
    assert "上涨21.7%" not in line


def test_render_factors_skips_missing_gracefully():
    line = render_factors({"rev_20": 0.1}, ("rev_20", "rsi_14"))
    assert "反转20" in line and "RSI14=—" in line


def test_engine_prompt_pins():
    # 契约钉:引擎侧两处字面量修补不被回退(Task 3/4 落地后通过)
    root = Path(__file__).resolve().parents[1] / "engine" / "financial_analyst"
    brief = (root / "buddy" / "tools.py").read_text(encoding="utf-8")
    assert "(10日窗,>1放量·<1缩量)" in brief
    ta = (root / "agent" / "tier2" / "technical_analyst.py").read_text(encoding="utf-8")
    assert "flip its sign" in ta
    assert "mean-reversion DOWN" not in ta
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_factor_semantics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guanlan_v2.factorlib.semantics'`

- [ ] **Step 3: 写实现** — 创建 `guanlan_v2/factorlib/semantics.py`(完整文件):

```python
"""因子语义契约层(0612演习修复#1)。

唯一一份「字段 → 中文名/方向/口径/渲染句式」字典。所有把因子值喂给 LLM 的
prompt 必须经 render_factor()/render_factors() 把裸值渲染成带方向语义的中文,
LLM 永远不直接解读裸字段名(演习事故:rev_20=0.217 被读成"20日+20%"实为-21.7%)。

口径来源 engine/financial_analyst/factors/core.py:
  rev_w  = -pct_change(close, w)   # 正值 = 过去 w 日下跌(超跌)
  mom_w  = +pct_change(close, w)
  turnover_w = vol[-1]/avg(vol,w)  # ⚠ 字段名叫 turnover,口径实为「量比」(倍数)
  ma_diff_w  = close/MA(w)-1;  rsi_14 ∈ [0,100]
另 seats/quote 的 vol_ratio = 当日量/10日均量(腾讯实时,与 turnover_20 不同窗口)。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def _pct(v: float) -> str:
    return f"{abs(v) * 100:.1f}%"


def _fmt(v: float, p: int) -> str:
    return ("%." + str(p) + "f") % v


def _rev_20(v: float) -> str:
    chg = -v                       # 还原真实 20 日涨跌幅
    side = "下跌" if chg < 0 else "上涨"
    if v >= 0.10:
        tag = ",超跌状态"
    elif v <= -0.10:
        tag = ",强势上行"
    else:
        tag = ""
    return f"过去20日{side}{_pct(chg)}{tag}"


def _mom_60(v: float) -> str:
    side = "下跌" if v < 0 else "上涨"
    return f"过去60日累计{side}{_pct(v)}"


def _rsi_14(v: float) -> str:
    if v < 30:
        return "超卖区,<30"
    if v > 70:
        return "超买区,>70"
    return "中性区,30-70"


def _ma_diff_20(v: float) -> str:
    side = "低于" if v < 0 else "高于"
    return f"收盘{side}20日均线{_pct(v)}"


def _vol_tag(v: float) -> str:
    if v >= 1.5:
        return ",明显放量"
    if v <= 0.8:
        return ",缩量"
    return ",量能平稳"


def _turnover_20(v: float) -> str:
    return f"当日量为20日均量的{_fmt(v, 2)}倍{_vol_tag(v)}"


def _vol_ratio(v: float) -> str:
    return f"10日窗{_vol_tag(v)}"


def _turnover_rate(v: float) -> str:  # noqa: ARG001 — 句式与值无关
    return "成交量/流通股本"


FACTOR_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "rev_20":        {"cn": "反转20",     "prec": 3, "explain": _rev_20},
    "mom_60":        {"cn": "动量60",     "prec": 3, "explain": _mom_60},
    "rsi_14":        {"cn": "RSI14",      "prec": 1, "explain": _rsi_14},
    "ma_diff_20":    {"cn": "均线乖离20", "prec": 3, "explain": _ma_diff_20},
    "turnover_20":   {"cn": "20日量比",   "prec": 2, "explain": _turnover_20, "unit": "倍"},
    "vol_ratio":     {"cn": "实时量比",   "prec": 2, "explain": _vol_ratio},
    "turnover_rate": {"cn": "换手率",     "prec": 2, "explain": _turnover_rate, "unit": "%"},
}


def render_factor(field: str, value: Any) -> str:
    """单字段渲染:「中文名=值(方向语义句)」;未知字段诚实回落「field=value」;None/NaN → —。"""
    meta = FACTOR_SEMANTICS.get(field)
    v = _num(value)
    if meta is None:
        return f"{field}={v:g}" if v is not None else f"{field}={value}"
    if v is None:
        return f"{meta['cn']}=—"
    val = _fmt(v, meta["prec"]) + meta.get("unit", "")
    return f"{meta['cn']}={val}({meta['explain'](v)})"


def render_factors(fac: Dict[str, Any], fields: Iterable[str] | None = None) -> str:
    """多字段渲染拼行(分号分隔)。fields 给定则按其顺序(缺失值渲染为 —),否则按 fac 自身顺序。"""
    keys = list(fields) if fields is not None else list(fac)
    return "; ".join(render_factor(k, fac.get(k)) for k in keys)
```

- [ ] **Step 4: 跑测试确认通过(除契约钉)**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_factor_semantics.py -q`
Expected: **12 passed, 1 failed** — 唯一失败是 `test_engine_prompt_pins`(Task 3/4 落地后转绿,属预期,不要为它改实现)

- [ ] **Step 5: 跑全量确认无破坏**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: 155 旧绿全保持 + 新增 12 绿 + 1 失败(契约钉,预期)

## Task 2: seats/api.py 接线(研判 fac_line + live_eval 文档 + 条件单 prompt)

**Files:**
- Modify: `guanlan_v2/seats/api.py:495-502`(fac_line)、`:727`(live_eval docstring)、`:928`(条件单 prompt 量比标注)

- [ ] **Step 1: 替换 fac_line 手写字符串为字典渲染** — `guanlan_v2/seats/api.py` 找到(当前 :498-502):

```python
            fac_line = (f"反转20(20日反转因子,越大越超跌)={_f(fac.get('rev_20'))}; "
                        f"动量60(60日累计收益率)={_f(fac.get('mom_60'))}; "
                        f"RSI14(0-100,<30超卖、>70超买)={_f(fac.get('rsi_14'), 1)}; "
                        f"均线乖离20(收盘/20日均线-1)={_f(fac.get('ma_diff_20'))}; "
                        f"量比20(当日量/20日均量,<1缩量)={_f(fac.get('turnover_20'), 2)}")
```

替换为:

```python
            from guanlan_v2.factorlib.semantics import render_factors
            fac_line = render_factors(
                fac, ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20"))
```

(就地局部 import,与本文件其他懒加载风格一致;紧随其后的 `if mdl.get("combo_pct")...` 追加行**保持不动**。上方 `def _f(...)` 仍被 combo/fm 行使用,不要删。)

- [ ] **Step 2: 修 live_eval docstring 命名陷阱** — `:727` 找到:

```python
        指标语义:maDiff20=收盘/MA20-1(>0 站上 MA20);rsi14∈[0,100];turnover20=量比(<1 缩量)。
```

替换为:

```python
        指标语义:maDiff20=收盘/MA20-1(>0 站上 MA20);rsi14∈[0,100];
        turnover20=20日量比(当日量/20日均量,字段名沿袭叫 turnover 但口径是量比;
        与 volRatio(腾讯实时,10日窗)是两个不同窗口的量比)。
```

- [ ] **Step 3: 条件单 prompt 量比消歧** — `:928` 附近找到:

```python
                         f"RSI14 {_f(ctx.get('rsi14'), 1)} 量比 {_f(ctx.get('volRatio'))} {ind_cn}\n"
```

替换为:

```python
                         f"RSI14 {_f(ctx.get('rsi14'), 1)} 实时量比(10日窗,>1放量) {_f(ctx.get('volRatio'))} {ind_cn}\n"
```

- [ ] **Step 4: 跑全量 pytest**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: 与 Task 1 收尾相同(155+12 绿,契约钉仍 1 失败)。seats 相关旧测试必须全绿。

## Task 3: 引擎 stock_brief 行情行内联语义(最小字面量修补)

**Files:**
- Modify: `engine/financial_analyst/buddy/tools.py:1260-1265`

注意:引擎保持自包含,**不 import guanlan_v2 的 semantics 模块**,只做字面量标注。

- [ ] **Step 1: 修补行情行** — 找到(:1260-1265):

```python
            lines.append(
                f"\n## 行情 (实时)\n{code}: 现价={rt['price']} "
                f"涨跌={rt.get('changePercent')}% 量比={rt.get('vol_ratio')} "
                f"换手={rt.get('turnover_rate')}% 振幅={rt.get('amplitude')}% "
                f"PE={rt.get('pe')} PB={rt.get('pb')}"
            )
```

替换为:

```python
            lines.append(
                f"\n## 行情 (实时)\n{code}: 现价={rt['price']} "
                f"涨跌={rt.get('changePercent')}% 量比={rt.get('vol_ratio')}(10日窗,>1放量·<1缩量) "
                f"换手={rt.get('turnover_rate')}%(成交/流通股本) 振幅={rt.get('amplitude')}% "
                f"PE={rt.get('pe')} PB={rt.get('pb')}"
            )
```

- [ ] **Step 2: 跑契约钉确认推进**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_factor_semantics.py::test_engine_prompt_pins -q`
Expected: 仍 FAIL,但失败断言推进到 technical_analyst 部分(`assert "flip its sign" in ta`)——说明 stock_brief 钉已扎住

## Task 4: 引擎 technical_analyst 英文 prompt 歧义修复

**Files:**
- Modify: `engine/financial_analyst/agent/tier2/technical_analyst.py:23`

- [ ] **Step 1: 替换歧义行** — 找到(:23):

```python
- rev_20 is reversal alpha (positive value = expect mean-reversion DOWN, negative = mean-reversion UP)
```

替换为:

```python
- rev_20 = NEGATIVE of the past-20d return. positive rev_20 => the stock FELL over the past 20 days (oversold; the alpha expects an upward bounce). negative rev_20 => the stock ROSE. NEVER read rev_20 as a return/momentum number; to recover the actual 20d return, flip its sign.
```

- [ ] **Step 2: 契约钉全绿**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_factor_semantics.py -q`
Expected: **13 passed**

- [ ] **Step 3: 跑全量**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: 155 旧绿 + 13 新绿 = **168 绿,0 失败**

## Task 5: v4 方向口径核验(只读,出结论不改码)

**Files:**
- Read only: `guanlan_v2/strategy/compute/v4.py`、`guanlan_v2/strategy/vendor/v4_ranking.py`(若路径不存在用 Grep 找 `TD` / `rev_20` 定位)

- [ ] **Step 1:** 读两文件中 `TF`/`TD` 矩阵与 rev_20 的打分使用处,回答:`TD["rev_20"]=+1` 的语义是「高 rev(超跌)得高分=买超跌(反转多头)」还是隐含变号?与 core.py 的 `rev = -pct_change` 口径连读是否自洽?
- [ ] **Step 2:** 把结论(2-4 句,引用 文件:行号)写进本计划文件末尾「## v4 核验结论」节(Edit 本文件追加)。**若发现口径矛盾:STOP 上报 controller,本计划不修 v4。**

## Task 6: 收口(controller 亲自做,不派 subagent)

- [ ] 全量 pytest 终验(预期 168 绿)
- [ ] 重启 9999:杀监听 PID(`Get-NetTCPConnection -LocalPort 9999 -State Listen` 取 OwningProcess → `Stop-Process -Id <pid> -Force`),看门狗 ~10s 自动拉新;`GET /console/sessions` 200 确认活
- [ ] 真机冒烟①:`POST /seats/decide`(code=SH688012, mode=fast, creed 任意)一次,读 `var/seats_decisions.jsonl` 新条目——rationale 对 20 日方向的表述应与「下跌21.7%/超跌反弹」一致,不再出现「动量强/20日+20%」类反向叙事
- [ ] 真机冒烟②:引擎子进程验 stock_brief 源已带标注(测引擎必须强制仓内路径,venv .pth 指旧仓):
  `& "G:\financial-analyst\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0,'G:/guanlan-v2/engine'); import financial_analyst.buddy.tools as t; src=open(t.__file__,encoding='utf-8').read(); print('PIN OK' if '(10日窗,>1放量·<1缩量)' in src else 'PIN MISS', t.__file__)"`
  Expected: `PIN OK G:\guanlan-v2\engine\...\buddy\tools.py`
- [ ] memory 收口(live-drill 待修#1#2 标已修 + 本计划状态行)

---

## v4 核验结论(Task 5,controller 复核裁决)

**自洽,无矛盾。** v4 自算 `rev_20 = _ref(c,20)/c - 1`(v4.py:85,`_ref=shift` v4.py:45-46)= 过去价/现价-1:股票下跌时为正——与 core.py:80 `-pct_change`(正值=超跌)**同向**(分母不同:core 以过去价为基准、v4 以现价为基准,符号一致幅度略异)。`TD["rev_20"]=+1`(v4.py:32)→ 高 rev(超跌)得高分 = 反转多头,连读自洽。注:首轮 Explore 核验误判「矛盾」系符号算术错误(把"涨20%"算成 rev=+0.20,实为 -0.167),controller 读源裁决推翻。语义字典 `_rev_20` 的「正值=下跌」渲染对两条管线均成立。

## Self-Review(已执行)

- 覆盖:演习两事故(rev 反向/turnover 命名)各有专属测试与接线任务;盘点报告 8 位点中喂 LLM 的 4 处(seats fac_line、seats 条件单、stock_brief、technical_analyst)全覆盖;workflow `_FACTOR_CATALOG` 本身语义完整不动;v4 仅核验。
- 占位符扫描:无 TBD/TODO;所有代码步骤给了完整代码。
- 类型一致:`render_factor(field, value) -> str` / `render_factors(fac, fields) -> str` 全文一致;测试断言与实现输出格式逐字核对(`反转20=—`、`8.85倍`、`(10日窗,>1放量·<1缩量)`)。
