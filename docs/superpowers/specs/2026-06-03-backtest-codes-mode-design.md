# Backtest Codes 模式 (单股 + 自定义 watchlist) · 设计

> 状态: 设计审中
> 日期: 2026-06-03
> 子项目: financial-analyst Agent 回测 panel — 候选模式扩展
> 工作量: ~半天 (后端轻 + 前端中 + tests)
> 触发事件: 用户 2026-06-03 反馈 "现在是回测大盘对吧 现在我想是有股票候选 有调仓 可以针对单只股票进行回测" + AskUserQuestion 答 "两种都要 (单股 + N 只 watchlist)"

## 目标

当前 backtest panel 只支持 **池子模式** (csi300/csi_fast/csi500/csi800), 在固定池子里按 rev_20 选 Top-N. 用户想要:
- **单股回测**: 输入 1 只代码 (eg SH600519), 看 agent 对这只股的择时
- **自定义 watchlist 回测**: 输入 3-5 只核心持仓 (eg SH600519 + SZ002594 + SH601318), agent 在这 N 只里选股调仓

两种用同一个 `codes: list[str]` 字段 cover (输 1 个 = 单股, 输 N 个 = watchlist). 跟现有池子模式 **并存** (前端 segment 切换).

## 范围

### 做
- 后端 `BacktestRunReq` 加 `codes: Optional[list[str]] = None` 字段
- 后端 `CandidateConfig` 加 `codes: Optional[list[str]] = None` 字段 (优先级: codes > pool > 旧 watchlist 路径)
- 后端 `select_candidates` 加 codes 分支: codes 非空 → base = codes (跳过 pool/watchlist 解析)
- 前端 BacktestMode 加 "候选模式" segment: `[池子] | [指定代码]`
  - 池子模式 (默认): 现有 pool dropdown + topn (不动)
  - 代码模式: input 多代码逗号分隔, topn 自动 = len(codes), pool 控件 disabled
- start payload 透传 codes (代码模式) 或 pool (池子模式), 互斥
- BacktestSummaryChips 在代码模式下显示 "candidates: [SH600519, SZ002594, ...]" 替代 "池: csi300"
- PoolFilterPopover 在代码模式下文案改: "用户指定 codes (N 只), 不走池子过滤"

### 不做 (留下轮)
- ❌ 代码自动补全 (输入框只接受手输 + 逗号分隔, 不做 dropdown 搜索)
- ❌ 代码格式校验 (前端只做"非空 + 6位数字 prefixed by SH/SZ/BJ"); 真实代码合法性由后端 fetch_quote_leq_prev 返空触发 fallback
- ❌ codes mode 下 _MockAgent 退化逻辑改写 (现 _MockAgent 空仓→买最低 rev_20, 1 只 case 自然退化为"买这只", 不需要特殊代码)
- ❌ codes + pool 共存 (一次只允许一种, 前端 segment 强制单选)

---

## 后端改动

### `BacktestRunReq` (server.py L203-219)

加字段 (插在 hold_days 之前, 跟 pool 同组):
```python
class BacktestRunReq(BaseModel):
    # ...
    pool: str = Field(default="csi300", pattern=..., description="...")
    codes: Optional[List[str]] = Field(default=None, description="自定义候选代码 (单股/watchlist 模式, 非空则覆盖 pool)")
    hold_days: int = ...
    # ...
```

校验 (Pydantic field_validator 或 model_validator):
- `codes` 非空时, 每个元素必须匹配正则 `^(SH|SZ|BJ)\d{6}$`
- `codes` 非空 + `pool != 'csi300'` (default) → 警告但接受 (codes 优先)
- `codes` 列表长度 ≤ 50 (防误输大池)

### `CandidateConfig` (candidate.py L23-31)

加字段:
```python
@dataclass
class CandidateConfig:
    topn: int = 20
    pool: Optional[str] = None     # 池子模式
    codes: Optional[List[str]] = None  # 新增 — 自定义代码模式 (优先级最高)
    rev20_lookback_tradedays: int = 30
    # ... 现有字段不动
```

### `select_candidates` (candidate.py L64-117)

优先级分支扩成 3 层:
```python
if cfg.codes:
    # codes 模式: base = holdings ∪ codes, 不解析 pool/watchlist
    base = list(dict.fromkeys([*holdings, *cfg.codes]))
    universe_label = "codes"
elif cfg.pool:
    # 池子模式 (现有)
    pool_codes = resolve_universe_codes(cfg.pool)
    base = list(dict.fromkeys([*holdings, *pool_codes]))
    universe_label = "pool"
else:
    # 旧 watchlist 路径 (现有)
    watch = _load_watchlist_codes(cfg)
    base = list(dict.fromkeys([*holdings, *watch]))
    universe_label = "watchlist"
# ... rev_20 计算 + ordered 构造跟现有一致, source 标签换成 universe_label
```

filter_stats (E P1.3 加的) 在 codes 模式下:
- `n_pool` = len(cfg.codes)  (复用同字段, 含义随模式变)
- `n_holdings`, `n_base`, `n_rev20_computable`, `n_final` 跟原计算

### `run_backtest` (backtest_run.py)

`CandidateConfig(...)` 加 `codes=req.codes`:
```python
candidate=CandidateConfig(
    topn=req.candidate_topn,
    pool=req.pool,
    codes=req.codes,   # 新增
),
```

如 `req.codes` 非空, 自动调整 `topn = len(req.codes)` (1 只就 topn=1, 5 只就 topn=5), 让 agent 在所有指定代码里选, 不被 topn 截断.

### 测试

`tests/test_candidate_codes_mode.py` (new):
```python
def test_codes_mode_uses_user_codes_as_base():
    cfg = CandidateConfig(codes=["SH600519", "SZ002594"], topn=2)
    # mock reader 返合理 close 历史
    result = select_candidates(...)
    assert set(result.codes) == {"SH600519", "SZ002594"}
    # filter_stats n_pool = 2 (codes 长度)
    assert result.filter_stats["n_pool"] == 2

def test_codes_mode_overrides_pool():
    cfg = CandidateConfig(codes=["SH600519"], pool="csi300", topn=1)
    result = select_candidates(...)
    # codes 优先, pool 不解析
    assert set(result.codes) == {"SH600519"}

def test_codes_mode_includes_holdings():
    cfg = CandidateConfig(codes=["SH600519"], topn=1)
    result = select_candidates(..., holdings=["SZ000001"])
    # holdings + codes deduped
    assert "SZ000001" in result.codes
    assert "SH600519" in result.codes
```

`tests/test_backtest_run_req_v2.py` (append):
```python
def test_codes_field_accepts_valid_list(self):
    req = BacktestRunReq(codes=["SH600519", "SZ002594"])
    assert req.codes == ["SH600519", "SZ002594"]

def test_codes_field_rejects_bad_format(self):
    # 前端可能跳格式校验, 后端 model 兜底
    with pytest.raises(ValidationError):
        BacktestRunReq(codes=["bad_format", "SH600519"])
```

(model_validator 实现 pattern 校验, 或 field_validator)

---

## 前端改动

### "候选模式" segment

`BacktestMode` 高级控件区 (P2.4 加的 5 控件) 顶部加 segment:
```jsx
const [candidateMode, setCandidateMode] = useState('pool');  // 'pool' | 'codes'
const [codes, setCodes] = useState('');  // 逗号分隔 string

<Segmented value={candidateMode} onChange={setCandidateMode}
  options={[{value: 'pool', label: '池子'}, {value: 'codes', label: '指定代码'}]} />
```

切到 'codes' 模式 → 隐藏 pool dropdown + topn input, 显示 codes textarea:
```jsx
{candidateMode === 'codes' ? (
  <label className="mono" style={{flex: 1}}>候选代码 (逗号/空格分隔)
    <input value={codes} onChange={e => setCodes(e.target.value)}
      placeholder="SH600519, SZ002594, SH601318"
      style={{width: '100%', padding: '5px 8px', border: '1px solid var(--line)',
              fontFamily: 'var(--mono)', fontSize: 12}} />
  </label>
) : (
  /* 现有 pool dropdown + topn (不动) */
)}
```

### start_run payload

```jsx
const codesList = candidateMode === 'codes'
  ? codes.split(/[\s,，、]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
  : null;
const r = await postJSON('/backtest/run', {
  // ... 现有字段
  pool: candidateMode === 'pool' ? pool : 'csi300',  // codes mode 仍传 default 占位但后端忽略
  codes: codesList,
  candidate_topn: codesList ? codesList.length : Number(topn),
});
```

### BacktestSummaryChips 适配

接收 `d.params.codes` (后端透传):
```jsx
const poolLabel = p.codes && p.codes.length
  ? `指定代码 (${p.codes.length} 只: ${p.codes.slice(0, 3).join(', ')}${p.codes.length > 3 ? '...' : ''})`
  : (p.pool || '(旧 watchlist 模式)');
```

### PoolFilterPopover 适配

接收 `codes` 额外 prop:
```jsx
function PoolFilterPopover({ pool, codes, topn, stats, onClose }) {
  // ...
  {codes && codes.length ? (
    <ol>
      <li>用户指定 codes (<strong>{codes.length}</strong> 只)</li>
      <li>叠加当前持仓 ({stats?.n_holdings ?? 0} 只)</li>
      <li>合并去重 ({stats?.n_base ?? '?'} 只)</li>
      <li>对每只算 rev_20 (可算 {stats?.n_rev20_computable ?? '?'} 只)</li>
      <li>不走池子过滤, 全部入选 (实际 {stats?.n_final ?? '?'} 只)</li>
    </ol>
  ) : (
    /* 现有 pool 模式 6 步描述 */
  )}
}
```

---

## 跨切关注

### 数据契约

`BacktestRunReq.codes` 默认 None, 不破坏现有 caller. `CandidateConfig.codes` 同. `BacktestResult.params` 已经透传 `req.model_dump()`, 加字段不破坏现有调用.

### 提交策略

- 单分支 `feat/backtest-codes-mode` (从 main `49cdf2a` 派生)
- 单 commit (scope 小): `feat(backtest): codes 模式 — 单股/watchlist 自定义候选 (优先级 codes > pool)`
- ff-merge → main → push (跟 D/E 同流程)

### 验收 DoD

- [ ] `BacktestRunReq(codes=["SH600519"])` 接受, `codes=["bad"]` 422
- [ ] `CandidateConfig(codes=["SH600519"], pool="csi300")` → codes 优先, base = ["SH600519"]
- [ ] `POST /backtest/run {codes: ["SH600519"], mode: "mock"}` → 200, mock agent 跑 N 日 → 至少 1 buy 1 sell
- [ ] 前端 segment 切 [指定代码] → pool dropdown 隐藏, codes input 显示
- [ ] 输 "SH600519, SZ002594" → 跑 mock 5 日 → 横条显示 "指定代码 (2 只: SH600519, SZ002594)"
- [ ] PoolFilterPopover 在 codes 模式显示 "用户指定 codes (N 只), 不走池子过滤"
- [ ] 全量回归不破
- [ ] 工作分支 feat/backtest-codes-mode, ff-merge main, push origin
