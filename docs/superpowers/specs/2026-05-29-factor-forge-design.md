# 炼因子 (Factor Forge) 设计 · SP-B v1

> 状态: 已批准, 待落 plan
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-B (经验/想法 → 因子, 截面 v1)

## 目标

在 `financial-analyst` 包内实现"炼因子"闭环的后端: 用户给一段**自然语言因子想法**, LLM 把它转成一个**截面因子表达式**(复用现成 expr DSL), 自动校验+编译+快测 IC, 用户满意则**入库**(持久化为可复用的 user 因子, 之后能被 factor_report/alpha_compare 按名引用)。暴露为 `alpha_forge` 对话工具——像 SP-A 的 factor_report 一样现在就能在觀瀾里用。realize 设计稿 quant.jsx 的 AlchemyCard 的**后端**(那张可视化卡片归 SP-C)。

## 背景与定位

### 在全套流水线里的位置
量化研究闭环: **想法/经验 → 因子(炼因子, SP-B) → 评测(SP-A) → 入库/迭代**。SP-A(单因子评测引擎 + factor_report 工具)已合 main。SP-B 在它上面加"自然语言 → 因子"这一步, 并加"用户因子持久化"。

子项目拆分 (整体): A 评测引擎 ✅ / **B 炼因子(本文)** / C 因子工作台 UI / D 多因子+合成模型 / E 研究档案。

### 已锁定的关键决策
1. **截面优先 (分期)**: v1 炼**截面排序因子**(像 alpha101/qlib158), 直接复用 SP-A 的 expr DSL + IC 评测。**事件/择时信号**(像 "连续放量+MACD金叉" 那种满足条件才触发的信号, 需要新的事件回测 + DSL 的 WHERE/CROSS 构造) → **SP-B.2** 后续。
2. **自由文本输入 (v1)**: forge 收一段自然语言想法(用户在觀瀾里打/贴, 可把经验卡片内容贴进来)。**从记忆/经验卡自动召回** → 后续 (wisdom 库无 FTS, memory 索引是 agent 记忆不是经验卡, 接起来更绕)。
3. **v1 用现有价量 DSL**: 字段 = close/open/high/low/volume/vwap/amount/returns/industry。覆盖 动量/反转/量价/波动 类。**基本面想法(高股息/低估值/小盘)当前 DSL 无 pe/pb/dv/mv 字段, 表达不了** → forge 识别并说明, 不硬编错因子。**基本面字段扩展 = SP-B.1b** (给 PanelData + DSL 加 daily_basic 的 pe_ttm/pb/dv_ttm/total_mv/turnover_rate, 数据已有, 但要先核实 loader 能带这些列; 它惠及所有因子工具)。
4. **不碰 UI**: SP-B 只到"能在对话里炼+入库"。AlchemyCard 可视化卡片 = SP-C。

### 现状基线 (已勘察)
- **LLM 调用**: `LLMClient.for_agent("buddy")` (`llm/client.py:99`) 构造; `async chat(messages, tools=None, response_format=None, temperature=0.2)` (`client.py:232`) 返回 OpenAI 信封 dict, 文本取 `resp["choices"][0]["message"]["content"]`。**只有 async**, 但 buddy 工具体跑在 `asyncio.to_thread` 的工作线程上(无运行中 loop, `agent.py:397`), 故工具内可 `asyncio.run(client.chat(...))`。**今天 tools.py 没有任何工具调 LLM**——forge 是第一个; 范式参考 `wisdom/extractor.py` / `agent/tier2/quant_analyst.py`(都 `LLMClient.for_agent(name)` 然后 `await chat`)。provider/model/network_profile 路由全在 chat 内部(由 llm.yaml 驱动), 工具不用管。
- **expr DSL** (`factors/zoo/expr.py`, SP-A 抽出): `FACTOR_VOCAB`(字段+算子白名单) + `validate_expr(expr)`(拒空/`__`/`import`/`lambda`) + `compile_factor(expr)`(受限 eval → PanelData→Series)。
- **评测**: `factor_report`(SP-A, 完整) + buddy `factor_test`(快测 IC, 复用 bench_runner 的 `bench_one`/`_forward_returns`)。
- **注册表** (`factors/zoo/registry.py`): `_REGISTRY` 内存 dict, `register(AlphaSpec)` 接受运行时新增(幂等), **无持久化**。`AlphaSpec`(frozen): name/family/description/formula_text/compute/paper/tags。**compute 是活函数不能序列化**。
- **可写根**: `~/.financial-analyst/` (cache 在 `selector._cache_dir()`=`~/.financial-analyst/cache`; 可写根解析见 `memory_paths.default_memory_root()` honor `$FINANCIAL_ANALYST_HOME`→cwd→`~/.financial-analyst`)。

## 范围

### 做 (in-scope)
1. 新建 `factors/forge/` 模块: `forge.py`(NL→DSL 编排) + `store.py`(用户因子持久化) + `__init__.py`。
2. `alpha_forge` 对话工具 (buddy/tools.py) — 第一个调 LLM 的工具。
3. `user_factors` 管理工具 (列出/删除已入库 user 因子)。
4. 启动时从 user_factors.json 重建并注册 user 因子 (让 factor_report/alpha_compare/factor_test 能按名用)。
5. 确定性单元测试 (mock LLMClient)。

### 不做 (out-of-scope, 明确推后)
- **事件/择时信号 + 事件回测** → SP-B.2。
- **基本面字段进 DSL** (pe/pb/dv/mv 等) → SP-B.1b。
- **从 wisdom/memory 自动召回经验卡** → 后续 (v1 只收自由文本)。
- **AlchemyCard 可视化 / 调参滑块 UI** → SP-C。
- **深度评测**: 不在 forge 内做; 用户对炼出的因子另跑 `factor_report`。

## 架构

**原生 `factors/forge/` 模块 + `alpha_forge` 工具**。被否决: 全塞 tools.py(膨胀, prompt/repair/持久化该独立可测); 做成 sub-agent(forge 是一次性转换, 工具内 asyncio.run(chat) 足够, 不必 agent loop)。

## 模块布局

```
src/financial_analyst/factors/forge/
  __init__.py    导出 forge_factor, ForgeResult, UserFactorStore
  forge.py       forge_factor() + ForgeResult + prompt 构造 + repair
  store.py       UserFactorStore (load/save/register_all/add/list/remove)
```

## 数据模型

```python
@dataclass
class ForgeResult:
    idea: str               # 原始自然语言想法
    expr: str               # 生成的 DSL 表达式 ("" if 失败)
    parsed: list[dict]      # [{"k": "触发", "v": "..."}] 信号要素, 喂 AlchemyCard
    name: str               # 建议因子名, 如 "usr_rev5vol"
    rationale: str          # LLM 给的逻辑说明
    compile_ok: bool        # validate+compile+dry-run 是否通过
    error: str              # 失败原因 (compile_ok=False 时)
    out_of_vocab: bool      # 想法是否需要 DSL 没有的字段(如基本面)
```

`user_factors.json` 每条:
```json
{"name": "usr_rev5vol", "family": "user", "expr": "rank(-delta(close,5)) * (volume/ts_mean(volume,5))",
 "description": "5日反转×量比", "parsed": [{"k":"...","v":"..."}],
 "created": "2026-05-29", "kpis": {"rank_ic": -0.03, "rank_icir": -1.2, "ic": ..., "hit_rate": ...}}
```
(存 **DSL 字符串**, 不存 compute; 加载时 `compile_factor(expr)` 重建。)

## forge_factor 流程 (forge.py)

```
forge_factor(idea, llm_client=None) -> ForgeResult:
  1. client = llm_client or LLMClient.for_agent("buddy")
  2. messages = [system_prompt(FACTOR_VOCAB + few-shot), user(idea)]
  3. resp = asyncio.run(client.chat(messages, response_format={"type":"json_object"}, temperature=0.2))
     text = resp["choices"][0]["message"]["content"];  obj = json.loads(text)
     → {expr, parsed, name, rationale, out_of_vocab}
  4. 若 out_of_vocab=True (LLM 判定想法需 DSL 没有的字段) → ForgeResult(compile_ok=False, out_of_vocab=True, error="需要 X 字段, 当前 DSL 仅价量")
  5. validate_expr(expr) → compile_factor(expr) → 小合成面板 dry-run compute (catch NameError/类型错)
  6. 失败 → 把 error 喂回 LLM 重试 1 次 (repair); 仍失败 → ForgeResult(compile_ok=False, error=...)
  7. 成功 → ForgeResult(compile_ok=True, expr, parsed, name, rationale)
```

**system prompt 要点**: "你是量化因子工程师。把用户想法转成**一个截面因子表达式**, 只能用这些字段+算子: {FACTOR_VOCAB}。表达式返回每个 (日期,股票) 的打分, **高分=更看好**。若想法需要表中没有的字段(如基本面 pe/股息/ROE、或事件型条件), 把 out_of_vocab 设 true 并说明。输出 JSON {expr, parsed:[{k,v}], name, rationale, out_of_vocab}。" + 2~3 个 few-shot (如 "5日反转"→`rank(-delta(close,5))`; "放量上涨"→`rank(delta(close,1))*rank(volume/ts_mean(volume,20))`; "低波动"→`rank(-stddev(returns,20))`)。

**name 规范**: `usr_` 前缀 + LLM 短名; 与现有注册名冲突则加序号 (store 保证唯一)。

## UserFactorStore (store.py)

- 路径: `~/.financial-analyst/factors/user_factors.json` (写根解析仿 `_cache_dir`/`default_memory_root`, honor `$FINANCIAL_ANALYST_HOME`)。
- `load() -> list[dict]`; `save(list)`; `add(entry)` (重名→报错或覆盖, 由调用方定; v1 重名加序号); `list()`; `remove(name)`。
- `register_all()`: 读 json, 对每条 `compile_factor(expr)` → `AlphaSpec(name, family="user", description, formula_text=expr, compute=fn)` → `registry.register(spec)`。**幂等**(register 同 compute 幂等; 但重编译每次是新函数对象 → register 对 family="user" 用"先删后加"或允许覆盖, 见错误处理)。
- 启动接线: 在 buddy/agent 或 tools 模块导入时调一次 `UserFactorStore().register_all()`(catch 异常, 缺文件=空, 不崩)。

## 工具 (buddy/tools.py)

- **`alpha_forge`** (cost_hint=minutes, confirm_required=True): 参数 `idea: str` (必填), `save: bool=False`, `universe="csi300_active"`, `quick_eval: bool=True`。
  - `forge_factor(idea)` → 若 compile_ok=False/out_of_vocab → is_error ToolResult 友好说明。
  - quick_eval=True → 复用 factor_test **底层**的 IC 计算 (`PanelData.from_loader` + `bench_runner.bench_one`/`_forward_returns`, 抽样 universe ~30-60s), **不调 factor_test 工具本身**(要的是数值不是文本)。得 rank_ic/rank_icir/ic/hit_rate/方向, 写进 store 条目的 kpis。
  - save=True → `UserFactorStore.add(...)` + `register` → 因子立即可被 factor_report/alpha_compare 按名引用。
  - 渲染中文报告: 原话 → 解析信号要素 → 公式 → 快测 IC + 方向 → (入库提示或已入库)。
  - 内部 `asyncio.run(client.chat(...))` (工具体在工作线程, 无运行 loop)。
- **`user_factors`** (cost_hint=fast): 列出已入库 user 因子 (name/expr/description/kpis); 可选 `remove=<name>` 删除。

## 错误处理
- LLM 返回非 JSON / 缺字段 → 容错解析(尽量提取 expr), 仍不行 → ForgeResult(compile_ok=False, error="LLM 输出无法解析")。
- expr 非法/编译失败/dry-run 抛错 → repair 1 次 → 仍失败 → compile_ok=False + 明确 error; 工具 is_error。
- out_of_vocab → 不报错, 友好说明缺什么字段 + 指向 SP-B.1b。
- LLM 不可用(网络/key) → 工具 catch → is_error "LLM 不可用" (不崩)。
- user_factors.json 缺失/损坏 → register_all 当空处理, 记 warning, 不崩。
- 重名 user 因子 → store 加序号保证唯一; register family="user" 允许覆盖(先 _REGISTRY.pop 再 register, 避免 frozen-compute 冲突报错)。

## 测试策略 (确定性单测, mock LLM)
1. **forge happy path**: mock LLMClient.chat 返回 `{expr:"rank(-delta(close,5))", parsed:[...], name:"usr_rev5", rationale:..., out_of_vocab:false}` → forge_factor → compile_ok=True, expr 正确, dry-run 在合成面板上产出 Series。
2. **repair**: mock 第一次返回坏 expr(如 `rank(-delta(close))` 缺参/`close + nonexistent`), 第二次返回好的 → compile_ok=True, 验证重试了 1 次。
3. **out_of_vocab**: mock 返回 out_of_vocab=true → ForgeResult.out_of_vocab=True, compile_ok=False, 不抛。
4. **解析容错**: mock 返回非 JSON / 缺 expr → compile_ok=False, error 明确。
5. **store 往返**: add → save → 新 UserFactorStore.load → register_all → `registry.get(name)` 能取到且 compute 在合成面板上工作(重编译成功)。
6. **store remove + 重名加序号**。
7. **alpha_forge 工具**: mock LLM + mock universe/loader(仿 SP-A tool 测试), save=False → is_error False + 报告含公式+IC; out_of_vocab → is_error True 友好说明; save=True → 之后 registry 有该因子。
8. **register_all 缺文件**: 不崩, 空注册。

## 验收标准 (Definition of Done)
- `forge_factor("5日反转")` (mock LLM) 返回 compile_ok=True 的截面因子, dry-run 通过。
- `alpha_forge` 工具在觀瀾跑通: 想法 → 公式 + 快测 IC; `save=true` 后该因子能被 `factor_report <name>` 引用并出完整报告。
- user_factors.json 持久化 + 启动重建注册可往返。
- out_of_vocab / LLM 不可用 / 坏 expr 都结构化处理不崩。
- 上述 8 组单测全绿; 不引入新重依赖 (LLMClient/expr/factor_test 都现成)。
- buddy 现有工具 (factor_test/alpha_compare/factor_report) 不回归。
