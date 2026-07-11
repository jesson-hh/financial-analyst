# 思考预算层(帷幄智能体化一期·单元一)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"思考预算"变成 LLM 座席的一等配置(max_tokens/timeout/模型档位),行业重排升 deep 档(deepseek-reasoner),A/B 档案标模型代次,帷幄长轮加 token 预算闸。

**Architecture:** 底座 engine/financial_analyst/llm/client.py 增座席级预算字段解析+chat() 透传(全部现有调用逐字节不变);config/llm.yaml agent_overrides 扩预算字段;screen/llm.py `_call_llm_json` 座席化后 rerank 换 `agent="rerank"` 座席;A/B 代次沿 rescore 落档→seats 成绩单→ww_rerank_perf 三级透传;BuddyAgent 加 turn 级 completion-token 预算闸(默认关)。

**Tech Stack:** Python 3 / FastAPI / openai AsyncOpenAI(per-request `timeout`/`max_tokens` 请求项)/ pytest。

**Spec:** docs/superpowers/specs/2026-07-12-weiwo-autonomy-runtime-design.md(§3 单元一)

## 真机探针结论(2026-07-12,计划的硬输入,不得推翻)

- `models.list()` 真 SKU=`deepseek-v4-flash`/`deepseek-v4-pro`;**别名 `deepseek-reasoner`/`deepseek-chat` 全部可用**。
- `deepseek-reasoner` 六项全通:plain / `response_format=json_object` / tools / `max_tokens` / `temperature` / **per-request `timeout`**;返回带 `reasoning_content`(json 模式也带);`deepseek-chat` reasoning_len=0。
- 结论:deep 档直接走现有 json_object 路径,**无需**正则抢救 fallback。

## Global Constraints

- 红线:重排仍是展示型——数据榜/正式 picks/blend/seats 信号逐字节不变;`kind=rerank_ab`+`snapshot=False` 双隔离不动。
- 不带预算字段的座席行为**逐字节不变**(kwargs 不得出现 max_tokens/timeout 键)。
- `CONSOLE_TURN_TOKEN_BUDGET` 默认 0=关,不改现有交互行为;预算耗尽必须诚实显形,绝不静默截断。
- 提交:逐文件 `git add`(绝不 `-A`);尾注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 测试:每任务跑本任务测试文件 + 收官全量 `python -m pytest tests/ -q` 必须全绿。
- 引擎(engine/)改动生效需重启 9999——由 Task 6(控制器亲手)统一做,子任务不碰生产进程。

---

### Task 1: client.py 座席预算字段 + chat() 透传(TDD)

**Files:**
- Modify: `engine/financial_analyst/llm/client.py`
- Test: `tests/test_llm_budget.py`(新建)

**Interfaces:**
- Produces: `LLMClient.default_max_tokens: int|None`、`LLMClient.default_timeout: float|None`;`chat(..., max_tokens=None, timeout=None)`(显式参 > 座席默认 > 不传)。后续任务(rerank 座席、autonomy)全靠这两个属性生效。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_budget.py
# -*- coding: utf-8 -*-
"""单元一 Task1:座席预算字段(max_tokens/timeout)解析 + chat() 透传。
红线:不带预算字段的座席 kwargs 不得出现 max_tokens/timeout 键(现有缝逐字节不变)。"""
import asyncio

import pytest

from financial_analyst.llm import client as C

CFG = {
    "default_provider": "deepseek", "default_model": "deepseek-chat",
    "providers": {"deepseek": {"api_key_env": "X_KEY", "base_url": "http://local",
                               "network_profile": "domestic",
                               "models": ["deepseek-chat", "deepseek-reasoner"]}},
    "agent_overrides": {
        "rerank": {"provider": "deepseek", "model": "deepseek-reasoner",
                   "max_tokens": 8192, "timeout": 300},
    },
}


class _FakeResp:
    def model_dump(self):
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}


class _FakeCompletions:
    def __init__(self, rec):
        self._rec = rec

    async def create(self, **kw):
        self._rec.append(kw)
        return _FakeResp()


class _FakeChat:
    def __init__(self, rec):
        self.completions = _FakeCompletions(rec)


class _FakeOpenAI:
    def __init__(self, rec):
        self.chat = _FakeChat(rec)


@pytest.fixture()
def fake_openai(monkeypatch):
    rec = []
    monkeypatch.setattr(C, "_get_openai_compat_client",
                        lambda *a, **k: _FakeOpenAI(rec))
    monkeypatch.setattr(C, "load_llm_config", lambda path=None: CFG)
    return rec


def test_for_agent_parses_budget_fields(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    assert c.model == "deepseek-reasoner"
    assert c.default_max_tokens == 8192
    assert c.default_timeout == 300.0


def test_for_agent_without_budget_is_none(fake_openai):
    c = C.LLMClient.for_agent("screen")
    assert c.default_max_tokens is None and c.default_timeout is None


def test_chat_passes_seat_budget(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert kw["max_tokens"] == 8192 and kw["timeout"] == 300.0


def test_chat_without_budget_omits_keys(fake_openai):
    c = C.LLMClient.for_agent("screen")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}]))
    kw = fake_openai[-1]
    assert "max_tokens" not in kw and "timeout" not in kw


def test_explicit_arg_beats_seat_default(fake_openai):
    c = C.LLMClient.for_agent("rerank")
    asyncio.run(c.chat([{"role": "user", "content": "hi"}], max_tokens=1024, timeout=60))
    kw = fake_openai[-1]
    assert kw["max_tokens"] == 1024 and kw["timeout"] == 60.0


def test_with_overrides_carries_budget(fake_openai):
    c = C.LLMClient.for_agent("rerank").with_overrides(model="deepseek-chat")
    assert c.default_max_tokens == 8192 and c.default_timeout == 300.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_llm_budget.py -q`
Expected: FAIL(`default_max_tokens` 属性不存在 / kwargs 无 max_tokens)

- [ ] **Step 3: 最小实现**

`engine/financial_analyst/llm/client.py` 四处改:

① `__init__`(:105)加 keyword 参数并存属性(注释:座席级思考预算,2026-07-12 单元一):

```python
    def __init__(self, provider: str, model: str, config: Dict[str, Any],
                 max_tokens: Optional[int] = None, timeout: Optional[float] = None):
        ...
        # 座席级思考预算(2026-07-12 思考预算层):None=不传该 kwargs,行为与旧版逐字节一致。
        self.default_max_tokens: Optional[int] = int(max_tokens) if max_tokens is not None else None
        self.default_timeout: Optional[float] = float(timeout) if timeout is not None else None
```

② `for_agent`(:130)解析 override 的 `max_tokens`/`timeout`:

```python
        override = config.get("agent_overrides", {}).get(agent_name, {})
        provider = override.get("provider", config["default_provider"])
        model = override.get("model", config["default_model"])
        return cls(provider=provider, model=model, config=config,
                   max_tokens=override.get("max_tokens"),
                   timeout=override.get("timeout"))
```

③ `with_overrides`(:138)构造新实例时带 `max_tokens=self.default_max_tokens, timeout=self.default_timeout`。

④ `chat`(:279)加 `max_tokens: Optional[int] = None, timeout: Optional[float] = None`,
计算 `eff_mt = max_tokens if max_tokens is not None else self.default_max_tokens`(timeout 同理),
下传 `_chat_openai_compat` / `_chat_litellm`(两函数签名各加这两个参数);两条路径内:

```python
        if eff_mt is not None:
            kwargs["max_tokens"] = int(eff_mt)
        if eff_timeout is not None:
            kwargs["timeout"] = float(eff_timeout)   # openai SDK per-request 项,压过 httpx client 默认 120s
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_llm_budget.py -q` → 6 passed

- [ ] **Step 5: 提交**

```bash
git add engine/financial_analyst/llm/client.py tests/test_llm_budget.py
git commit -m "feat(llm): 座席级思考预算 max_tokens/timeout——for_agent 解析+chat 透传,无预算座席逐字节不变"
```

---

### Task 2: config/llm.yaml 思考档位 + schema/路由守护测试(TDD)

**Files:**
- Modify: `config/llm.yaml`
- Test: `tests/test_llm_config_guard.py`(新建)

**Interfaces:**
- Produces: 座席名 `rerank` / `review_officer` / `review_section`(单元二消费后两个)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_config_guard.py
# -*- coding: utf-8 -*-
"""座席档位 schema 守护:字段白名单/模型在册/类型合法;FA_CONFIG_DIR 路由钉死。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_KEYS = {"provider", "model", "max_tokens", "timeout"}


def _cfg():
    with open(ROOT / "config" / "llm.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_overrides_schema_and_models_in_register():
    cfg = _cfg()
    providers = cfg["providers"]
    for name, ov in (cfg.get("agent_overrides") or {}).items():
        assert set(ov) <= ALLOWED_KEYS, f"{name} 有未知字段: {set(ov) - ALLOWED_KEYS}"
        prov = ov.get("provider", cfg["default_provider"])
        assert prov in providers, f"{name} provider 不在册"
        assert ov.get("model", cfg["default_model"]) in providers[prov]["models"], f"{name} model 不在册"
        if "max_tokens" in ov:
            assert isinstance(ov["max_tokens"], int) and ov["max_tokens"] > 0
        if "timeout" in ov:
            assert isinstance(ov["timeout"], (int, float)) and ov["timeout"] > 0


def test_deep_tier_seats_exist():
    ov = _cfg().get("agent_overrides") or {}
    for seat in ("rerank", "review_officer"):
        assert ov.get(seat, {}).get("model") == "deepseek-reasoner", f"{seat} 应为 deep 档"
        assert ov[seat].get("timeout", 0) >= 180, f"{seat} deep 档须放宽超时(reasoner 思考 1-3 分钟)"
    assert ov.get("review_section", {}).get("model") == "deepseek-chat"


def test_fa_config_dir_routes_to_repo():
    """9999 进程模型路由钉死:server._CONFIG_DIR 必须指向仓内 config/ 且 llm.yaml 存在
    (server.py create_app 对 FA_CONFIG_DIR setdefault 到它;2026-07-12 审计批判环坐实)。"""
    from guanlan_v2 import server
    assert server._CONFIG_DIR == ROOT / "config"
    assert (server._CONFIG_DIR / "llm.yaml").is_file()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_llm_config_guard.py -q`
Expected: `test_deep_tier_seats_exist` FAIL(座席未定义);其余两条应 PASS(既有事实)。

- [ ] **Step 3: 改 config/llm.yaml**

`agent_overrides:` 块替换为(保留 industry_extract 原行与文件其余部分):

```yaml
# agent_overrides (2026-06-01 清零后重启用):
# - industry_extract (2026-07-03): AI投研看板研报抽取走 Kimi k2.6 (256K ctx, 用户指定读研报模型).
# - 思考档位 (2026-07-12 思考预算层): deep=deepseek-reasoner+max_tokens+长超时(判断密集型缝),
#   fast=deepseek-chat(填表型缝, 默认). 真机探针(2026-07-12): reasoner 别名支持
#   json_object/tools/max_tokens/per-request timeout 且返回 reasoning_content;
#   models.list()=[deepseek-v4-flash, deepseek-v4-pro], 别名可用.
#   其余 agent 仍走 default_provider (deepseek/deepseek-chat).
agent_overrides:
  industry_extract: {provider: kimi, model: kimi-k2.6}
  rerank:           {provider: deepseek, model: deepseek-reasoner, max_tokens: 8192, timeout: 300}
  review_officer:   {provider: deepseek, model: deepseek-reasoner, max_tokens: 8192, timeout: 300}
  review_section:   {provider: deepseek, model: deepseek-chat}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_llm_config_guard.py -q` → 3 passed

- [ ] **Step 5: 提交**

```bash
git add config/llm.yaml tests/test_llm_config_guard.py
git commit -m "feat(config): 思考档位座席 rerank/review_officer(deep)+review_section(fast)+schema守护"
```

---

### Task 3: rerank 升 deep 档——`_call_llm_json` 座席化(TDD)

**Files:**
- Modify: `guanlan_v2/screen/llm.py`、`guanlan_v2/screen/rerank.py:46-50`
- Test: `tests/test_screen_llm_seat.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `default_timeout`、Task 2 的 `rerank` 座席。
- Produces: `_call_llm_json(system, user, *, timeout=45.0, temperature=0.2, agent="screen")`;
  纯函数 `_effective_timeout(seat_timeout, fallback) -> float`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_screen_llm_seat.py
# -*- coding: utf-8 -*-
"""_call_llm_json 座席化:agent 参数透传 for_agent;座席超时 > 调用方 fallback。"""
import asyncio

from guanlan_v2.screen import llm as sl


class _FakeClient:
    def __init__(self, default_timeout=None):
        self.provider, self.model = "deepseek", "deepseek-reasoner"
        self.total_tokens, self.default_timeout = 7, default_timeout

    async def chat(self, messages, response_format=None, temperature=0.2):
        return {"choices": [{"message": {"content": '{"x": 1}'}}]}


def test_effective_timeout_prefers_seat():
    assert sl._effective_timeout(300.0, 45.0) == 305.0   # 座席超时 +5s 缓冲(让 SDK 先抛)
    assert sl._effective_timeout(None, 45.0) == 45.0     # 无座席预算=旧行为逐字节不变


def test_call_llm_json_passes_agent(monkeypatch):
    seen = {}

    def _fa(agent_name, config_path=None):
        seen["agent"] = agent_name
        return _FakeClient(default_timeout=300.0)

    import financial_analyst.llm.client as ec
    monkeypatch.setattr(ec.LLMClient, "for_agent", staticmethod(_fa))
    r = asyncio.run(sl._call_llm_json("s", "u", agent="rerank"))
    assert seen["agent"] == "rerank"
    assert r["ok"] is True and r["model"] == "deepseek/deepseek-reasoner"


def test_call_llm_json_default_agent_is_screen(monkeypatch):
    seen = {}

    def _fa(agent_name, config_path=None):
        seen["agent"] = agent_name
        return _FakeClient()

    import financial_analyst.llm.client as ec
    monkeypatch.setattr(ec.LLMClient, "for_agent", staticmethod(_fa))
    asyncio.run(sl._call_llm_json("s", "u"))
    assert seen["agent"] == "screen"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_screen_llm_seat.py -q`
Expected: FAIL(`_effective_timeout` 不存在 / `agent` 参数不存在)

- [ ] **Step 3: 实现**

`guanlan_v2/screen/llm.py`:在 `_call_llm_json` 前加纯函数,并改签名/内部:

```python
def _effective_timeout(seat_timeout, fallback: float) -> float:
    """座席配置了 timeout(deep 档)→ 用它 +5s 缓冲(让 SDK per-request 超时先抛,错误信息更准);
    否则用调用方 fallback(旧行为逐字节不变)。"""
    return float(seat_timeout) + 5.0 if seat_timeout else float(fallback)


async def _call_llm_json(system: str, user: str, *, timeout: float = 45.0,
                         temperature: float = 0.2, agent: str = "screen") -> Dict[str, Any]:
```

内部两处:`LLMClient.for_agent(agent, config_path=LLM_CONFIG_PATH)`;
`eff = _effective_timeout(getattr(client, "default_timeout", None), timeout)` 后
`asyncio.wait_for(..., timeout=eff)`,超时 reason 用 `int(eff)`。

`guanlan_v2/screen/rerank.py:50`:

```python
    return asyncio.run(_call_llm_json(system, user, timeout=120.0, temperature=0.2, agent="rerank"))
```

(`timeout=120.0` 保留=座席缺配置时的兜底;座席在册时 `_effective_timeout` 取 305s。)

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_screen_llm_seat.py tests/test_screen_rerank.py -q` → 全绿(rerank 既有测试打桩 `_call_llm`,不受影响,跑它是回归确认)

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/llm.py guanlan_v2/screen/rerank.py tests/test_screen_llm_seat.py
git commit -m "feat(rerank): 升 deep 档——_call_llm_json 座席化 agent=rerank,座席超时优先"
```

---

### Task 4: A/B 档案模型代次三级透传(TDD)

**Files:**
- Modify: `guanlan_v2/screen/rescore.py:73-84`(_record_rerank_ab)、`guanlan_v2/seats/api.py`(rerank_ab 分支 pairs 载荷,约 :2104 后)、`guanlan_v2/console/tools.py:1090-1093`(rerank_perf_impl 行渲染)
- Test: `tests/test_rescore_api.py`(加一测)、`tests/test_basket_perf.py`(改 rerank_ab 对测试)、`tests/test_console_tools.py`(改渲染测试)

**Interfaces:**
- Consumes: `run_rerank` 成功 dict 已含 `model`(rerank.py:158,Task 3 后值=deepseek/deepseek-reasoner)。
- Produces: picks 行(仅 rerank 臂)`model` 字段;`/seats/basket_perf?kind=rerank_ab` pair 载荷 `model` 字段;ww_rerank_perf 行尾 ` · <model>`。

- [ ] **Step 1: 写失败测试(三处各一)**

tests/test_rescore_api.py 追加:

```python
def test_record_rerank_ab_stamps_model(tmp_path, monkeypatch):
    """代次标注:rerank 臂行带 model,data 臂(无 LLM)不带。"""
    from guanlan_v2.screen import picks as pk, rescore as rs
    monkeypatch.setattr(pk, "PICKS_PATH", tmp_path / "picks.jsonl")
    rows = [{"code": f"SH60000{i}"} for i in range(3)]
    rk = {"ok": True, "model": "deepseek/deepseek-reasoner",
          "rows": [{"code": r["code"], "rank_after": 3 - i} for i, r in enumerate(rows)]}
    rs._record_rerank_ab("rs_test", rows, rk, top_n=3)
    got = {r["arm"]: r for r in pk.read_picks(limit=10)}
    assert got["rerank"].get("model") == "deepseek/deepseek-reasoner"
    assert "model" not in got["data"]
```

tests/test_basket_perf.py 的 rerank_ab 对测试:假 picks 行 rerank 臂加 `"model": "deepseek/deepseek-reasoner"`,断言响应 `pairs[0]["model"] == "deepseek/deepseek-reasoner"`。

tests/test_console_tools.py 的 rerank_perf 渲染测试:假 pair 加 `"model": "deepseek/deepseek-reasoner"`,断言 content 行含 `· deepseek/deepseek-reasoner`;另一无 model 的 pair 行不含 `· deepseek`(缺失不渲染)。

- [ ] **Step 2: 跑三文件确认新断言失败**

Run: `python -m pytest tests/test_rescore_api.py tests/test_basket_perf.py tests/test_console_tools.py -q`

- [ ] **Step 3: 实现三处**

rescore.py `_record_rerank_ab` 循环体改:

```python
    for arm, codes in (("data", data_codes), ("rerank", rr_codes)):
        row = {"kind": "rerank_ab", "arm": arm, "codes": codes,
               "run_id": run_id, "ts": ts, "snapshot": False}
        if arm == "rerank" and rk.get("model"):
            row["model"] = str(rk["model"])   # 代次标注:升档=换处理组,跨代次不混合归因
        append_pick(row)
```

seats/api.py rerank_ab 分支 `pairs.append({...})` 载荷加一键:`"model": arms["rerank"].get("model"),`。

console/tools.py rerank_perf_impl 行渲染(:1092-1093)改:

```python
        mdl = p.get("model")
        lines.append(f"{p.get('run_id')} · {ts} · data臂 {_arm_s(data_arm)} · "
                     f"rerank臂 {_arm_s(rerank_arm)} · Δ={diff_s}"
                     + (f" · {mdl}" if mdl else ""))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_rescore_api.py tests/test_basket_perf.py tests/test_console_tools.py -q` → 全绿

- [ ] **Step 5: 提交**

```bash
git add guanlan_v2/screen/rescore.py guanlan_v2/seats/api.py guanlan_v2/console/tools.py tests/test_rescore_api.py tests/test_basket_perf.py tests/test_console_tools.py
git commit -m "feat(rerank-ab): 模型代次三级透传——picks落档/basket_perf载荷/ww_rerank_perf显形"
```

---

### Task 5: 帷幄长轮 token 预算闸(TDD)

**Files:**
- Modify: `engine/financial_analyst/buddy/agent.py`(BuddyAgent.__init__ :198、run_turn 循环 :336)、`guanlan_v2/console/api.py:117-121`(_default_agent_factory)
- Test: `tests/test_buddy_token_budget.py`(新建)

**Interfaces:**
- Produces: `BuddyAgent(turn_token_budget: int = 0)`;模块级纯函数 `_budget_verdict(budget, spent, iteration, warned) -> str`("ok"/"warn"/"stop");env `CONSOLE_TURN_TOKEN_BUDGET`(默认 0=关)。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_buddy_token_budget.py
# -*- coding: utf-8 -*-
"""帷幄长轮 token 预算闸:纯函数判定矩阵 + 工厂 env 解析。默认 0=关,行为逐字节不变。"""
import importlib

from financial_analyst.buddy.agent import BuddyAgent, _budget_verdict


def test_verdict_matrix():
    assert _budget_verdict(0, 10 ** 9, 5, False) == "ok"      # 预算关=永不干预
    assert _budget_verdict(1000, 100, 3, False) == "ok"
    assert _budget_verdict(1000, 850, 3, False) == "warn"     # ≥80% 注入收敛提示
    assert _budget_verdict(1000, 850, 3, True) == "ok"        # 已警告过不重复
    assert _budget_verdict(1000, 1000, 3, False) == "stop"    # 耗尽=停循环诚实显形
    assert _budget_verdict(1000, 1200, 3, True) == "stop"
    assert _budget_verdict(1000, 1200, 0, False) == "ok"      # 首轮永不拦(至少答一次)


def test_agent_default_budget_off():
    a = BuddyAgent(system_prompt="t")
    assert a.turn_token_budget == 0


def test_factory_reads_env(monkeypatch):
    monkeypatch.setenv("CONSOLE_TURN_TOKEN_BUDGET", "50000")
    from guanlan_v2.console import api as capi
    a = capi._default_agent_factory("sid-test")
    assert a.turn_token_budget == 50000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_buddy_token_budget.py -q`
Expected: FAIL(`_budget_verdict` 不存在)

- [ ] **Step 3: 实现**

agent.py 模块级(BuddyAgent 类外)加:

```python
def _budget_verdict(budget: int, spent: int, iteration: int, warned: bool) -> str:
    """turn 级 completion-token 预算判定。0=关;首轮(iteration=0)永不拦(至少答一次);
    ≥100%→stop(诚实显形停循环);≥80% 且未警告→warn(注入收敛提示)。"""
    if not budget or iteration <= 0:
        return "ok"
    if spent >= budget:
        return "stop"
    if not warned and spent >= int(budget * 0.8):
        return "warn"
    return "ok"
```

`__init__` 加参数 `turn_token_budget: int = 0`,`self.turn_token_budget = max(0, int(turn_token_budget))`(docstring 注明:无人值守夜跑安全门,2026-07-12)。

`run_turn` 循环头(`for iteration in range(self.max_tool_iters):` 之后、LLM 调用之前)加:

```python
            spent = self._client.total_completion_tokens - _budget_start
            verdict = _budget_verdict(self.turn_token_budget, spent, iteration, _budget_warned)
            if verdict == "stop":
                yield TurnEvent("error",
                                f"token 预算耗尽({spent}/{self.turn_token_budget}):停止工具循环,"
                                f"以上为已完成部分(诚实截停,非完整答案)。")
                break
            if verdict == "warn":
                _budget_warned = True
                self.messages.append(Message(role="user", content=(
                    f"[系统:本轮 token 预算已用 {spent}/{self.turn_token_budget},"
                    f"请立即收敛——不要再发起新工具调用,直接给结论。]")))
```

循环前初始化:`_budget_start = self._client.total_completion_tokens`、`_budget_warned = False`。
循环自然结束(break 后)沿既有 done 事件路径收尾,不新增事件类型。

console/api.py `_default_agent_factory` 改:

```python
def _default_agent_factory(sid: str):
    """生产路径:BuddyAgent + 帷幄工具注册(仅 9999 进程触达引擎)。
    CONSOLE_TURN_TOKEN_BUDGET(默认 0=关):turn 级 completion-token 预算闸,无人值守安全门。"""
    from financial_analyst.buddy.agent import BuddyAgent
    ct.register_console_tools()
    budget = int(os.environ.get("CONSOLE_TURN_TOKEN_BUDGET", "0") or 0)
    return BuddyAgent(system_prompt=_SYSTEM_PROMPT, turn_token_budget=budget)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_buddy_token_budget.py -q` → 3 passed

- [ ] **Step 5: 提交**

```bash
git add engine/financial_analyst/buddy/agent.py guanlan_v2/console/api.py tests/test_buddy_token_budget.py
git commit -m "feat(console): 帷幄长轮 token 预算闸 CONSOLE_TURN_TOKEN_BUDGET(默认关·耗尽诚实截停)"
```

---

### Task 6: 全量回归 + 真机 e2e(控制器亲手,不派发)

- [ ] 全量 `python -m pytest tests/ -q` 全绿。
- [ ] 杀 9999 → 看门狗自愈(引擎改动生效);`/health` 200。
- [ ] 真机 rerank deep 档:手动 `POST /screen/rescore {top_n:10}` 一轮 →
  归档 `rerank.model == "deepseek/deepseek-reasoner"`、picks rerank 臂行带 model、
  `GET /seats/basket_perf?kind=rerank_ab` 新对含 model、ww_rerank_perf 行尾显代次;
  elapsed 与 chat 档历史对照留档(预期变慢,记录数字)。
- [ ] 真机预算闸:secrets.env 暂设 `CONSOLE_TURN_TOKEN_BUDGET=800` 重启,console 发一条多工具长问题,
  观察 warn 注入/诚实截停事件;验完删除该行恢复默认关,再重启。
- [ ] 更新 .superpowers/sdd/progress.md 台账。
