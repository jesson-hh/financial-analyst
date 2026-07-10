# A/B 成绩单两缺陷修复(start 取数空 + 测试污染)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 rerank A/B 成绩单第一次算出真数字(缺陷A:`_closes` 误用端点空 `start` 参数),并永久堵死测试向生产 picks 档案的泄漏(缺陷B)+ 成绩单未成熟显形(防反馈回路污染)。

**Architecture:** 三处小修:①`seats/api.py` rerank_ab 分支 `_closes` 显式传每对 `start_d`(绝不闭包捕获——晚绑定坑);②测试补 patch + conftest autouse 护栏;③`ww_rerank_perf` content 加未成熟标记。全部修法已由 4-agent 对抗核验 CONFIRMED 并离线实证(修后 3 真对出数:rs_fa480466ee Δ=+0.0229)。

**Tech Stack:** Python/FastAPI/pytest(零新依赖)。

## Global Constraints

- **展示型红线**:A/B 成绩单只读展示,零信号回写;本修不碰 picks 正式语义(`snapshot_only`/默认 kind 过滤两道边界不动)。
- **诚实显形**:失败/未成熟必须显形;绝不编数。
- **TDD**:回归测试先在旧代码上 RED,修后 GREEN。
- **逐文件 git add,绝不 `git add -A`**(并发 session 误扫有前科)。
- **提交尾注**:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **清污染/重启 9999/真机验证 = 控制器亲手**(Task 2),implementer 绝不碰 `var/` 生产文件、绝不碰 9999。
- 默认路径(无 kind)`codes 与 start 必填` 契约零变化(:2100 校验不动;`test_basket_perf_default_behavior_unchanged` 守护)。
- 分支:`ab-scorecard-fix`(base main)。

---

### Task 1: 修 B(堵泄漏)→ 修 A(TDD)→ 未成熟显形(单 implementer,一次提交)

**Files:**
- Modify: `tests/test_rescore_api.py:34-56`(补 patch + 断言)
- Modify: `tests/conftest.py`(autouse 护栏,照 :27 先例)
- Modify: `guanlan_v2/seats/api.py:2045-2046, 2068, 2081, 2092`(缺陷A 四处)
- Modify: `tests/test_basket_perf.py`(新增录音回归测试 + :114 旧测补桩)
- Modify: `guanlan_v2/console/tools.py:1081-1085`(`_arm_s` 重写)
- Modify: `tests/test_console_tools.py:1802-1810`(fake 补字段 + 断言标记)

**Interfaces:**
- Produces:`/seats/basket_perf?kind=rerank_ab` 每 pair 载荷新增 `"start": start_d`(纯加性);`_closes(c: str, s: str)`(rerank_ab 分支内);ww_rerank_perf content 臂格式 `+X.XX%[·未成熟m/n]` / `失败(reason)` / `无excess(基准缺失)`。
- Consumes(不变):`compute_basket_perf(closes, start, horizon, bench_df)`;`read_picks(limit=500)`;`_drop_unsettled`。

---

- [ ] **Step 1: 修 B —— test_rescore_api.py 补 patch(先堵泄漏源)**

在 `tests/test_rescore_api.py` 的 `test_run_rescore_end_to_end_rows`(:34),`monkeypatch.setattr(rs, "RUNS_PATH", ...)`(:36)之后插入:

```python
    from guanlan_v2.screen import picks as pk
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    monkeypatch.setattr(rs, "_run_rerank_bridge",
                        lambda rows, market: {"ok": False, "reason": "stubbed"})
```

并在 `assert end["stats"]["board_freshness"]...`(:54)之后加:

```python
    assert end["rerank"]["ok"] is False           # 桩真被走到(防重构绕开 rerank 段后静默失覆盖)
```

- [ ] **Step 2: 验证修 B —— 跑该测试且生产档案逐位不变**

```bash
cd /g/guanlan-v2
wc -l var/screen_picks.jsonl        # 记录行数 N
python -m pytest tests/test_rescore_api.py -q
wc -l var/screen_picks.jsonl        # 必须仍 = N(泄漏已堵的硬证据)
```
Expected: 全绿 + 行数逐位不变。

- [ ] **Step 3: conftest autouse 护栏(同类事故一刀切断)**

在 `tests/conftest.py` 的 `_isolate_console_memory`(:27-30)之后加(注意 `append_pick` 自带 `mkdir(parents=True)`,嵌套路径安全;显式 patch 的测试后跑覆盖,优先级更高):

```python
@pytest.fixture(autouse=True)
def _isolate_screen_archives(tmp_path, monkeypatch):
    """测试永不写真 var/ 选股档案(picks/rescore runs;2026-07-10 缺陷B同类事故护栏)。
    各测试自己的显式 monkeypatch.setattr(pk/rs, ...) 在本 fixture 之后生效,优先级更高。"""
    from guanlan_v2.screen import picks as pk
    from guanlan_v2.screen import rescore as rs
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "_isolated_screen" / "picks.jsonl")
    monkeypatch.setattr(rs, "RUNS_PATH", tmp_path / "_isolated_screen" / "runs.jsonl")
```

Run: `python -m pytest tests/test_rescore_api.py tests/test_screen_picks.py tests/test_basket_perf.py tests/test_screen_rerank.py -q`
Expected: PASS(护栏与显式 patch 不冲突)。

- [ ] **Step 4: 写录音式回归测试(旧代码上必须 RED)**

在 `tests/test_basket_perf.py` 的 `_FakeLoader`(:72-77)之后加录音 loader,在 `test_basket_perf_rerank_ab_pairs` 之后加新测试:

```python
class _RecordingLoader:
    """录下每次 fetch_quote 收到的 start 实参——钉死『取数起始日=每对 pick ts』。"""
    def __init__(self):
        self.calls = []

    def fetch_quote(self, code, start, end, freq):
        self.calls.append((str(code), str(start)))
        return pd.DataFrame({"trade_date": [d for d, _ in _SER],
                             "close": [v for _, v in _SER]})
```

```python
def test_rerank_ab_uses_per_pair_start(tmp_path, monkeypatch, client):
    """缺陷A 的精确反面:取数起始日=每对 pick ts;查询参数 start 被忽略(docstring 契约)。"""
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    from guanlan_v2.screen import picks as pk
    rec = _RecordingLoader()
    monkeypatch.setattr(_lf, "get_default_loader", lambda: rec)
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    ts = "2026-06-01T18:00:00"                      # 早于 _SER 首根 06-02 → 首根即入场
    for arm in ("data", "rerank"):
        pk.append_pick({"kind": "rerank_ab", "arm": arm, "codes": ["SH600001"],
                        "run_id": "rs_a", "ts": ts, "snapshot": False})
    r = client.get("/seats/basket_perf",
                   params={"kind": "rerank_ab", "limit": 5, "start": "2099-01-01"}).json()
    assert r["ok"] and r["n"] == 1
    pair = r["pairs"][0]
    assert pair["start"] == "2026-06-01"                              # 载荷显形实际起始日
    assert rec.calls and all(s == "2026-06-01" for _, s in rec.calls)  # 每次取数都用对内 ts,2099 被忽略
    for arm in ("data", "rerank"):
        a = pair["arms"][arm]
        assert a["ok"] is True and a["per_code"][0]["entry_date"] == "2026-06-02"
    assert pair["excess_diff"] == pytest.approx(0.0)                  # 两臂同码 → 恒等
```

Run: `python -m pytest tests/test_basket_perf.py::test_rerank_ab_uses_per_pair_start -q`
Expected: **FAIL**(旧代码:`pair["start"]` KeyError;且录到的 start 全是 "2099-01-01"、两臂 ok:false)。

- [ ] **Step 5: 修 A —— seats/api.py 四处(显式传参,绝不闭包捕获 start_d)**

`guanlan_v2/seats/api.py` rerank_ab 分支:

(1) :2045-2046 签名+取数:
```python
                def _closes(c: str, s: str):
                    df = loader.fetch_quote(c, str(s), end, "day")
```
(其余函数体不动。)

(2) :2068 `start_d = str(arms["data"].get("ts") or "")[:10]` 之后加防御:
```python
                    if not start_d:
                        continue                 # ts 缺失=空窗必空对,诚实跳过
```

(3) :2081 调用处传参:
```python
                                closes[cc] = await asyncio.to_thread(_closes, cc, start_d)
```

(4) :2092 pair 载荷显形实际起始日(纯加性字段):
```python
                    pairs.append({"run_id": rid, "ts": arms["data"].get("ts"),
                                  "start": start_d, "arms": out_arms, "excess_diff": diff})
```

**默认路径 :2114 的同名 `_closes` 一个字都不要动**(其 start 有 :2100 必填校验保护)。

Run: `python -m pytest tests/test_basket_perf.py::test_rerank_ab_uses_per_pair_start -q`
Expected: PASS。

- [ ] **Step 6: 旧测 :114 补桩(防修 A 后变真网络慢测)**

`test_basket_perf_rerank_ab_pairs`(:114)函数体开头插入:

```python
    import financial_analyst.data.loader_factory as _lf
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(EQ, "load_eqw_ret", lambda: _BENCH)
```

(其 ts=2026-07-01、codes=SH600000/SZ000001,`_FakeLoader` 只认 600001 → 两臂 ok:false 如实并列,与 :128 原注释语义一致,现有断言原样成立。)

Run: `python -m pytest tests/test_basket_perf.py -q`
Expected: 全绿。

- [ ] **Step 7: ww_rerank_perf 未成熟显形(防反馈回路污染)**

`guanlan_v2/console/tools.py` `rerank_perf_impl` 内 `_arm_s`(:1081-1085)整体替换为:

```python
        def _arm_s(a: Dict[str, Any]) -> str:
            if not a.get("ok"):
                return f"失败({a.get('reason', '?')})"
            ex = a.get("excess")
            s = f"{float(ex):+.2%}" if isinstance(ex, (int, float)) else "无excess(基准缺失)"
            n, mn = a.get("n"), a.get("matured_n")
            if isinstance(n, int) and isinstance(mn, int) and mn < n:
                s += f"·未成熟{mn}/{n}"
            return s
```

(修三点:①ok:false 的 reason 默认值从误导性 `'未成熟'` 改 `'?'`;②excess=None 而 ok:true = 基准缺失,如实命名;③matured_n<n 追加未成熟标记——agent 只看 content,未标注的未成熟数字会经蒸馏+召回反哺固化成永久记忆。)

同步 `tests/test_console_tools.py:1804-1810` 的 fake 臂补字段并加断言:

```python
    fake = {"ok": True, "kind": "rerank_ab", "n": 1, "pairs": [
        {"run_id": "rs_a", "ts": "2026-07-01T18:00:00", "excess_diff": 0.021,
         "arms": {"data": {"ok": True, "excess": -0.01, "n": 5, "matured_n": 0},
                  "rerank": {"ok": True, "excess": 0.011, "n": 5, "matured_n": 0}}}]}
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: fake)   # 桥打桩
    r = ct.rerank_perf_impl(limit=5)
    assert r["ok"] and "rs_a" in r["content"] and "+2.1pp" in r["content"]
    assert "未成熟0/5" in r["content"]                                   # 未成熟显形(防蒸馏未熟数字)
```

Run: `python -m pytest tests/test_console_tools.py -k rerank -q`
Expected: PASS。

- [ ] **Step 8: 全量回归 + 档案逐位不变(泄漏已堵硬证据)**

```bash
cd /g/guanlan-v2
python - <<'PY'
import hashlib, pathlib
for f in ("var/screen_picks.jsonl", "var/rescore_runs.jsonl"):
    print(f, hashlib.md5(pathlib.Path(f).read_bytes()).hexdigest())
PY
python -m pytest -q
# 再跑一遍上面的 md5 —— 两文件哈希必须逐位相同
```
Expected: 全量绿(基线 1088)+ 两档案 md5 前后一致。若有非本改动的既有红(环境漂移),记录并诚实标注。

- [ ] **Step 9: 提交(逐文件 add)**

```bash
cd /g/guanlan-v2
git add guanlan_v2/seats/api.py guanlan_v2/console/tools.py tests/test_rescore_api.py tests/test_basket_perf.py tests/test_console_tools.py tests/conftest.py
git commit -m "fix(ab): 成绩单per-pair start取数+测试泄漏双堵+未成熟显形

缺陷A: rerank_ab 分支 _closes 误用端点空 start 参数(该分支契约=忽略 start)致
fetch_quote 恒 0 根、excess_diff 恒 null——改显式传每对 start_d(绝不闭包捕获,防晚
绑定),pair 载荷加 start 显形;录音式回归测试钉死契约。缺陷B: test_rescore_api 漏
patch PICKS_PATH/_run_rerank_bridge 致每跑套件真调 DeepSeek+污染真档——补桩+conftest
autouse 护栏一刀切断。ww_rerank_perf 未成熟标记防未熟数字经蒸馏+召回反哺固化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 清污染 + 重启 9999 + 真机验证(控制器亲手,不派 subagent)

1. **清污染**(必须在 Task 1 修 B 落地后):备份 `var/screen_picks.jsonl` → `.bak-YYYYMMDD`;utf-8 逐行过滤,删除谓词 = `kind=="rerank_ab" and run_id=="rs_t1"`,**每条被删行断言 `codes==["SH1","SH2"]`**(安全不变量;绝不硬断言 92 计数——行数是活的);同卷临时文件 + `os.replace` 原子替换;删后自检 rerank_ab 恰 6 行 3 完整对、全文件无 rs_t1。免停 9999(无并发写者:日跑链死、append_pick 即开即关)。
2. **重启 9999**(仅因修 A 是服务器代码,uvicorn 无热重载):杀进程,check_9999.ps1 代际链自动拉起,验 `/health` 200。
3. **真机只读验证**:`GET /seats/basket_perf?kind=rerank_ab&limit=5` → n=3、rs_fa480466ee excess_diff=+0.0229、另两对 0.0(与离线探针逐位对)、每对 `start=2026-07-05`、matured 全 false;顺看响应延迟(~40 次本地二进制 fetch_quote,应远低于 30s);再经真 `_wrap` 信封走 `ww_rerank_perf` 确认 content 渲染 3 行且带 `未成熟0/N` 标记(MEMORY 教训:数据型 ww 工具必验信封级)。
4. 台账/记忆收尾。

## Self-Review

**1. 覆盖**:核验报告的全部 blocking/important 项都有步骤(A 四处→Step 5;B→Step 1;:114 连带→Step 6;护栏→Step 3;未成熟显形→Step 7;录音测试三断言→Step 4;档案不变性→Step 2/8;清污染/重启/真机→Task 2)。**未纳入**(有意,YAGNI/另立):calibration `_closes` 无 NaN 过滤(另一缺陷家族)、read_picks 尾窗 500 行长期滚出问题(样本攒起来之前不构成风险)、全局断网 pytest-socket(副作用面未验证)。
**2. 占位符**:无;所有代码可直接粘贴。
**3. 类型一致**:`_closes(c: str, s: str)` 与 `to_thread(_closes, cc, start_d)` 一致;pair `start` 字段与录音测试断言一致;`_arm_s` 读 `n/matured_n` 与 compute_basket_perf 返回键(basket_perf.py:56)一致;fake 臂补的正是这两键。
