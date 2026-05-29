# 多因子合成 LLM 赋能 (Compose LLM) 设计 · SP-D.2

> 状态: 待用户 review
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-D (多因子合成) 的 LLM 增量 — 输入顾问 + 结果研判

## 目标

给多因子合成加 LLM 层, 形成闭环: **说目标 → LLM 配方 → 跑 OOS → LLM 研判 → 迭代**。
1. **输入顾问**: 自然语言目标 ("低回撤的动量+反转组合") → LLM 产出成员 (DSL 表达式) + 合成方法 + 理由 → 直接喂 `compose_factors`。把"手动选成员/方法"变"说目标"。
2. **结果研判**: compose 跑完, LLM 读 weights/成员对比/OOS 指标 → 写研判 (为什么行/不行 + 过拟合/相关性风险 + 下一步换哪个成员), 在机械 verdict 之上加质性解读。

纯 `compose_factors` 引擎保持 **LLM-free** (确定性底座); LLM 层独立、可注入 `complete_fn` 单测。

## 背景与定位

SP-D (多因子合成 equal/ic_weighted/linear/lgbm + OOS) ✅ 已交付; 本文是其 LLM 增量 (称 SP-D.2)。

### 现状基线 (已勘察)

- **compose 引擎** (`factors/compose/compose.py`): `compose_factors(members, config, method, train_frac) -> ComposeResult`。成员经 `_resolve_member_compute(name)` (先注册名 `registry.get`, 失败回退 `validate_expr`+`compile_factor`)。`ComposeResult{method, members, weights:{}, train_frac, n_train_dates, n_test_dates, composite:FactorReport|None, member_oos:[{name,rank_ic,sharpe}], verdict:str, warnings, status, error}`。**verdict 是机械串** (如 "综合分 OOS Sharpe 0.81 vs 最优成员 0.55 → 增益 (+0.26)")。永不抛, 5 错误态。
- **forge LLM 范式** (`factors/forge/forge.py`): `forge_factor(idea, complete_fn=None)`; `complete_fn(messages)->str` 可注入 (测试), 默认 `_default_complete` = `LLMClient.for_agent("buddy")` + `asyncio.run(client.chat(messages, response_format={"type":"json_object"}, temperature=0.2))["choices"][0]["message"]["content"]`。**必须在无事件循环的线程跑** (asyncio.run); 2 次尝试单轮 repair; 永不抛 (失败落 ForgeResult.error)。`CompleteFn = Callable[[List[dict]], str]`。`FACTOR_VOCAB` (`zoo/expr.py`) = 字段+算子白名单 (含本次新 cross)。
- **compose REST/工具/UI**: `POST /factor/compose` (ComposeReq, archive/note); agent 工具 `factor_compose` (members≥2); 工作台 `ComposeMode` (手选成员 chips + method Segmented + train_frac → 结果: weights + 成员对比表 + verdict + `<FactorReportView>` composite)。
- **REST forge 教训** (SP-B.2 抓到): 端点调用 forge 的 `asyncio.run` 必须脱离事件循环 → forge 端点用 sync `def`。compose advise 端点同理 (调 LLM) 须 sync `def`; interpret 也是。

### 已锁定决策 (本次 brainstorm)

- **两者都做**: 输入顾问 (NL→配方) + 结果研判 (解读+迭代)。
- **顾问出表达式**: LLM 产出 DSL 表达式成员 (复用 FACTOR_VOCAB), 而非从 442 库名里挑 (省 token + 复用 forge DSL 能力)。
- **verdict 保留**: 机械 verdict 作确定性底座不动; 研判是其上的 LLM 层 (LLM 挂了回落 verdict)。
- **引擎纯净**: `compose_factors` 不碰 LLM; LLM 全在新 `advisor.py`。

## 范围

### 做
1. 新 `factors/compose/advisor.py`: `ComposeRecipe` + `compose_advisor` + `interpret_compose` + LLM complete 助手 (json + text)。
2. REST: `POST /factor/compose/advise` + `/factor/compose` 加 `interpret` 字段。
3. agent 工具 `factor_compose` 加 `goal` (可选, 触发 advise) + 跑完自动研判。
4. UI `ComposeMode`: 「🪄 一句话配方」输入 (advise→填表) + 结果区自动研判 panel。
5. 测试 (stub complete_fn + REST TestClient)。

### 不做
- 改 `compose_factors` 机械 verdict / 引擎逻辑。
- advisor 从全库 442 名里挑 (出表达式即可)。
- 顾问自动直接跑 (出配方后用户/agent 审一眼再跑; 工作台填表可改)。

## `factors/compose/advisor.py`

```python
CompleteFn = Callable[[List[dict]], str]

@dataclass
class ComposeRecipe:
    goal: str
    members: List[str] = field(default_factory=list)   # DSL 表达式
    method: str = "lgbm"
    train_frac: float = 0.6
    rationale: str = ""
    status: str = "ok"          # ok / out_of_vocab / bad_output / llm_error
    error: str = ""
```

**`compose_advisor(goal, complete_fn=None) -> ComposeRecipe`** (镜像 forge):
- prompt (`_ADVISOR_SYSTEM`): "把目标拆成 ≥2 个**互补**的截面因子表达式 (只用 FACTOR_VOCAB), 选合成方法 (equal/ic_weighted/linear/lgbm, 成员相关高→linear/lgbm 去冗余, 少而稳→ic_weighted), 给 train_frac (0.5~0.7) 和理由。" + 1-2 few-shot。输出 JSON `{members:[...], method, train_frac, rationale}`。
- 校验: `len(members)>=2`; 每个经 `validate_expr` + `compile_factor` + 小面板 dry-run (复用 forge `_tiny_panel` 思路); method ∈ 四法 (非法→lgbm); train_frac clip [0.5,0.8]。非法成员 → 收集错误, 第 2 次 repair (把错喂回)。仍不行 → status="bad_output"。LLM 抛 → status="llm_error"。**永不抛**。
- complete 默认 `_complete_json` (同 forge: LLMClient.for_agent("buddy") + asyncio.run + response_format json_object)。

**`interpret_compose(result, complete_fn=None) -> str`** (研判):
- 只在 `result.status=="ok"` 调; 把 `{method, weights, member_oos, composite 的 ic/portfolio 关键指标, verdict, n_train_dates, n_test_dates}` 拼成紧凑事实块喂 LLM (`_INTERPRET_SYSTEM`: "你是量化组合研究员, 基于这些 OOS 事实写 3-5 句研判: ①综合分 vs 最优成员增益是否显著 ②权重是否过度集中 (过拟合风险) ③成员是否同源冗余 ④下一步迭代建议 (换/加/正交化哪个)。只说数据支持的, 不编造。")。
- complete 默认 `_complete_text` (LLMClient.for_agent("buddy") + asyncio.run + chat **无 response_format**, temperature 0.3) → 返回 content。
- **任何异常 → 返回 ""** (调用方回落机械 verdict)。

**LLM 助手** (`advisor.py` 内, 复用 forge 的线程纪律): `_complete_json(messages)` / `_complete_text(messages)` — 各自 `asyncio.run(client.chat(...))`; 必须 sync 上下文 (端点用 sync def, 工具在 worker thread)。

## 接入

### REST (`buddy/server.py`)
- `class AdviseReq(BaseModel): goal: str; universe: str = "csi300_active"`。
- `POST /factor/compose/advise` (**sync `def`** — advisor 用 asyncio.run, 脱离事件循环, 同 forge 端点): 调 `_advisor_mod.compose_advisor(req.goal)` → `_jsonable(asdict(recipe))`。LLM 失败 → recipe.status; 内部异常 → 500。
- `ComposeReq` 加 `interpret: bool = False`。`factor_compose_ep`: 改为 **sync `def`** (因 interpret 会 asyncio.run); compose 跑完若 `interpret and res.status=="ok"`: `body["interpretation"] = _advisor_mod.interpret_compose(res)` (try/except → "")。archive 逻辑不变。

### Agent 工具 (`buddy/tools.py`)
- `factor_compose` 加可选 `goal`: 给了 → 先 `compose_advisor(goal)` 出 members (status≠ok 则报错返回); 否则用传入 members。跑完 `res.status=="ok"` → 文末附 `interpret_compose(res)` 研判段 (失败跳过)。`members` 改非必填 (goal 或 members 二选一)。

### UI (`ui/quant.jsx` ComposeMode)
- 顶部加「🪄 一句话配方」: textarea(goal) + 按钮 → `POST /factor/compose/advise` → 用返回的 members 填 chips、method 填 Segmented、显示 rationale (可改后点合成评测)。
- 合成调用带 `interpret:true`; 结果区 verdict 下方加「LLM 研判」panel 渲染 `interpretation` (空则不显)。
- 三态: advise loading/error; 研判随结果出。cache-buster bump。

## 测试 (tests/test_compose_llm.py)

1. **compose_advisor (stub complete_fn 返 canned JSON)** → ComposeRecipe, members≥2 且都 compile 通过, method 合法, status="ok"。
2. **advisor 非法成员 repair**: 第 1 次返含烂表达式, 第 2 次返合法 → 最终 ok (验证 repair 喂错)。
3. **advisor 全程烂 / 缺 members** → status="bad_output", 不抛。
4. **advisor LLM 抛** (complete_fn raise) → status="llm_error", 不抛。
5. **interpret_compose (stub)**: 喂一个 ok 的 ComposeResult → 返回非空研判串; complete_fn raise → 返回 ""。
6. **REST `/factor/compose/advise`** (monkeypatch `compose.advisor.compose_advisor` 或注入 stub complete): 200 + members/method/rationale; LLM 失败态 200+status。
7. **REST `/factor/compose` interpret=true** (stub): body 含 `interpretation` 非空; interpret=false → 无该字段或空。
8. 不污染注册表; 控制端 miniconda 复跑; 不用 pandas≥2.2-only API; 经模块属性访问 (`_advisor_mod.xxx`) 便于 monkeypatch。

## 验收标准 (DoD)

- `compose_advisor` NL→ComposeRecipe (表达式成员 + 方法 + 理由), 校验+repair, 4 状态不抛。
- `interpret_compose` ComposeResult→研判串, 失败回落 "" (不拖垮)。
- `compose_factors` 引擎/verdict 不变、不回归。
- `POST /factor/compose/advise` + `/factor/compose` interpret 字段; 端点 sync def (asyncio.run 脱离事件循环); _jsonable。
- agent `factor_compose` 支持 goal + 自动研判。
- 工作台 ComposeMode: 一句话配方→填表→跑→研判 闭环可走 (浏览器 stub 实测: stub complete_fn 或真 LLM)。
- 单测全绿; 现有 compose/forge/rest 套件不回归; 无新依赖 (复用 LLMClient)。
