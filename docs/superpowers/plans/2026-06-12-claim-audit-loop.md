# 断言质检闭环 Implementation Plan(0612演习修复#2)

> **状态:已执行完毕并验收(2026-06-12)** — CA1-CA3 两段审查全过(CA1 质量审查采纳 \+\d 误报修复[负向前瞻方案,drop 会漏检演习坏样例被 controller 否]、高幅度死区锁定测试、复审残留观察「20日线」缩写一字修 |线;RSI 带 % 应报的裁决维持),pytest **183 绿**(168→183);CA4 核查:演习经验卡 EV-015 **干净**(5 项回测数字全有出处,「20日+20%」错误叙事只存在于会话 condensation 与 decide creed 只读档案,不构成卡污染,第二张研判卡不存在);9999 已拉新;冒烟:真 decide audit_flags=[](好 rationale+creed 止损5% 合法源)、合成坏样例 2 flags(方向矛盾+无出处)双向验真,落盘记录带 audit_flags 字段。注:测试数「12」实为 11+后补 2+1=14(test_claim_audit),计划行文笔误不影响验收。挂账:落子页决策卡 audit_flags 徽章 UI(后端字段已留好)、演习会话 condensation 内残留错误叙事(只读,该会话未来轮 reseed 仍会喂到,建议该会话废弃或下轮人工纠正)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研判产出(rationale/key_evidence)落盘前过确定性断言质检——方向矛盾与无出处百分数打 `audit_flags` 标记(advisory 不阻断),经验卡落库时对 insight 做「数字断言出处」提示,把「真思考+错原料=自信的错误」的最后一道闸装上。

**Architecture:** 新模块 `guanlan_v2/factorlib/claim_audit.py`(纯函数,与 semantics.py 同层):`audit_claims(claims, fac, source)` 查方向矛盾+百分数出处,`unsourced_percents(text, source)` 供经验卡复用。seats/api.py decide 在 `_persist_decision` 前算 flags 入记录+响应;console/tools.py 的 ww_seats_decide content 加 ⚠ 行、cards_save_impl 加 advisory 行。**全 advisory 不阻断**(诚实显形,不挡研判)。

**Tech Stack:** Python 3.13 / pytest;零第三方依赖(re/math)

**硬约束(同修复#1):**
- **本仓无 git——禁止 git 命令,"提交"=跑 pytest**
- pytest 口径:`& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`(当前基线 **168 绿**)
- 改 python 后 9999 重启 controller 收口统一做
- GateGuard 首次 Write/Edit 拦四事实,陈述后重试;用户指令原话:「继续2」

**已核事实:**
- decide LLM 结果组装与落盘:`guanlan_v2/seats/api.py:615-640`(j 解析后 → `_persist_decision("decide", {...})` → JSONResponse);在 scope 内的喂入物:`fac`(dict)、`fac_line`、`card_line`、`res_line`、`res_excerpt`、`rf_line`、`creed`、`regime`
- ww_seats_decide impl:`guanlan_v2/console/tools.py:243-258`(content 由响应字段拼装;`raw: r` 透传)
- cards_save_impl:`guanlan_v2/console/tools.py:319-334`(`_self_post("\cards", {...})`,参数 title/insight/expr/verdict/conf/ic/cat/status)
- cards API:`POST /cards` 是 **upsert**(body.id or next_id,cards/api.py:68-70),`GET /cards/list?status=all`
- 0612 演习两段真实文本作测试样例:
  - **坏样例**(演习选股叙事+creed,被字典层证伪):「动量最强(20日+20%)、业绩爆发式增长、量比2.2放量上攻」(真值:rev_20=0.2170881 即 20日**跌**21.7%)
  - **好样例**(修复#1后冒烟 rationale):「超跌反转因子20日跌幅21.7%,RSI14=22.8处于超卖区,均线乖离-19.1%严重偏离,且20日量比8.85倍显著放量,可能预示反弹。」

---

## Task 1: claim_audit.py + 单元测试(TDD)

**Files:**
- Create: `tests/test_claim_audit.py`
- Create: `guanlan_v2/factorlib/claim_audit.py`

- [ ] **Step 1: 写失败测试** — 创建 `tests/test_claim_audit.py`(完整文件):

```python
"""断言质检(0612演习修复#2)单元测试。

坏样例=演习真实误读文本(20日+20% 实为跌21.7%);好样例=修复#1后真实冒烟 rationale。
质检是 advisory:返回 flags 列表,空=干净;绝不抛异常。
"""
from guanlan_v2.factorlib.claim_audit import audit_claims, unsourced_percents

# 0612 演习中微公司真实因子值
FAC = {"rev_20": 0.2170881, "mom_60": -0.0313489, "rsi_14": 22.79383,
       "ma_diff_20": -0.1907521, "turnover_20": 8.8468891}
# 喂入源(修复#1 后 fac_line 的真实渲染,含全部合法数字)
SRC = ("反转20=0.217(过去20日下跌21.7%,超跌状态); 动量60=-0.031(过去60日累计下跌3.1%); "
       "RSI14=22.8(超卖区,<30); 均线乖离20=-0.191(收盘低于20日均线19.1%); "
       "20日量比=8.85倍(当日量为20日均量的8.85倍,明显放量)")

BAD = "动量最强(20日+20%)、业绩爆发式增长、量比2.2放量上攻"
GOOD = ("超跌反转因子20日跌幅21.7%,RSI14=22.8处于超卖区,均线乖离-19.1%严重偏离,"
        "且20日量比8.85倍显著放量,可能预示反弹。")


def test_drill_bad_text_flagged():
    flags = audit_claims(BAD, FAC, SRC)
    assert any("方向矛盾" in f for f in flags)      # 20日+X% vs 实际下跌
    assert any("无出处" in f for f in flags)        # 20% 不在喂入证据里


def test_drill_good_text_clean():
    assert audit_claims(GOOD, FAC, SRC) == []


def test_direction_rev20_rose_but_text_says_fell():
    flags = audit_claims("近20日下跌明显,弱势", {"rev_20": -0.15}, "")
    assert any("方向矛盾" in f for f in flags)


def test_direction_rsi_contradiction():
    assert any("超买" in f or "方向矛盾" in f
               for f in audit_claims("RSI显示超买,回调风险大", {"rsi_14": 22.8}, ""))
    assert any("超卖" in f or "方向矛盾" in f
               for f in audit_claims("RSI已超卖,可博反弹", {"rsi_14": 78.7}, ""))


def test_direction_ma_diff():
    flags = audit_claims("已站上20日均线,趋势转强", {"ma_diff_20": -0.19}, "")
    assert any("方向矛盾" in f for f in flags)


def test_direction_turnover20():
    flags = audit_claims("20日量比显示放量", {"turnover_20": 0.6}, "")
    assert any("方向矛盾" in f for f in flags)


def test_provenance_creed_numbers_are_legit():
    # 止损/止盈数字来自 creed(在 source 里)→ 不许误报
    assert audit_claims("触发后止损5%、止盈10%", {}, "信条:止损5%止盈10%持有10日") == []


def test_provenance_rounding_tolerated():
    # 21.7% 被复述成约22% 属合理改写,不报;凭空的 35% 要报
    assert audit_claims("近20日跌约22%", FAC, SRC) == []
    flags = audit_claims("该股近期上涨35%", FAC, SRC)
    assert any("35" in f and "无出处" in f for f in flags)


def test_dead_zone_no_false_positive():
    # |rev|<2% 的微小波动不触发方向断言(±0.02 死区)
    assert audit_claims("20日小幅上涨", {"rev_20": 0.01}, "") == []


def test_nan_and_missing_fields_safe():
    assert audit_claims("随便说点什么", {}, "") == []
    assert audit_claims("20日上涨", {"rev_20": float("nan")}, "") == []


def test_unsourced_percents_helper():
    assert unsourced_percents("RankIC 4.8%,年化48%", "ic: RankIC 4.80% · 回测年化48%") == []
    rogue = unsourced_percents("动量20日+20%", "RankIC 4.80%")
    assert rogue and abs(rogue[0] - 20.0) < 1e-9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_claim_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guanlan_v2.factorlib.claim_audit'`

- [ ] **Step 3: 写实现** — 创建 `guanlan_v2/factorlib/claim_audit.py`(完整文件):

```python
"""断言质检(0612演习修复#2)。

对 LLM 研判产出做确定性核查(advisory,不阻断):
① 方向矛盾——文本断言与喂入因子真值方向相反(演习事故:rev_20=0.217 实为跌21.7%,
   LLM 说成"20日+20%");② 百分数出处——文本里的 X% 必须能在喂入证据(source)或
   因子真值里找到(±0.55pp 容差,容忍"21.7%→约22%"的合理改写,抓凭空数字)。
返回 flags 列表(空=干净);任何输入异常都吞掉返回 [](质检绝不挡研判)。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PCT_TOL = 0.55     # 百分数出处容差(百分点):容忍取整改写,抓 ≥0.6pp 的凭空数字
_DIR_DEAD = 0.02    # 方向断言死区:|涨跌幅|<2% 不判方向矛盾


def _v(fac: Dict[str, Any], k: str):
    try:
        x = float(fac.get(k))
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _pcts(text: str) -> List[float]:
    return [float(m.group(1)) for m in _PCT.finditer(text or "")]


def unsourced_percents(text: str, source: str) -> List[float]:
    """text 中无法在 source 找到出处的百分数(±0.55pp)。供经验卡 insight 质检复用。"""
    legit = _pcts(source)
    return [x for x in _pcts(text) if not any(abs(x - p) <= _PCT_TOL for p in legit)]


def audit_claims(claims: str, fac: Dict[str, Any], source: str = "") -> List[str]:
    """研判文本 vs 喂入因子真值+证据源。返回违规描述列表(advisory)。"""
    try:
        flags: List[str] = []
        t = claims or ""
        rev, mom = _v(fac, "rev_20"), _v(fac, "mom_60")
        rsi, mad, t20 = _v(fac, "rsi_14"), _v(fac, "ma_diff_20"), _v(fac, "turnover_20")

        # ① 方向矛盾(模式都限定在量名近旁,降误报)
        if rev is not None and rev >= _DIR_DEAD and re.search(r"20日[^。;,\n]{0,8}(上涨|涨幅|\+\d)", t):
            flags.append(f"方向矛盾:近20日实际下跌{rev * 100:.1f}%,文中称20日上涨")
        if rev is not None and rev <= -_DIR_DEAD and re.search(r"20日[^。;,\n]{0,8}(下跌|跌幅)", t):
            flags.append(f"方向矛盾:近20日实际上涨{-rev * 100:.1f}%,文中称20日下跌")
        if mom is not None and mom >= _DIR_DEAD and re.search(r"60日[^。;,\n]{0,8}(下跌|跌幅)", t):
            flags.append(f"方向矛盾:近60日实际上涨{mom * 100:.1f}%,文中称60日下跌")
        if mom is not None and mom <= -_DIR_DEAD and re.search(r"60日[^。;,\n]{0,8}(上涨|涨幅)", t):
            flags.append(f"方向矛盾:近60日实际下跌{-mom * 100:.1f}%,文中称60日上涨")
        if rsi is not None and rsi < 30 and "超买" in t:
            flags.append(f"方向矛盾:RSI14={rsi:.1f}处于超卖区,文中称超买")
        if rsi is not None and rsi > 70 and "超卖" in t:
            flags.append(f"方向矛盾:RSI14={rsi:.1f}处于超买区,文中称超卖")
        if mad is not None and mad <= -_DIR_DEAD and re.search(r"(站上|高于)20日均线", t):
            flags.append(f"方向矛盾:收盘低于20日均线{-mad * 100:.1f}%,文中称站上均线")
        if mad is not None and mad >= _DIR_DEAD and re.search(r"(跌破|低于)20日均线", t):
            flags.append(f"方向矛盾:收盘高于20日均线{mad * 100:.1f}%,文中称跌破均线")
        if t20 is not None and t20 <= 0.8 and re.search(r"20日量比[^。;,\n]{0,10}放量", t):
            flags.append(f"方向矛盾:20日量比{t20:.2f}缩量,文中称放量")
        if t20 is not None and t20 >= 1.5 and re.search(r"20日量比[^。;,\n]{0,10}缩量", t):
            flags.append(f"方向矛盾:20日量比{t20:.2f}放量,文中称缩量")

        # ② 百分数出处:合法源 = source 文本里的数字 + 因子真值换算的百分数
        legit = _pcts(source)
        for k in ("rev_20", "mom_60", "ma_diff_20"):
            x = _v(fac, k)
            if x is not None:
                legit.append(abs(x) * 100)
        for x in _pcts(t):
            if not any(abs(x - p) <= _PCT_TOL for p in legit):
                flags.append(f"数字{x:g}%在喂入证据中无出处")
        return flags
    except Exception:  # noqa: BLE001 — 质检自身故障绝不挡研判
        return []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests/test_claim_audit.py -q`
Expected: **12 passed**

- [ ] **Step 5: 跑全量**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: **180 passed**(168+12),0 失败

## Task 2: decide 接线(audit_flags 入记录+响应)

**Files:**
- Modify: `guanlan_v2/seats/api.py:615-645` 附近

- [ ] **Step 1: j 解析后、`_persist_decision` 前插入质检**(紧贴 `if not j:` 块之后):

```python
            # 断言质检(修复#2):方向矛盾+无出处百分数 → advisory flags,不阻断
            audit_flags: list = []
            try:
                from guanlan_v2.factorlib.claim_audit import audit_claims
                _claims = " ".join([str(j.get("rationale") or "")]
                                   + [str(x) for x in (j.get("key_evidence") or [])])
                _audit_src = "\n".join([fac_line, card_line, res_line, res_excerpt or "",
                                        rf_line, str(creed or ""), str(regime or "")])
                audit_flags = audit_claims(_claims, fac, _audit_src)
            except Exception:  # noqa: BLE001 — 质检失败不挡研判
                audit_flags = []
```

- [ ] **Step 2: 落盘记录加字段** — `_persist_decision("decide", {...})` 的 dict 里、`"creed": creed,` 之前加一行:

```python
                "audit_flags": audit_flags,
```

- [ ] **Step 3: 响应加字段** — 紧随其后的 `return JSONResponse({...})` 里(`"mode": mode,` 之后)加:

```python
                "audit_flags": audit_flags,
```

(先 Read :637-660 确认 JSONResponse 的实际键序与收口括号,把字段插在 mode 之后即可。)

- [ ] **Step 4: 全量 pytest** — Expected: 180 passed

## Task 3: ww 工具显形(研判 ⚠ 行 + 经验卡 advisory)

**Files:**
- Modify: `guanlan_v2/console/tools.py:243-258`(seats_decide_impl)、`:319-334`(cards_save_impl)
- Modify: `tests/test_console_tools.py`(加 2 测试)

- [ ] **Step 1: seats_decide_impl content 加 ⚠ 行** — `:253-258` 找到:

```python
    return {"ok": True,
            "content": (f"落子研判 {r.get('name')}({r.get('code')}): 方向 {r.get('direction')}"
                        f" · 置信 {r.get('confidence')} · {str(r.get('rationale', ''))[:200]}"),
```

替换为:

```python
    _af = r.get("audit_flags") or []
    _af_line = ("\n⚠ 断言质检 " + str(len(_af)) + " 处: " + "; ".join(str(x) for x in _af[:3])) if _af else ""
    return {"ok": True,
            "content": (f"落子研判 {r.get('name')}({r.get('code')}): 方向 {r.get('direction')}"
                        f" · 置信 {r.get('confidence')} · {str(r.get('rationale', ''))[:200]}"
                        + _af_line),
```

(`artifact`/`raw` 行原样保留。)

- [ ] **Step 2: cards_save_impl 加 insight 数字出处 advisory** — `:333-334` 的成功 return 前插入:

```python
    advisory = ""
    try:
        from guanlan_v2.factorlib.claim_audit import unsourced_percents
        rogue = unsourced_percents(insight, " ".join([title, expr, str(ic or "")]))
        if rogue:
            advisory = (f"\n⚠ insight 含 {len(rogue)} 个未注明出处的数字断言"
                        f"({', '.join(f'{x:g}%' for x in rogue[:3])}),建议核对后再 approve。")
    except Exception:  # noqa: BLE001
        advisory = ""
```

并把成功 return 的 content 改为 `f"经验卡已沉淀: {cid}「{title}」({status})" + advisory`。

- [ ] **Step 3: 加 2 个测试** — `tests/test_console_tools.py` 末尾追加(沿用该文件现有 monkeypatch 风格,先 Read 文件头看 `_self_post`/`_self_get` 怎么 patch 的,保持一致):

```python
def test_seats_decide_content_shows_audit_flags(monkeypatch):
    from guanlan_v2.console import tools as ct
    monkeypatch.setattr(ct, "_self_post", lambda path, body, timeout=180: {
        "ok": True, "code": "SH688012", "name": "中微公司", "direction": "买入",
        "confidence": 85, "rationale": "x", "audit_flags": ["方向矛盾:近20日实际下跌21.7%,文中称20日上涨"]})
    r = ct.seats_decide_impl("SH688012", name="中微公司")
    assert r["ok"] and "断言质检 1 处" in r["content"] and "方向矛盾" in r["content"]


def test_cards_save_advisory_on_unsourced_numbers(monkeypatch):
    from guanlan_v2.console import tools as ct
    monkeypatch.setattr(ct, "_self_post", lambda path, body, timeout=180: {"id": "c_test1"})
    r = ct.cards_save_impl("测试卡", insight="动量20日+20%飙升", ic="RankIC 4.80%")
    assert r["ok"] and "未注明出处" in r["content"]
    r2 = ct.cards_save_impl("测试卡2", insight="RankIC 4.8%稳健", ic="RankIC 4.80%")
    assert r2["ok"] and "未注明出处" not in r2["content"]
```

(若该文件 `_self_post` 签名/patch 方式不同——以真实形状为准调整;若现有测试用 fixture 注入,沿用 fixture。)

- [ ] **Step 4: 全量 pytest** — Expected: **182 passed**(180+2),0 失败

## Task 4: 演习经验卡污染核查(只读判定,必要才修)

**Files:**
- Read only(必要时经 `POST /cards` upsert 修正)

- [ ] **Step 1:** `GET http://127.0.0.1:9999/cards/list?status=all` 找 title 含「动量+流动性共振选股」的卡(0612 演习沉淀;另有一张研判结论卡也一并查),读全文 insight/verdict/ic
- [ ] **Step 2:** 逐数字断言核出处:RankIC 4.8%/4.80%、RankICIR 0.35、年化48%、Sharpe 1.44、回撤-8.1%、样本内外 4.86%→4.67% ← 这些全部来自演习真实工作流回测(合法);**「动量强」叙事按 mom_20 复合因子语义判断**(mom_20 是真动量因子,卡层面"动量强+流动性好"若指因子构成则正确——演习污染发生在选股叙事层"中微20日+20%",预判不在卡里,此卡干净)
- [ ] **Step 3:** 若发现卡内确有与数据矛盾的断言(如把某票的错误涨幅写进了 insight)→ 用 `POST /cards`(带原 id 全字段 upsert)修正该句并在 insight 尾注「(2026-06-12 断言质检修订)」;若干净 → 不动,把判定(卡id+逐断言核对结论)写进本计划末尾「## Task 4 核查结论」节
- [ ] **Step 4:** 本任务只走 HTTP(9999 在跑),不直接动 store 文件

## Task 5: 收口(controller 亲自做)

- [ ] 全量 pytest 终验(预期 182 绿)
- [ ] 重启 9999(杀监听 PID,看门狗拉新,探活 /console/sessions)
- [ ] 真机冒烟:`POST /seats/decide`(SH688012, mode=fast, date=今日, creed 含「止损5%」)→ 响应与 jsonl 新记录带 `audit_flags` 字段(好 rationale 预期 `[]`;若 LLM 这次又编了数字,flags 非空恰证明功能在工作——两种结果都算通过,关键是字段存在且为 list)
- [ ] 合成坏样例直验(不经 LLM):python 调 `audit_claims("动量最强(20日+20%)", {"rev_20":0.217}, "下跌21.7%")` → 非空
- [ ] memory 收口(live-drill 修复#2 落地行 + MEMORY.md 索引行)

---

## Self-Review(已执行)

- 覆盖:深层问题#2 的两个落点(研判落盘前质检、经验卡落库提示)+ 污染核查;UI 徽章(落子页决策卡显示 audit_flags)明确**不在本批**(挂账,后端字段已留好)。
- 占位符扫描:无;所有代码步骤给完整代码;Task 3 测试给"以真实 monkeypatch 风格为准"的适配指引(实现者先读再写,属事实核对非占位)。
- 类型一致:`audit_claims(claims: str, fac: Dict, source: str) -> List[str]`、`unsourced_percents(text: str, source: str) -> List[float]` 全文一致;`audit_flags` 字段名在 persist/response/tool content/测试四处统一。
