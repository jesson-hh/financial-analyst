# report_v2 语义检索经验注入 (SP-1 接入) · 设计

| 字段 | 值 |
|---|---|
| 日期 | 2026-06-02 |
| 状态 | 设计已确认, 待 review → 转实施计划 |
| 子项目 | SP-1 (经验检索) 的**接入端** — 引擎已建 (57f2abc) + bug 修复 (7dae55b), 本 spec 让它"通电"到生产研报 |
| 跨仓 | fa (CLI 加 --json) + stocks (report_v2 改检索) |

## 背景与动机

SP-1 的 `KnowledgeIndex` 引擎已建好、bug 已修、索引已 build (256 文件 → 1131 chunks)、检索质量已实测精准 (反转因子→factor_insights / 游资票→pitfalls top-1 score 0.49 / 出货→bilibili_notes EV-003)。但**没接入生产研报** —— `report_v2._load_knowledge_pack` 仍把 6 个大文件**全文塞进 prompt** (~134KB ≈ 4-4.5 万 token/股, 10 股研报 = 40+ 万 token 纯知识注入, 大部分与当前股票无关)。

本 spec 把"全文塞"改成"语义检索 top-K", 让 sub-agent 拿到**精准相关**的经验而非整本书。

## 目标 / 非目标

### 目标
- `report_v2._load_knowledge_pack` 的 4 个"大杂烩"文件 (pitfalls/factor_insights/sentiment_summary/rules_learned) 改为语义检索 top-K
- 跨仓零代码耦合: stocks **不 import** financial_analyst, 不装 chromadb/BGE — 走**子进程调 fa CLI**
- 向后兼容: 检索任何失败 → 退回现有全文逻辑, 研报零退化
- 索引新鲜度: report_v2 跑前自动增量 build (一轮研报只 build 一次)

### 非目标
- 不改 SP-2 (FDR) / SP-3 (SHAP) 的接入 (各自独立, 后续)
- 不给 stocks 环境装 chromadb/sentence-transformers (走子进程避免)
- 不做 HTTP 服务化 (子进程够用)
- 不做 query 拼接策略的"最优化" (留 extra_query 参数可扩)
- analyst_playbook / rating_system **不改检索** (思维范本 + 评级规则必须完整, 保留全文)

## 关键约束 (实测确认)

| 约束 | 事实 | 设计影响 |
|---|---|---|
| 跨环境 | report_v2 在 stocks 环境; chromadb/BGE/fa **全没装**; 引擎在 base 环境 fa 包 | 走子进程调 base 的 `python -m financial_analyst.cli` |
| 零耦合 | PROJECT.md §1.4: stocks 不 import fa | 子进程 + JSON 通信 (CLI 子进程不算 import fa 代码) |
| CLI 现状 | `fa knowledge search` **无 --json** (只人类可读) | 第一步: fa CLI 加 --json flag |
| 检索质量 | 已实测精准 | query 拼接用 name+factor_signals+主力判断+风险 |

## 架构

```
fa 端 (base环境, 1处增量):
  knowledge_index/cli.py: search 加 --json flag → stdout 输出 [{source,section,text,score}...]
  (build 命令已有, 复用)

stocks 端 (stocks环境, report_v2.py):
  _FA_PYTHON = env FA_PYTHON | "D:/app/miniconda/python.exe"   # base 环境, 可 env 覆盖
  _KB_ENABLED = env REPORT_KB_SEMANTIC != "0"                  # 总开关 (逃生舱)

  _kb_build_incremental():  [main() 开头跑 1 次] 子进程 fa knowledge build, 失败静默
  _kb_retrieve(query, k):   子进程 fa knowledge search --json → list[dict] | None(异常)
  _load_knowledge_pack():   playbook+rating 全文 + (检索成功→retrieved_experience / 失败→4文件全文)
```

### 数据流
```
report_v2 main()
  → _kb_build_incremental()              [子进程, 增量刷新索引, 一轮研报 1 次]
  → 逐股 analyze_stock(TARGET)
      → _load_knowledge_pack(code, factor_signals=...)
          → query = f"{name} {factor_signals} {vol_judge} {whale_judge} 风险 失效 经验"
          → _kb_retrieve(query, k=8)      [子进程 fa knowledge search --json]
          → 成功: knowledge_pack.retrieved_experience = top-8 chunks, _kb_mode='semantic'
            失败: 4 大杂烩退回全文, _kb_mode='fulltext_fallback'
      → analysis_ctx['knowledge_pack'] → _agent_ctx/{CODE}.json → sub-agent 读
```

## 组件接口

### fa: `knowledge_index/cli.py` search 加 --json
```python
@app.command()
def search(query: str, k: int = 5, preview: int = 240,
           json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON to stdout"),
           ...):
    results = KnowledgeIndex(...).search(query, k=k)
    if json_out:
        import json as _json
        typer.echo(_json.dumps(
            [{"source": r.source, "section": r.section, "text": r.text, "score": r.score}
             for r in results], ensure_ascii=False))
        return
    # 现有人类可读输出原样保留
```
注: 警告/日志走 stderr (BgeEmbedder 的 FutureWarning 等), stdout 只出纯 JSON, 否则 stocks 端 json.loads 会被污染。实施期验证 stdout 干净。

### stocks: `report_v2.py` 新增
```python
import os, subprocess, json
_FA_PYTHON = os.environ.get("FA_PYTHON", "D:/app/miniconda/python.exe")
_KB_ENABLED = os.environ.get("REPORT_KB_SEMANTIC", "1") != "0"

def _kb_build_incremental() -> None:
    """跑前增量刷新索引 (一轮研报 1 次, 由 main() 调). 失败静默 → search 自然 fallback."""
    if not _KB_ENABLED:
        return
    try:
        subprocess.run([_FA_PYTHON, "-m", "financial_analyst.cli", "knowledge", "build"],
                       capture_output=True, timeout=180, env={**os.environ, "NO_PROXY": "*"})
    except Exception:
        pass

def _kb_retrieve(query: str, k: int = 8):   # -> list[dict] | None
    """语义检索. 任何异常 (非0/坏JSON/超时/FileNotFound) → None → 触发全文 fallback."""
    if not _KB_ENABLED:
        return None
    try:
        r = subprocess.run([_FA_PYTHON, "-m", "financial_analyst.cli", "knowledge",
                            "search", query, "--k", str(k), "--json"],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=120, env={**os.environ, "NO_PROXY": "*"})
        if r.returncode != 0:
            return None
        hits = json.loads(r.stdout.strip())
        return hits or None
    except Exception:
        return None

def _load_knowledge_pack(code, target_date=None, factor_signals="", extra_query=""):
    query = f"{_name_of(code)} {factor_signals} {extra_query} 风险 失效 经验".strip()
    hits = _kb_retrieve(query, k=8)
    base = {
        '_usage': '...(检索版说明: retrieved_experience=语义检索 top-8 最相关经验, 带 source/section 出处)',
        'analyst_playbook': _safe_read(STRATEGY_DIR/'analyst_playbook.md', 22000),  # 全文
        'rating_system':    _safe_read(STRATEGY_DIR/'rating_system.md'),            # 全文
        'morning_brief_today': _load_morning_brief(target_date),
        'mainlines_today':     _load_mainlines_today(target_date),
        'previous_history':    _extract_stock_history(code),
    }
    if hits is None:
        base.update({  # fallback: 4 大杂烩退回全文 (现有逻辑原样)
            'pitfalls': _safe_read(STRATEGY_DIR/'pitfalls.md'),
            'factor_insights': _safe_read(STRATEGY_DIR/'factor_insights.md'),
            'sentiment_summary': _safe_read(STRATEGY_DIR/'research'/'sentiment_summary.md', 12000),
            'rules_learned': _safe_read(STRATEGY_DIR/'rules_learned.md', 6000),
            '_kb_mode': 'fulltext_fallback',
        })
    else:
        base['retrieved_experience'] = hits
        base['_kb_mode'] = 'semantic'
    return base
```

### sub-agent 消费: `agent_prompts.py` 一句话改
knowledge_pack 说明段加: `retrieved_experience` = 语义检索到的最相关经验片段 (带 source/section 出处), 优先级等同原 pitfalls/factor_insights。两种模式 (semantic 字段 retrieved_experience / fallback 字段 pitfalls 等) agent 都认。

## 错误处理 (分层 fallback, 研报绝不中断)

| 失败点 | 处理 |
|---|---|
| build 子进程超时/失败 | 静默 pass, search 阶段自然 fallback |
| search 非0 / 坏JSON / 超时120s | `_kb_retrieve` 返 None → 全文 fallback |
| 索引空 (n_chunks=0) | search 返 [] → None → fallback |
| base python 路径不存在 (换机) | FileNotFoundError → None → fallback |
| 总开关 REPORT_KB_SEMANTIC=0 | 直接 fallback, 不起子进程 |

**最坏情况 = 退回现状全文, 研报功能零退化。**

## 测试

| 文件 | 覆盖 |
|---|---|
| stocks `strategy/test_kb_retrieve.py` (独立烟测, 符合项目无 pytest 惯例) | mock subprocess 合法 JSON → `_kb_retrieve` 解析正确; mock 非0/坏JSON/超时 → 均 None; `_KB_ENABLED=0` → None |
| fa `tests/test_knowledge_cli.py` (已有, 加 case) | `search --json` 输出合法 JSON, stdout 不含警告污染 |
| 手动集成验证 | 跑 `report_v2.py SH600519`, 验 `_agent_ctx/SH600519.json` 的 `knowledge_pack._kb_mode=='semantic'` 且 `retrieved_experience` 非空 |

## 性能

- **优化**: `_kb_build_incremental` 提到 `main()` 开头跑 **1 次** (非每股), 开销从 N×(build+search) 降到 1×build + N×search
- build ~5s (增量, mtime 驱动只 re-embed 改动) + search ~4s/股 (含 BGE 加载)
- token: 4 大杂烩全文 ~70K → top-8 chunks ~3K, **每股省 ~30K**, 10 股省 ~30 万 token

## 风险

1. **每股 1 次 search 子进程 ~4s** (BGE 模型每次加载). 可接受 (研报本就分钟级). 极致优化 (常驻 search 服务) 留 Phase 2。
2. **检索质量依赖 query 拼接** — 已实测精准, 但留 `extra_query` 参数可调。
3. **stdout 污染风险** — BgeEmbedder 有 FutureWarning, 必须确认走 stderr 不进 stdout JSON (实施期验证)。
4. **跨仓合规** — 实现后 grep 确认 stocks 无 `import financial_analyst` (只 subprocess + chromadb 库)。
5. **实施期待核实**: `_name_of(code)` 是占位 — report_v2 实际取股名走 `name_map.get(code)` (analyze_stock 已有 name_map), `_load_knowledge_pack` 需把 name/factor_signals 作参数传入 (现签名只有 code)。实施期改调用处 (line 1632) 传 factor_signals + name。

## 提交策略
- fa 端 (cli.py --json + 测试): 提交到 fa
- stocks 端 (report_v2.py + agent_prompts.py + 烟测): stocks 非 git, 改文件 + 追加 strategy/log.md
- 不 push (按"攒着一起推")

## 验收 DoD
- [ ] `fa knowledge search "rev_20" --json` 输出合法 JSON 数组, stdout 无警告污染
- [ ] `report_v2.py SH600519` 跑通, `_agent_ctx/SH600519.json` 的 `knowledge_pack._kb_mode=='semantic'` + `retrieved_experience` 非空且 source/section 合理
- [ ] `REPORT_KB_SEMANTIC=0` 跑同股 → `_kb_mode=='fulltext_fallback'`, 研报正常 (逃生舱验证)
- [ ] grep 确认 stocks report_v2 无 `import financial_analyst`
- [ ] fa 全量回归不破; stocks 烟测过
- [ ] strategy/log.md 追加一条
