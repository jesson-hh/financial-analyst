# 已验证 TA 指标库 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有 `guanlan_v2/factorlib` 加一个已验证的 TA 指标族(family=`ta`),并把验证过的"概念→完整因子表达式"喂给经验卡「炼」步骤,修正先前"MACD 无法量化"的错误 grounding。

**Architecture:** 沿用 `factorlib` 既有机制——`base/*.json` 由 `register_all()` 启动时校验+编译+注册进引擎运行期 zoo registry(不改 `engine/`、不改 `server.py`)。新增一个 `base/ta_indicators.json`(用 `sma(x,n,m)=EMA` 等算子重建 TA 指标)。验证靠 `scripts/verify_ta_indicators.py` 逐条 POST `/factor/report` 断言 `status=ok`(入库门禁)。消费靠 `refine.py` 读该 JSON 把范例注入 `SYSTEM_PROMPT`。

**Tech Stack:** Python 3.13、FastAPI(薄壳)、vendored `financial_analyst` 引擎(`factors/zoo/expr.py` 的 `validate_expr`/`compile_factor`、`operators.py` 的 `sma`/`cross`/`ts_min`/`ts_max`…)、pytest、deepseek(炼,live 烟测)。

---

## 关键事实(实现者须先读)

- **引擎在仓内** `G:/guanlan-v2/engine/financial_analyst/`。venv 里另有一份**旧分支**的可编辑安装会遮蔽它。本计划的单测**只用 `validate_expr`**(纯字符串禁词检查,版本无关)+ Python `compile()` + 自带白名单,**不依赖** `cross` 等新算子是否在已安装副本里——所以单测不受遮蔽影响。真正"能否在面板上算出 KPI"由 **live 的 `/factor/report`**(走在仓 engine 的运行中 9999 服务)把关。
- **9999 后端在跑**(后台任务 `bpn33nfs9`)。改 `refine.py` / 新增 JSON 后,要**重启**才会被服务加载;`register_all()` 启动时自动拾取 `base/*.json`,**无需改 `server.py`**。
- **硬约束**:不改 `engine/`、不 push、不合 main。沿用 `cards`/`seats`/`factorlib` 既有模式。环境已有 `DEEPSEEK_API_KEY` + `HTTPS_PROXY=127.0.0.1:7890`。
- **运行测试**(PowerShell 或 bash,cwd=仓根):
  `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q`(PYTHONPATH 已含仓根;若否,前置 `$env:PYTHONPATH="G:/guanlan-v2"`)。
- **commit 政策**:每个 Task 末尾给出 commit 步骤,但**执行时是否真 commit 由用户拍板**(用户规矩:不 push、不合 main;且"没让提交先别提交")。先把 commit 步骤写在计划里,执行阶段再确认。

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `guanlan_v2/factorlib/base/ta_indicators.json` | TA 指标族数据(name/family/expr/description/source) | 新增 |
| `tests/test_ta_indicators.py` | TA JSON 快速门禁(schema/禁词/语法/白名单,不碰数据) | 新增 |
| `scripts/verify_ta_indicators.py` | live 验证:逐条 POST /factor/report 断言 status=ok,出台账 | 新增 |
| `guanlan_v2/cards/refine.py` | 读 ta_indicators.json,把范例注入 SYSTEM_PROMPT | 改(追加 `_load_ta_examples`) |
| `guanlan_v2/cards/factor_dsl_kb.md` | 修正 §二(删"MACD 无法量化",只留 OBV/CCI/SAR 真缺口) | 改(替换 §二) |
| `tests/test_cards_refine.py` | 新增 TA 注入断言 + 更新 §二 断言 | 改 |
| `guanlan_v2/factorlib/README.md` | 加 TA 族台账 | 改(追加一节) |
| `ui/cards/README.md`、`docs/module_map.md` | 状态/开放项同步 | 改(小幅) |

---

## Task 1: TA 指标 JSON + 快速门禁单测

**Files:**
- Create: `tests/test_ta_indicators.py`
- Create: `guanlan_v2/factorlib/base/ta_indicators.json`

- [ ] **Step 1: 写失败测试** — `tests/test_ta_indicators.py`

```python
# tests/test_ta_indicators.py
# TA 指标库(guanlan_v2/factorlib/base/ta_indicators.json)的快速门禁:
# 不碰数据、不连服务器 —— schema + 无禁词 + Python 语法可解析 + 仅用引擎白名单名。
# 真·能否在真实面板上算出 KPI,由 scripts/verify_ta_indicators.py(POST /factor/report)把关。
import ast
import json
import sys
from pathlib import Path

# 优先用在仓 engine/(venv 里的可编辑安装是旧分支)。validate_expr 版本无关,
# 这里只是确保引擎可导入;真正运行期算子由 live /factor/report 用在仓 engine 校验。
_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.factors.zoo.expr import validate_expr  # noqa: E402

_TA_JSON = _REPO / "guanlan_v2" / "factorlib" / "base" / "ta_indicators.json"

# 引擎 compile_factor 受限命名空间允许的名字(字段 + 算子),见 engine/.../factors/zoo/expr.py。
_ALLOWED_NAMES = {
    "close", "open", "high", "low", "volume", "vwap", "amount", "returns", "industry",
    "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate",
    "rank", "scale", "ts_sum", "ts_mean", "stddev", "ts_max", "ts_min",
    "ts_argmax", "ts_argmin", "ts_rank", "delta", "delay", "correlation",
    "covariance", "decay_linear", "sma", "wma", "signedpower", "log", "sign",
    "abs", "abs_", "product", "power", "indneutralize", "max_pair", "min_pair",
    "filter_where", "cross",
}


def _entries():
    return json.loads(_TA_JSON.read_text(encoding="utf-8"))


def test_json_exists_and_nonempty():
    assert _TA_JSON.is_file(), f"缺 {_TA_JSON}"
    assert len(_entries()) >= 15


def test_every_entry_has_required_fields():
    for e in _entries():
        assert e.get("name", "").startswith("ta_"), f"name 不合规: {e}"
        assert e.get("family") == "ta", f"family 必须 ta: {e}"
        assert e.get("expr", "").strip(), f"expr 为空: {e}"
        assert e.get("description", "").strip(), f"description 为空: {e}"


def test_names_unique():
    names = [e["name"] for e in _entries()]
    assert len(names) == len(set(names)), "name 有重复"


def test_expr_passes_validate_and_syntax():
    for e in _entries():
        validate_expr(e["expr"])                      # 无 __ / import / lambda
        compile(e["expr"], f"<{e['name']}>", "eval")   # Python 语法可解析


def test_expr_only_uses_whitelisted_names():
    for e in _entries():
        tree = ast.parse(e["expr"], mode="eval")
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        illegal = used - _ALLOWED_NAMES
        assert not illegal, f"{e['name']} 用了清单外名字: {illegal}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_ta_indicators.py -q`
Expected: FAIL —`test_json_exists_and_nonempty` 报 `缺 .../ta_indicators.json`(文件还没建)。

- [ ] **Step 3: 建 `guanlan_v2/factorlib/base/ta_indicators.json`**

```json
[
  {"name": "ta_macd_dif", "family": "ta", "expr": "sma(close,13,2) - sma(close,27,2)", "description": "MACD DIF:EMA12−EMA26(EMA(P)=sma(x,P+1,2))", "source": "ta-textbook/macd"},
  {"name": "ta_macd_dea", "family": "ta", "expr": "sma(sma(close,13,2) - sma(close,27,2),10,2)", "description": "MACD DEA:DIF 的 9 日 EMA", "source": "ta-textbook/macd"},
  {"name": "ta_macd_hist", "family": "ta", "expr": "2*((sma(close,13,2) - sma(close,27,2)) - sma(sma(close,13,2) - sma(close,27,2),10,2))", "description": "MACD 柱:2×(DIF−DEA)", "source": "ta-textbook/macd"},
  {"name": "ta_macd_golden_cross", "family": "ta", "expr": "cross(sma(close,13,2) - sma(close,27,2), sma(sma(close,13,2) - sma(close,27,2),10,2))", "description": "MACD 金叉:DIF 上穿 DEA(0/1)", "source": "ta-textbook/macd"},
  {"name": "ta_macd_dead_cross", "family": "ta", "expr": "cross(sma(sma(close,13,2) - sma(close,27,2),10,2), sma(close,13,2) - sma(close,27,2))", "description": "MACD 死叉:DEA 上穿 DIF(0/1)", "source": "ta-textbook/macd"},
  {"name": "ta_rsi6", "family": "ta", "expr": "100*sma(max_pair(delta(close,1),0),6,1)/(sma(max_pair(delta(close,1),0),6,1)+sma(max_pair(-delta(close,1),0),6,1)+1e-8)", "description": "RSI(6):Wilder 平滑相对强弱", "source": "ta-textbook/rsi"},
  {"name": "ta_rsi12", "family": "ta", "expr": "100*sma(max_pair(delta(close,1),0),12,1)/(sma(max_pair(delta(close,1),0),12,1)+sma(max_pair(-delta(close,1),0),12,1)+1e-8)", "description": "RSI(12)", "source": "ta-textbook/rsi"},
  {"name": "ta_rsi14", "family": "ta", "expr": "100*sma(max_pair(delta(close,1),0),14,1)/(sma(max_pair(delta(close,1),0),14,1)+sma(max_pair(-delta(close,1),0),14,1)+1e-8)", "description": "RSI(14)", "source": "ta-textbook/rsi"},
  {"name": "ta_rsi24", "family": "ta", "expr": "100*sma(max_pair(delta(close,1),0),24,1)/(sma(max_pair(delta(close,1),0),24,1)+sma(max_pair(-delta(close,1),0),24,1)+1e-8)", "description": "RSI(24)", "source": "ta-textbook/rsi"},
  {"name": "ta_kdj_k", "family": "ta", "expr": "sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1)", "description": "KDJ-K:RSV 的 3 日平滑", "source": "ta-textbook/kdj"},
  {"name": "ta_kdj_d", "family": "ta", "expr": "sma(sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1),3,1)", "description": "KDJ-D:K 的 3 日平滑", "source": "ta-textbook/kdj"},
  {"name": "ta_kdj_j", "family": "ta", "expr": "3*sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1)-2*sma(sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1),3,1)", "description": "KDJ-J:3K−2D", "source": "ta-textbook/kdj"},
  {"name": "ta_kdj_golden_cross", "family": "ta", "expr": "cross(sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1), sma(sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1),3,1))", "description": "KDJ 金叉:K 上穿 D(0/1)", "source": "ta-textbook/kdj"},
  {"name": "ta_boll_pctb", "family": "ta", "expr": "(close - ts_mean(close,20) + 2*stddev(close,20))/(4*stddev(close,20)+1e-8)", "description": "布林 %B:close 在(下轨,上轨)内位置", "source": "ta-textbook/boll"},
  {"name": "ta_boll_bandwidth", "family": "ta", "expr": "(4*stddev(close,20))/(ts_mean(close,20)+1e-8)", "description": "布林带宽:(上轨−下轨)/中轨", "source": "ta-textbook/boll"},
  {"name": "ta_boll_upper_break", "family": "ta", "expr": "cross(close, ts_mean(close,20)+2*stddev(close,20))", "description": "上穿布林上轨(0/1)", "source": "ta-textbook/boll"},
  {"name": "ta_wr14", "family": "ta", "expr": "-100*(ts_max(high,14)-close)/(ts_max(high,14)-ts_min(low,14)+1e-8)", "description": "威廉 %R(14):0~−100,越接近 0 越强", "source": "ta-textbook/wr"},
  {"name": "ta_bias20", "family": "ta", "expr": "(close-ts_mean(close,20))/(ts_mean(close,20)+1e-8)", "description": "乖离率(20):close 偏离 MA20 的比例", "source": "ta-textbook/bias"},
  {"name": "ta_roc20", "family": "ta", "expr": "close/(delay(close,20)+1e-8) - 1", "description": "变动率(20):20 日涨跌幅", "source": "ta-textbook/roc"},
  {"name": "ta_atr14", "family": "ta", "expr": "ts_mean(max_pair(max_pair(high-low, abs(high-delay(close,1))), abs(low-delay(close,1))),14)", "description": "平均真实波幅(14)", "source": "ta-textbook/atr"}
]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_ta_indicators.py -q`
Expected: PASS(5 个测试)。若 `test_expr_only_uses_whitelisted_names` 报某名字非法 → 那条 expr 用了白名单外字段,修 JSON。

- [ ] **Step 5: Commit**(执行时先与用户确认)

```bash
git add tests/test_ta_indicators.py guanlan_v2/factorlib/base/ta_indicators.json
git commit -m "feat(factorlib): add verified TA indicator family (ta_*) + JSON contract tests"
```

---

## Task 2: live 验证脚本(入库门禁 /factor/report status=ok)

**Files:**
- Create: `scripts/verify_ta_indicators.py`

- [ ] **Step 1: 写脚本** — `scripts/verify_ta_indicators.py`

```python
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
```

- [ ] **Step 2: 跑脚本(需 9999 在跑)**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe scripts/verify_ta_indicators.py`
Expected: 每条打印 `[ok ] ta_xxx ic=... cov=...`,末尾 `ledger: 22 ok / 0 bad / 22 total`,退出码 0。
(MACD金叉 / RSI14 / KDJ-K 三条已预先实测 status=ok;其余用同族算子,预期 ok。)

- [ ] **Step 3: 处理不通过项(若有)**

若某条 `[BAD]`:读 `error` → 多半是误用清单外字段或括号问题 → 修 `ta_indicators.json` 那条 expr;若该指标确实拼不出 → 从 JSON 删除并记到 Task 6 的"诚实跳过"台账。改完重跑 Step 2,直到 `0 bad`。再跑 `pytest tests/test_ta_indicators.py -q` 确认门禁仍绿。

- [ ] **Step 4: Commit**(执行时先与用户确认)

```bash
git add scripts/verify_ta_indicators.py guanlan_v2/factorlib/base/ta_indicators.json
git commit -m "feat(factorlib): add live /factor/report verifier for TA indicators (status=ok gate)"
```

---

## Task 3: 「炼」注入 TA 范例(refine.py)

**Files:**
- Modify: `guanlan_v2/cards/refine.py`(在 `_DSL_KB` 追加块之后加 `_load_ta_examples` + 追加 `SYSTEM_PROMPT`)
- Test: `tests/test_cards_refine.py`(新增一个测试函数)

- [ ] **Step 1: 写失败测试** — 追加到 `tests/test_cards_refine.py` 末尾

```python
def test_system_prompt_includes_ta_indicator_examples():
    # 已验证 TA 指标库范例已注入 prompt:模型能照着写 MACD/RSI/KDJ 的可编译 expr
    assert "TA 指标范例" in SYSTEM_PROMPT
    assert "sma(close,13,2)" in SYSTEM_PROMPT          # MACD DIF 范例(EMA12)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_cards_refine.py::test_system_prompt_includes_ta_indicator_examples -q`
Expected: FAIL —`"TA 指标范例"` 不在 SYSTEM_PROMPT(还没注入)。

- [ ] **Step 3: 改 `guanlan_v2/cards/refine.py`** — 在现有 `_DSL_KB` 块(`SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n---\n" + _DSL_KB` 那段)**之后、`_PATCH_KEYS = (...)` 之前**插入:

```python
def _load_ta_examples() -> str:
    """从已验证 TA 指标库(factorlib/base/ta_indicators.json)生成「概念→expr」范例块。

    只读 JSON(库里只放 /factor/report status=ok 的条目),不连引擎、不碰数据。
    缺文件/坏 JSON → 返回空串(grounding 退化,不崩)。
    """
    import json
    from pathlib import Path
    fp = Path(__file__).resolve().parent.parent / "factorlib" / "base" / "ta_indicators.json"
    try:
        entries = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return ""
    lines = [f"- {e.get('description') or e['name']}: `{e['expr']}`"
             for e in entries if e.get("family") == "ta" and e.get("expr")]
    if not lines:
        return ""
    return ("## TA 指标范例(已 /factor/report 实测 status=ok,照着仿写)\n"
            "本引擎用 `sma(x,n,m)`=EMA(α=m/n,故 P 日 EMA=`sma(x,P+1,2)`)等算子可重建常见技术指标:\n"
            + "\n".join(lines))


# 把已验证 TA 指标范例追加进 system prompt(在通用 KB 之后)。
_TA_EXAMPLES = _load_ta_examples()
if _TA_EXAMPLES:
    SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n---\n" + _TA_EXAMPLES
```

- [ ] **Step 4: 跑测试确认通过**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_cards_refine.py -q`
Expected: PASS(含新测试 + 既有 refine 测试不回归)。

- [ ] **Step 5: Commit**(执行时先与用户确认)

```bash
git add guanlan_v2/cards/refine.py tests/test_cards_refine.py
git commit -m "feat(cards): inject verified TA indicator examples into refine system prompt"
```

---

## Task 4: 修正 factor_dsl_kb.md §二(删错误的"MACD 无法量化")

**Files:**
- Modify: `guanlan_v2/cards/factor_dsl_kb.md`(整段替换 §二)
- Test: `tests/test_cards_refine.py`(更新 `test_system_prompt_includes_concept_dsl_kb`)

- [ ] **Step 1: 更新测试到新契约** — 把 `tests/test_cards_refine.py` 里的 `test_system_prompt_includes_concept_dsl_kb` **整体替换**为:

```python
def test_system_prompt_includes_concept_dsl_kb():
    # §一 通用 alpha 范例 + §三 组合规则 仍在
    assert "cross(ts_mean(close,5), ts_mean(close,20))" in SYSTEM_PROMPT  # 均线金叉范例
    assert "truth value" in SYSTEM_PROMPT                                 # 组合规则(别用 and/or)
    # §二 已修正:技术指标大多可重建;只有 OBV/CCI/SAR 是真缺口、expr 留空
    assert "OBV" in SYSTEM_PROMPT and "CCI" in SYSTEM_PROMPT and "SAR" in SYSTEM_PROMPT
    assert "留空" in SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测试确认仍过(防误删 §一/§三)**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_cards_refine.py::test_system_prompt_includes_concept_dsl_kb -q`
Expected: PASS(此刻 §二 旧文里 OBV/CCI/SAR 也在,断言已满足;本步只是把测试改成新契约,下一步换文不破它)。

- [ ] **Step 3: 替换 `guanlan_v2/cards/factor_dsl_kb.md` 的 §二**

把这段(旧 §二,标题 `## 二、本引擎【不支持】的指标 …` 到该节结束、§三标题之前):

````markdown
## 二、本引擎【不支持】的指标 —— 遇到就把 expr 留空,并在 reply 说明"本引擎无该指标,无法量化"

**没有** `macd` `signal` `dif` `dea` `kdj` `rsi` `boll`(布林)`obv` `wr`(威廉)`cci` `sar` 这些字段/函数,写了**必定编译报错**。

- **「金叉」只有均线版可表达**(见上 `cross(ts_mean(close,5), ts_mean(close,20))`);
  **MACD 版的金叉 / 强金叉 / 弱金叉本引擎无法表达 → `expr` 留空**(强/弱金叉本质是 MACD 的 DIF 上穿 DEA、看零轴上下,本引擎没有 MACD,算不了)。
- 任何需要清单外字段/指标的经验,一律 `expr` 留空字符串,reply 写清"该指标本引擎暂不支持,无法量化"——**不要硬编不存在的字段**(如 `macd`/`ret`/`close_price`)。
````

替换为:

````markdown
## 二、技术指标(MACD / RSI / KDJ / BOLL / WR 等)—— 大多可重建,见「TA 指标范例」块

本引擎虽无 `macd`/`dif`/`dea`/`kdj`/`rsi`/`boll` 这类**现成命名**字段,但有 `sma(x,n,m)`(GTJA 递归平滑 = EMA,平滑系数 α = m/n,故 **P 日 EMA = `sma(x,P+1,2)`**)外加 `ts_min`/`ts_max`/`ts_mean`/`stddev`/`delta`/`max_pair`/`cross`,**可重建大部分技术指标**。常见指标的**已验证写法**见下方「TA 指标范例」块,照着仿写,别自创 `macd`/`dif` 等不存在的字段。

例:MACD 金叉 `cross(sma(close,13,2)-sma(close,27,2), sma(sma(close,13,2)-sma(close,27,2),10,2))`;RSI / KDJ / BOLL / WR 同理可拼(见范例块)。

**真正无法表达的(缺底层原语,遇到就 `expr` 留空,并在 reply 说明缺哪种原语):**
- `OBV`:缺"自上市累加"的 expanding cumsum;
- `CCI`:缺平均绝对偏差(mean absolute deviation);
- `SAR`:缺抛物线递归(path-dependent)。

需要上述三类原语的经验,一律 `expr` 留空,reply 写"该指标缺 XX 原语,本引擎暂不支持量化"——不要硬编不存在的字段(如 `obv`/`cci`/`ret`)。
````

- [ ] **Step 4: 跑全部 refine 测试**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_cards_refine.py -q`
Expected: PASS(§一/§三 断言、新 §二 断言、TA 注入断言全过)。

- [ ] **Step 5: Commit**(执行时先与用户确认)

```bash
git add guanlan_v2/cards/factor_dsl_kb.md tests/test_cards_refine.py
git commit -m "fix(cards): correct factor_dsl_kb section 2 — TA indicators reconstructible; only OBV/CCI/SAR are true gaps"
```

---

## Task 5: 重启后端 + live 集成验证(注册 + 炼烟测)

**Files:** 无(运行期验证)

- [ ] **Step 1: 重启 9999(加载新 JSON + 新 prompt)**

停掉旧后台任务 `bpn33nfs9`,重启:
Run(后台 bash):`GUANLAN_PORT=9999 PYTHONPATH=/g/guanlan-v2 G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.server`
等启动日志出现 `[guanlan_v2] factorlib: registered N / ...`(N 应比之前多 22,即 TA 全部注册)。

- [ ] **Step 2: 验证 TA 已注册进引擎 /factor/list**

Run: `curl -s -m 20 http://127.0.0.1:9999/factorlib/list | python -c "import sys,json;d=json.load(sys.stdin);ta=[f for f in d['factors'] if f['family']=='ta'];print('ta count',len(ta));print('all valid',all(f.get('valid') for f in ta));print([f['name'] for f in ta if not f.get('valid')])"`
Expected: `ta count 22` / `all valid True` / `[]`(无 invalid)。

- [ ] **Step 3: 验证 live /factor/report 门禁全绿**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe scripts/verify_ta_indicators.py`
Expected: `ledger: 22 ok / 0 bad / 22 total`,退出码 0。

- [ ] **Step 4: 炼烟测(真 deepseek):MACD 应写出可编译 expr,OBV 应诚实留空**

Run(MACD,应得 sma 重建):
`curl -s -m 60 -X POST http://127.0.0.1:9999/cards/refine -H "Content-Type: application/json" -d '{"draft":{"name":"MACD金叉","insight":"DIF上穿DEA看多"},"instruction":"给这条经验写个因子表达式"}'`
Expected: 返回 JSON,`patch.expr` 含 `sma(close,13,2)`/`cross(...)` 之类**白名单**写法(非 `macd`)。把该 expr 再 POST `/factor/report` 应 `status=ok`。

Run(OBV,应诚实留空):
`curl -s -m 60 -X POST http://127.0.0.1:9999/cards/refine -H "Content-Type: application/json" -d '{"draft":{"name":"OBV放量","insight":"OBV创新高"},"instruction":"给这条经验写个因子表达式"}'`
Expected: `patch.expr` 为空串,`reply` 说明"OBV 缺 expanding cumsum / 本引擎暂不支持"。

- [ ] **Step 5: 无需 commit**(纯验证步;若烟测暴露 prompt 问题,回 Task 3/4 修)

---

## Task 6: 文档同步 + 全量回归

**Files:**
- Modify: `guanlan_v2/factorlib/README.md`(追加 TA 族台账一节)
- Modify: `ui/cards/README.md`、`docs/module_map.md`(状态/开放项小幅)

- [ ] **Step 1: factorlib/README.md 追加 TA 族台账** — 在 `### mined/(1 条占位 …)` 一节之后插入:

````markdown
### base/ta_indicators.json(TA 指标族,family=`ta`)

技术指标用引擎算子重建(非引擎缺能力,只是先前没写进库):`sma(x,n,m)` = GTJA 递归 EMA(α=m/n,P 日 EMA=`sma(x,P+1,2)`),配合 `ts_min`/`ts_max`/`stddev`/`delta`/`max_pair`/`cross`。
**入库门禁**:每条经 `scripts/verify_ta_indicators.py` POST `/factor/report` 实测 `status=ok` 才收;台账见该脚本输出。

| 族 | 条目 |
|---|---|
| MACD | `ta_macd_dif` `ta_macd_dea` `ta_macd_hist` `ta_macd_golden_cross` `ta_macd_dead_cross` |
| RSI | `ta_rsi6` `ta_rsi12` `ta_rsi14` `ta_rsi24` |
| KDJ | `ta_kdj_k` `ta_kdj_d` `ta_kdj_j` `ta_kdj_golden_cross` |
| BOLL | `ta_boll_pctb` `ta_boll_bandwidth` `ta_boll_upper_break` |
| 其他 | `ta_wr14` `ta_bias20` `ta_roc20` `ta_atr14` |

**真缺口(未收,缺底层原语)**:`OBV`(expanding cumsum)、`CCI`(平均绝对偏差)、`SAR`(抛物线递归)。
**消费**:`register_all()` 启动注册进引擎 zoo(出现在 `/factor/list`);并由 `guanlan_v2/cards/refine.py` 读取,把范例注入「炼」的 system prompt。
````

> 注:若 Task 2/3 有条目被剔除,这里的清单与计数同步改成实际入库集合。

- [ ] **Step 2: ui/cards/README.md 状态同步** — 在"状态/开放项"里加一行:

```markdown
- 炼·因子表达式 grounding 已扩到「已验证 TA 指标库」(factorlib `ta_*`,22 条经 /factor/report 实测):MACD/RSI/KDJ/BOLL/WR/BIAS/ROC/ATR 可由 `sma`=EMA 等重建;真缺口仅 OBV/CCI/SAR。
```

- [ ] **Step 3: docs/module_map.md 同步** — 在 factorlib / cards 相关条目补一句"含 TA 指标族(`ta_*`),供炼的因子表达式 grounding"。(按该文件现有行文风格,改最贴近的一两行,不重排版。)

- [ ] **Step 4: 全量回归**

Run: `& G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全绿(原 83 + 新 test_ta_indicators 5 + 新 refine 1 ≈ 89 passed)。

- [ ] **Step 5: Commit**(执行时先与用户确认)

```bash
git add guanlan_v2/factorlib/README.md ui/cards/README.md docs/module_map.md
git commit -m "docs: record TA indicator family ledger + cards grounding status"
```

---

## 自查(写计划者已核对)

- **Spec 覆盖**:§4.A 作者层→Task1;§4.B 验证层→Task2(+Task1 快速门禁);§4.C 注册→Task5、炼 grounding→Task3、§二修正→Task4;§8 文档→Task6;§9 验收 1→Task5.2、验收 2→Task5.4、验收 3→Task2/5.3、验收 4→Task6.4。无遗漏。
- **占位扫描**:无 TBD/TODO;每个改代码的步骤都给了完整代码或精确替换块。
- **类型/名称一致**:JSON 字段 `name/family/expr/description/source` 全程一致;`_load_ta_examples` 读的 `family=="ta"` 与 JSON 写入一致;测试断言的 `"sma(close,13,2)"`、`"TA 指标范例"`、`OBV/CCI/SAR` 与 refine/KB 实际文案一致;`_ALLOWED_NAMES` 与 `engine/.../expr.py` 的 compile_factor 命名空间一致。
- **风险**:Task5 依赖 9999 重启与真 deepseek;若某 TA 条目 live 不过(Task2),按 Step3 剔除并同步 Task6 计数——计划已含该回路。
```
