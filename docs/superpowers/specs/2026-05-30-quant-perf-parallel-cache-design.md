# 量化提速：并行加载 + 面板缓存 (+快测池) · 设计

> 状态: 已批准方向 (用户选「Python 提速」), 待 review 落 plan
> 日期: 2026-05-30
> 项目: financial-analyst (觀瀾) 量化研究流水线 — 交互性能优化

## 目标

把交互式因子评测从「每点一次等 48-86s」降到「首次 ~13s, 同池之后每个因子 ~0.5s」, **纯 Python, 不丢任何依赖/测试**。

## 背景与定位 (已实测)

剖析 csi300_active (868 只) × 2 年日线 (420,112 行):

| 项 | 耗时 |
|----|------|
| 顺序加载 868 只 | **85.8s** |
| 因子计算 (alpha003) | **0.55s** |
| 第 2 个因子 (复用同一面板) | 0.41s |
| 并行加载 (16 线程) | **13.0s** |

**结论: 加载占 99%, 计算占 0.5%。** 因子计算已经是 C (numpy/pandas), C++ 改写只优化那 0.5% 且对占 99% 的文件 I/O 毫无帮助 → 排除 C++。瓶颈是 (a) `from_loader` 顺序逐只读 bin, (b) UI 每点一个因子都重新加载整个面板。两者都是 Python 层可解。

参考 FinceptTerminal v4 (49% C++ / 50% Python): 它的 C++/Qt 只是桌面外壳, 量化分析仍是内嵌 Python + QuantLib — 即业内标杆也没把量化引擎改 C++。

## 范围

### 做
- **A. 并行加载**: `PanelData.from_loader` 的两个 per-code 循环 (fetch_quote + daily_basic) 改 ThreadPoolExecutor。
- **B. 面板缓存**: 新模块 `factors/zoo/panel_cache.py` 的 `load_panel_cached(...)`, LRU + 线程锁; `factor_report` / `compose_factors` / bench 改调它。
- **C. (可独立) 快测池**: `config/universes/csi_fast.txt` (~100 大盘股) + UI `POOLS` 加「快测」设默认。

### 不做
- C++ / 原生桌面 (数据证明与速度无关)。
- 改因子计算逻辑 (已经够快)。
- 持久化/磁盘缓存 (内存 LRU 足够交互场景)。
- 缓存失效的复杂策略 (会话内数据不变, 服务每日重启自然刷新)。

## A. 并行加载 (`factors/zoo/panel.py`)

### 现状
`from_loader` 内 `for code in codes: df = loader.fetch_quote(code, start, end, freq)` 顺序执行 868 次; 之后 `_merge_daily_basic` 又 `for code in codes: loader.fetch_daily_basic(code, ...)` 顺序 868 次。

### 改法
把"取一只 + 该只的 trade_date 索引提升 + set code 索引"封成 worker, 用 `ThreadPoolExecutor(max_workers=_MAX_WORKERS)` 并发, 收集后 `pd.concat(...).sort_index()` (排序保证结果与完成顺序无关, 确定性)。`_merge_daily_basic` 同样并行化逐只 fetch。

- `_MAX_WORKERS = min(16, (os.cpu_count() or 4) * 2)` (实测 16 线程 6.6x; 文件 I/O 释放 GIL)。
- **保留**: skip-on-failure (失败/空 → skipped 列表 + warning), 最终 `sort_index()`, industry/benchmark 列逻辑不变。
- **线程安全前置检查 (实现第一步)**: 读 `data/loaders/qlib_binary.py`, 确认 `fetch_quote`/`fetch_daily_basic` 无非线程安全的共享可变状态 (日历/instruments 缓存)。若有 → worker 内对该读取加 `threading.Lock`, 或每线程独立 loader 实例。**这是 A 的最大风险点, 必须先验证再并行。**
- 无公共 API 变化 → 对 REST/工具/CLI/研究脚本全部透明提速。

### 正确性约束
并行结果必须与顺序**逐字节等价** (相同 codes 集合、相同行数、相同值)。靠 `sort_index()` 消除顺序差异。

## B. 面板缓存 (`factors/zoo/panel_cache.py` 新模块)

```python
# 伪代码
_cache = OrderedDict()          # key -> PanelData
_lock = threading.Lock()
_MAXSIZE = 3                    # 每面板 ~50-100MB → 上限 ~300MB

def _key(codes, start, end, freq, with_industry):
    h = hashlib.md5(",".join(sorted(codes)).encode()).hexdigest()
    return (h, start, end, freq, with_industry)

def load_panel_cached(loader, codes, start, end, freq="day", industry_loader=None):
    k = _key(codes, start, end, freq, industry_loader is not None)
    with _lock:
        if k in _cache:
            _cache.move_to_end(k); return _cache[k]
    panel = PanelData.from_loader(loader, codes, start, end, freq, industry_loader=industry_loader)
    with _lock:
        _cache[k] = panel; _cache.move_to_end(k)
        while len(_cache) > _MAXSIZE: _cache.popitem(last=False)
    return panel

def clear_panel_cache():   # 测试 + 显式刷新用
    with _lock: _cache.clear()
```

- **接入点**: `factors/eval/report.py:factor_report`, `factors/compose/compose.py:compose_factors`, bench (`/factor/bench` 走的 bench_runner 或 server 端点) 把 `PanelData.from_loader(...)` 换成 `load_panel_cached(...)`。
- **不可变契约**: 缓存的面板被多个调用方共享, **调用方只读不改** (已核: build_report 用 `compute(panel)` 产新 Series, winsorize/zscore 作用于 alpha 不碰面板; compose 从面板建矩阵也只读)。spec 实现时再确认一遍无 in-place 改面板; 若有则该调用方先 copy。
- **key 含 `with_industry`**: 有无 industry 列产出的面板不同, 必须区分。
- **线程安全**: server (uvicorn) 把 sync 端点跑在线程池 → 并发请求可能同时读写 `_cache`, 故 `OrderedDict` 操作全程持 `_lock`。注意: `from_loader` (慢) 在锁外执行, 避免一个慢加载阻塞所有缓存命中; 代价是同 key 并发首次可能重复加载一次 (可接受, 不影响正确性)。

## C. 快测池 (可独立交付)

- `config/universes/csi_fast.txt`: csi300 市值前 ~100 只 (大盘流动, 加载快)。实现时由 `resolve_universe_codes('csi300')[:100]` 生成静态 txt (确定性, 不留运行时依赖)。
- `ui/quant.jsx`: `POOLS = ['快测', 'csi300', 'csi500', 'csi800', 'all']`, 默认 `useState('快测')`, `poolParam('快测') -> 'csi_fast'`; bump `quant.html` 的 `?v=`。
- 效果: 首次加载 ~100 只也只要几秒。

## 测试

- **A 正确性** (`tests/test_panel_parallel_load.py`): 用 stub loader (确定性多只) 跑 `from_loader`, 断言结果与"顺序参考实现"等价 (相同 index、shape、抽样值)。再加一个"部分代码故意 raise → 仍 skip 且其余正确"。
- **B 缓存** (`tests/test_panel_cache.py`): 同参第 2 次命中 (返回同一对象 / 计数器证明 from_loader 只调一次, 用 monkeypatch 计数); 换 window/freq/codes → 未命中; 超 `_MAXSIZE` → LRU 淘汰最旧; `clear_panel_cache` 清空; 并发 (ThreadPoolExecutor 多线程同调) 不崩。
- **C** (`tests/test_universe_resolve.py` 加一例): `resolve_universe_codes('csi_fast')` 返回 ~100 个带前缀真实码。
- **全量回归 970**: 并行加载必须不改任何现有结果 (A 的等价性是关键)。
- **手动时延 sanity** (非 pytest, 实现后实测): csi300_active 首次 ~13s, 第 2 个因子 ~0.5s。

## 验收标准 (DoD)

- `from_loader` 并行化, 868 只加载 85s→~13s, 全量 970 测全绿 (结果不变)。
- `load_panel_cached` 接入 report/compose/bench; 同池第 2 个因子 <1s。
- (C 若做) 快测池可选且默认, 首次几秒。
- 无新增重依赖 (ThreadPoolExecutor/OrderedDict/hashlib 都是标准库)。
- 线程安全: 并发请求不崩、不串面板。
- 提交后推 origin/main。

## 开放点 (review 时定)
1. 缓存放**引擎层** (`factors/zoo/panel_cache.py`, 默认) — REST/工具/CLI 全受益。是否同意?
2. **C 快测池**是否纳入本次 (默认纳入, 但可拆到下一次)。
3. `_MAXSIZE=3` 是否合适 (~300MB 上限)。
