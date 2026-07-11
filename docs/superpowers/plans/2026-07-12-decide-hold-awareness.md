# decide 持仓感知(帷幄智能体化一期·单元三)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落子 decide 链喂持仓上下文(入场价/持有 bar 数/浮盈亏),LLM 买后会喊卖;前端复盘 run 循环维护仓位状态机接线;JudgeCard 接真台账成本。

**Architecture:** 照 order 链先例(seats/api.py:1908-1936 【持仓】块)但走 payload 可选键(decide 是 POST);fast/deep 共用 usr_p 故一处注入两档同吃;前端 runRealThink 循环内维护 LLM 口径仓位状态机(镜像 runBacktest 的 pos/entryPx 语义)。

**Spec:** docs/superpowers/specs/2026-07-12-weiwo-autonomy-runtime-design.md §6

## Global Constraints

- **只喂上下文**:不改 decide 输出 JSON schema、不动 clock 机械止损、不动信号混合 w;无持仓键时 prompt/落盘**逐字节不变**。
- 持仓键全可选:`hold_entry`(float,>0 才认)、`hold_bars`(int,可缺);**不引入 hold_since**(order 链该参数从未被读,死参数勿照抄)。
- 落盘:dec 记录**有值才落键**(与 run_id/source 同模式,旧记录形状不变)。
- watcher 不接线(无持仓状态源,诚实挂账,绝不编造持仓)。
- 提交:逐文件 add;尾注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`;UI 改 jsx 必 bump ?v=(用 Edit)。
- 引擎/后端生效须重启 9999——Task 3 控制器统一做。

## 测绘事实(2026-07-12,实施依据)

- `_decide_impl` @ seats/api.py:575-904;payload 键解析 :583-610;**fast/deep 共用 usr_p**(:808-816,段序:标的/因子/pa_block/经验卡/研报/摘录/配方/市况/pa_method+_ask);`_ask` :802-807;LLM 调用 :821-835;落盘 `_DEC_LOG`。
- order 链【持仓】块 :1908-1936:`held = hold_entry is not None and hold_entry > 0`;`pnl_pct = (price/hold_entry-1)*100`;文案"你已持有该股…继续持有→side 观望;了结卖出→side 卖出并 note 给理由"。
- 前端:`seatDecide` POST /seats/decide @ ui/seats/luozi-data.jsx:1381-1385;三调用点=luozi-app.jsx:330-343(runRealThink 复盘循环,**循环内无仓位状态**,只有 nBuy/nSell/nWatch 计数 :317,347-361)、luozi-panels.jsx:1388-1400(RunDecCard 重跑)、:1505-1517(JudgeCard 手动)。
- 事后重放状态机可镜像:luozi-data.jsx:1507-1563 runBacktest 的 `pos/entryPx/entryIdx`(LLM 口径=纯 side 驱动)。
- 台账:`ledgerState()` @ luozi-data.jsx:1450-1459,positions[] 含 {code,qty,avg_cost}(加权成本,无入场日)。
- 测试模板:tests/test_seats_decide_pa.py 四件套(_CapLLM 捕 usr_p/_DayLoader/_client/_post),新测原样抄隔离手法。

---

### Task 1: 后端 _decide_impl 持仓块(TDD)

**Files:**
- Modify: `guanlan_v2/seats/api.py`(_decide_impl 三处:payload 解析/usr_p 注入/落盘记录)
- Test: `tests/test_seats_decide_hold.py`(新建,抄 test_seats_decide_pa.py 四件套)

**Interfaces:**
- Produces: payload 可选键 `hold_entry: float`(>0 才认)、`hold_bars: int`;usr_p 在【市况】行之后、pa_method_line+_ask 之前插入持仓块;dec 落盘记录有值才落 `hold_entry`/`hold_bars` 键。

- [ ] **Step 1: 写失败测试**(抄 pa 测试四件套后加三测)

```python
def test_hold_injects_block(...):
    r = _post(client, hold_entry=10.0, hold_bars=3)
    assert "【持仓】" in _CAP["user"]
    assert "入场价 10.0" in _CAP["user"] and "持有约 3" in _CAP["user"]
    assert "了结卖出" in _CAP["user"]          # 卖出指引真的进了 prompt
    # 浮盈亏 = 桩行情最后收盘/10.0-1(用 _DayLoader 固定收盘算出精确百分数断言)
    rec = json.loads((tmp_path/"dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["hold_entry"] == 10.0 and rec["hold_bars"] == 3

def test_no_hold_prompt_unchanged(...):
    _post(client)                                # 不带持仓键
    assert "【持仓】" not in _CAP["user"]
    rec = ...最后一行
    assert "hold_entry" not in rec and "hold_bars" not in rec   # 旧记录形状不变

def test_hold_entry_nonpositive_ignored(...):
    _post(client, hold_entry=0)
    assert "【持仓】" not in _CAP["user"]
```

- [ ] **Step 2: RED** → **Step 3: 实现**

payload 解析(:610 后,与 mode 同区):

```python
    # 单元三·持仓感知(2026-07-12):可选键,>0 才认;只喂上下文绝不改输出 schema/clock。
    hold_entry = payload.get("hold_entry")
    try:
        hold_entry = float(hold_entry) if hold_entry is not None else None
    except (TypeError, ValueError):
        hold_entry = None
    held = hold_entry is not None and hold_entry > 0
    hold_bars = payload.get("hold_bars")
    try:
        hold_bars = int(hold_bars) if (held and hold_bars is not None) else None
    except (TypeError, ValueError):
        hold_bars = None
```

usr_p 注入(:815 一带,【市况】行之后):最后收盘价取现成 `fac`/df 末收盘局部量(实施时用 usr_p 已有的 lastClose 来源;若无现成局部量则从 df["close"].iloc[-1] 取,包 try 缺价则浮盈亏显 —):

```python
    hold_line = ""
    if held:
        _pnl = f"{(last_close / hold_entry - 1.0) * 100.0:.2f}%" if last_close else "—"
        hold_line = (f"【持仓】入场价 {hold_entry:g} · 持有约 {hold_bars if hold_bars is not None else '—'} {unit}"
                     f" · 最新收盘 {last_close if last_close else '—'} · 浮动盈亏 {_pnl}\n"
                     "你已持有该股:重点判断【继续持有】还是【了结卖出】——继续持有 → side 填\"观望\";"
                     "了结卖出 → side 填\"卖出\"并在 note 给理由。输出 JSON 结构不变。\n")
```

(`unit` 是 :627 既有局部量"日"/"根30分钟bar",30min 频率自动适配。)落盘记录(与 run_id 同模式):`if held: rec["hold_entry"] = hold_entry` / `if hold_bars is not None: rec["hold_bars"] = hold_bars`。

- [ ] **Step 4: `python -m pytest tests/test_seats_decide_hold.py tests/test_seats_decide_pa.py tests/test_seats_decide_intraday.py -q` 全绿**
- [ ] **Step 5: 提交** `git add guanlan_v2/seats/api.py tests/test_seats_decide_hold.py` + `feat(seats): decide 持仓感知——payload 可选 hold_entry/hold_bars 注【持仓】块,买后会喊卖`

---

### Task 2: 前端接线——runRealThink 仓位状态机 + JudgeCard 台账成本

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(seatDecide 透传 extra)、`ui/seats/luozi-app.jsx`(runRealThink 循环状态机)、`ui/seats/luozi-panels.jsx`(JudgeCard 读台账)、`ui/seats/观澜 · 落子.html`(三 jsx ?v bump,用 Edit)

**实施细则(先 Read 三个 jsx 相关段再动):**
1. `seatDecide`(luozi-data.jsx:1381-1385):加可选末参 `extra`(对象),浅并进 POST body(`...(extra || {})`)——不带时 body 逐字节不变。
2. `runRealThink`(luozi-app.jsx:275-380):循环外 `let pos = 0, entryPx = null, entryIdx = null;`;每次 `lzSeatDecide(...)` 调用时若 `pos === 1` 传 `{hold_entry: entryPx, hold_bars: i - entryIdx}`;拿到决策后按 LLM 口径更新:`side==='买入' && !pos` → `pos=1; entryPx=bar 收盘; entryIdx=i`;`side==='卖出' && pos` → 清零。**只跟 LLM 口径**(与 runBacktest 纯 LLM 臂语义一致,不掺 hybrid w)。
3. `JudgeCard`(luozi-panels.jsx:1486-1518):研判前 `await window.lzLedgerState()`(现成 :1450),在 positions 里按当前 code 找到且 qty>0 → extra 传 `{hold_entry: avg_cost}`(**不传 hold_bars**——台账无入场日,诚实缺省;徽章/文案不动)。找不到/台账未开→不传。
4. RunDecCard 重跑(:1388-1400)不接(历史决策重跑证据,当时持仓状态不可考,诚实不编)。
5. watcher 不接(挂账,无持仓源)。

- [ ] 自检:三处 ?v bump;pos 状态机只在 runRealThink 作用域,不污染全局;无持仓时 payload 无新键。
- [ ] 提交:`git add ui/seats/luozi-data.jsx ui/seats/luozi-app.jsx ui/seats/luozi-panels.jsx "ui/seats/观澜 · 落子.html"` + `feat(luozi-ui): 复盘循环仓位状态机+JudgeCard台账成本——decide持仓感知前端接线`

---

### Task 3: 全量回归 + 真机(控制器亲手)

- [ ] 全量 pytest 全绿;杀 9999 自愈。
- [ ] 真机直 POST /seats/decide 带 `hold_entry`(取一真实票+真日期):dec 落盘含 hold 键、LLM 输出体现持有/卖出权衡(fast 档即可,省钱)。
- [ ] 浏览器 innerText 验落子页 JudgeCard 正常(台账现空仓→不传持仓,行为同旧)。
- [ ] 台账收官;终审;合 main(推远端须再问)。
