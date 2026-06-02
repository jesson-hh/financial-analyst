# 经验检索 + FDR 校正 + SHAP 可解释性 · 设计

> 状态: 设计审中, 待 user 确认后进 workflow
> 日期: 2026-06-02
> 子项目: 量化研究专业化升级 (3 独立模块, 串行交付)

## 目标
1. **SP-1 向量化经验检索**: sub-agent 知识注入从 "全文 keyword 拼接" 升级为 "语义检索 top-K"
2. **SP-2 FDR 多重检验校正**: 因子筛选加 Benjamini-Hochberg 校正, 防 442 alpha 扫描出 ~22 个假阳性
3. **SP-3 SHAP 模型可解释性**: LGB 模型出每股 top-5 因子贡献分解

## 工作量估算
- SP-1: ~3-4 天 (核心)
- SP-2: ~1 天
- SP-3: ~2 天
- 合计 ~6-7 天, 串行执行

---

## SP-1 向量化经验检索

### 新模块: `financial_analyst/data/knowledge_index/`

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包入口, export KnowledgeIndex |
| `chunker.py` | `chunk_markdown(path) -> List[Chunk]` 按 H2 切块, 保留 source_file + section_h2 + text + mtime |
| `embedder.py` | `BgeEmbedder` 用 sentence-transformers 加载 `BAAI/bge-large-zh-v1.5`, `encode(texts) -> np.ndarray` |
| `store.py` | `ChromaStore(root)` 包装 chromadb PersistentClient, upsert/query 接口 |
| `indexer.py` | `KnowledgeIndexer.build()` 扫 strategy/ → 切块 → embed → upsert. 增量靠 mtime |
| `search.py` | `KnowledgeIndex.search(query, k=5) -> List[ChunkResult]` |
| `cli.py` | `fa knowledge build / search / stats` (typer) |

### 数据契约
- **源** (read-only): `G:/stocks/strategy/{pitfalls,factor_insights,rating_system,rules_learned}.md` + `research/*.md` + `wisdom/*.md` + `stocks/*.md`
- **索引落盘**: `G:/stocks/stock_data/knowledge_index/chroma/` (shared 共享盘)
- **Chunk schema**: `{id, source_file, section_h2, text, embedded_vec, mtime, tokens, lang}`
- **Collection**: 单 collection 名 `strategy_knowledge` (Phase 1+ 可拆 namespace)

### API
```python
from financial_analyst.data.knowledge_index import KnowledgeIndex
idx = KnowledgeIndex()                        # 默认路径来自 DataPaths
idx.build(force=False)                        # 全量, 增量靠 mtime 比对 (默认)
idx.search("反转因子 失效场景", k=5)
# → [ChunkResult(text='...', source='strategy/factor_insights.md', section='rev_20 历史', score=0.87), ...]
```

### CLI
```bash
fa knowledge build                            # 增量构建
fa knowledge build --force                    # 全量重建
fa knowledge search "反转 失效" --k 5         # 命令行查
fa knowledge stats                            # 索引大小 / 上次更新 / 源文件覆盖率
```

### 接入 (跨仓: stocks 直接调 chromadb 库)
`G:/stocks/strategy/report_v2.py` 的 `knowledge_pack` 生成处:
- **旧**: 把 pitfalls/factor_insights/rating_system 等整段拼字符串塞进 sub-agent prompt
- **新**: 按 query=`f"{code} {factor_signals} {agent_role}"` 检索 top-5 chunks 拼成更精准 context
- stocks 端**直接 import chromadb** + 读 `stock_data/knowledge_index/chroma/` (chromadb 是公开库, 不算跨仓 import financial-analyst 代码)

### 跨仓边界
| 仓库 | 行为 |
|---|---|
| financial-analyst | 实现 KnowledgeIndex (chunker/embedder/store/indexer/search/cli); **只读** stocks/strategy/* MD; **只写** stock_data/knowledge_index/ |
| stocks | report_v2.py 直接调 chromadb 库 (库, 非我们代码) 读共享 chroma store; **不 import** financial_analyst |
| 数据 | knowledge_index 在 stock_data/ 共享盘 |

### 配置扩展
`financial_analyst/data/paths.py` `DataPaths` 加两字段:
- `strategy_root: Path` (默认 `parquet_root.parent.parent / "strategy"`)
- `knowledge_index_root: Path` (默认 `parquet_root.parent / "knowledge_index"`)
- 都可 env 覆盖: `FA_STRATEGY_ROOT` / `FA_KNOWLEDGE_INDEX_ROOT`

### 新依赖 (3 个, 都 Apache-2.0 / MIT)
- `chromadb >= 0.5` — 嵌入式向量库
- `sentence-transformers >= 3.0` — BGE 模型加载/推理
- `torch >= 2.0` (sentence-transformers 传递依赖, 通常已装)

### 模型 (首次自动下载)
- `BAAI/bge-large-zh-v1.5` (~340MB) → `~/.cache/huggingface/`
- 中文检索 SOTA (CC-by-NC-4.0 模型权重 / Apache-2.0 代码), 零 API 成本

### 测试
| 文件 | 覆盖 |
|---|---|
| `tests/test_knowledge_chunker.py` | 合成 MD → H2 切块计数 / 无 H2 fallback / 跨 H2 boundary |
| `tests/test_knowledge_embedder.py` | stub embedder (random vec), 验输入 list[str] → output (n, 1024) np.ndarray, 真模型烟测 (mark slow, 默认 skip) |
| `tests/test_knowledge_store.py` | tmp chroma store, upsert + query + delete; 跨实例持久化 |
| `tests/test_knowledge_indexer.py` | 假 strategy/ → index → 查命中正确 source/section; mtime 增量 (改一个文件 + rebuild, 只重 embed 改动的) |
| `tests/test_knowledge_cli.py` | typer CliRunner build / search / stats |

---

## SP-2 FDR 多重检验校正

### 改 3 个文件
1. **`factors/eval/config.py`** `EvalConfig`:
   - 加 `fdr_method: Optional[Literal['bh', 'bonferroni']] = 'bh'`
   - 加 `fdr_alpha: float = 0.05`

2. **`factors/eval/ic.py`** `IcResult`:
   - 加 `p_value: Optional[float] = None` (单因子 IC t-test p)
   - 加 `fdr_q: Optional[float] = None` (批量校正后 q-value, 单因子模式留 None)
   - 加 `is_significant: bool = False`

3. **`factors/zoo/bench_runner.py`** `run_bench`:
   - 跑完所有因子后, 收集 `p_value` 列 → `statsmodels.stats.multitest.multipletests(method='fdr_bh')` → 回填 `fdr_q` + `is_significant`
   - 输出 DataFrame 新增列: `p_value`, `fdr_q`, `is_significant`

### API 改变
```python
# 单因子: 同前, IcResult.fdr_q = None (单因子无法 FDR)
rep = factor_report('rev_20', cfg)
rep.ic.p_value         # 新字段
rep.ic.fdr_q is None   # True (单因子不批校正)

# 批量: bench_runner 自动应用
df = run_bench(panel, family='alpha101', fwd_days=5)
# df 新列: p_value, fdr_q, is_significant
sig = df[df.is_significant]  # FDR-adjusted 通过 (一般 << raw p<0.05)
```

### UI
- `quant.jsx` 因子库列表行加 "✓ FDR" 徽章 (绿色 if is_significant, 灰 if not)
- `/factor/bench` 端点 rows 自动含 `fdr_q` + `is_significant` (走 `_jsonable`)

### 回填任务 (实施期手动, 不进 spec)
跑 `factor_report` on 442 alpha → 收集 IC p → BH 校正 → 在 `factor_insights.md` 顶部加 "## FDR-adjusted 通过子集 (2026-06-02)" 段, 列 q<0.05 子集 (估计 20-50 个)

### 测试
| 文件 | 覆盖 |
|---|---|
| `tests/test_fdr_correction.py` | 构 100 个合成因子 (50 真信号 IC~0.05 + 50 纯噪声 IC~0) → 验 BH 校正后 false positive rate ≤ fdr_alpha=0.05 |
| 同上 | `fdr_method='bonferroni'` 验 alpha/n 阈值 |
| 同上 | `fdr_method=None` 验关闭 (向后兼容, fdr_q/is_significant 全 None/False) |
| 改 `tests/test_factor_rest.py` | 验 /factor/bench 返回含新字段 |

---

## SP-3 SHAP 可解释性

### 新文件 `financial_analyst/factors/eval/shap_explain.py`
```python
import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from typing import Dict, List, Tuple

def shap_top_k(
    model: lgb.Booster,
    feature_matrix: pd.DataFrame,     # index=code, columns=factor names
    k: int = 5,
) -> Dict[str, List[Tuple[str, float]]]:
    """每只股票算 SHAP, 返 {code: [(factor, contrib_signed), ...] sorted desc by |contrib|}.

    contrib 是有符号的 (正=推涨 / 负=推跌). 调用方按 abs 排可, 按 signed 排可.

    实现: shap.TreeExplainer(model).shap_values(feature_matrix) → (n, p) ndarray,
    每行取 |contrib| top-k, 返 dict.
    """
    ...
```

### 接入
- **financial-analyst 端**:
  - `factors/compose/compose.py` ComposeResult 加可选字段 `composite_shap_top5: Optional[Dict] = None` (lgbm 方法时填充)
  - 测试覆盖 `tests/test_shap_explain.py`
- **stocks 端** (独立, 不 import financial-analyst):
  - `G:/stocks/strategy/report_v2.py` 直接 `import shap` 库, 训 LGB 之后算 SHAP, 输出 `model_shap_top5: Dict[code, List[(factor, contrib)]]` 进 `_agent_ctx/{CODE}.json`
  - `G:/stocks/strategy/v4_ranking.py` 同样直接调 shap, 排名结果可选含 top-5 contrib

### UI
- `G:/stocks/app/pages/3_个股分析.py` (Streamlit) 加 "因子贡献分解" 横条 mini-panel (top-5)
- `quant.jsx` SHAP 视图 = **本次不做**, 留 Phase 2

### 新依赖
- `shap >= 0.46` (MIT)

### 测试
| 文件 | 覆盖 |
|---|---|
| `tests/test_shap_explain.py` | 训 LGB 在合成数据 (3 真信号 + 7 噪声因子, n=200 样本) → `shap_top_k(k=3)` 返 dict, 验 top-1 命中真信号 (≥80% 案例) |

---

## 跨切关注

### 提交策略
- **单分支** `feat/knowledge-fdr-shap` 三 commit (SP-1 / SP-2 / SP-3 分别提交)
- 不推 origin (按 "保留等一起推" 模式)
- 可选: 一次性 ff-merge 到 main 后, 一次性推

### 工作流
- 一个 workflow, 3 阶段串行: SP-1 (新模块多文件) → SP-2 (改 3 文件) → SP-3 (新+改 2 文件)
- 每阶段含: 实现 + 测试; **不 commit** (控制端审)
- 最后阶段: 全量回归 + 验收报告
- **不做 parallel** agent on protocol-layer files (上次 Phase 0 教训)

### 不做
- 不做 FAISS / Qdrant / Pinecone (ChromaDB embedded 够用)
- 不做向量索引的 cross-repo 客户端服务化 (stocks 直接读 chromadb)
- 不做 SHAP 的全市场预计算 / 缓存 (按需算)
- 不做 FDR 的复杂方法 (storey q-value / SGoF), 只支持 BH 和 Bonferroni
- 不做 quant.jsx 的 SHAP 详情面板 (留 Phase 2)
- 不做 BGE 模型的 fine-tune (零样本足够)

### 验收 DoD
- [ ] `fa knowledge build` 全量索引 strategy/ (估 ~500 chunks), `search "rev_20"` 返 top-5 含 factor_insights 反转章节
- [ ] `run_bench(family='alpha101')` 输出 df 含 `p_value` / `fdr_q` / `is_significant` 列; sig 子集 << raw p<0.05
- [ ] `compose_factors(method='lgbm')` 结果可选含 SHAP top-5
- [ ] stocks 端 `report_v2.py` 跑一只股票, `_agent_ctx/*.json` 出现 `model_shap_top5` + knowledge_pack 改为检索结果
- [ ] 全量回归 1212 → ~1280+ (新增测试) 不破
- [ ] 三个新依赖加进 `pyproject.toml` core 依赖
- [ ] 工作分支 main 不动, 改在 `feat/knowledge-fdr-shap`
