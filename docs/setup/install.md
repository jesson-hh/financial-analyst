# Install — 三条路径

`financial-analyst` 提供三种安装方式. 按"我是谁"选:

| 我是 | 路径 | 一行命令 |
|------|------|---------|
| 个人用户 / 试用 | A. **pip from PyPI** | `pip install financial-analyst` |
| 开发者 / 想改源码 | B. **pip editable from source** | `git clone ... && pip install -e .[dev]` |

完成任一路径后, 跑 `fa init` 完成首启配置 (见 [`zero_to_report.md`](zero_to_report.md)).

---

## A. PyPI (推荐, 1 分钟)

适合: **你只想用, 不打算改源码**.

```bash
# 1. 建 venv (避免污染系统 Python)
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

# 2. 装包 (一行搞定, 所有运行时依赖含 GuanLan UI 后端的 fastapi/uvicorn 都自动拉)
pip install financial-analyst

# 3. 首启 wizard (引导 LLM key + 数据包 + 验证)
fa init

# 4. 第一份研报
fa report SH600519
```

`pip install financial-analyst` 自动拉所有运行时依赖:
- litellm / pydantic / pandas / numpy / pyarrow / lightgbm
- tushare / **pytdx** (主站直连, 无 token) / mcp / huggingface-hub
- **fastapi / uvicorn** (核心起 GuanLan UI 后端用, v1.0.3 起已并入 core)

> 历史包袱: v1.0.2 之前 `fastapi/uvicorn` 是 `[serve]` 可选 extra, 现在并入 core 后用户不需要再敲 `[serve]`. 老的 `pip install financial-analyst[serve]` 命令仍然能跑 (pip 忽略不存在的 extra), 但已没必要.

### 升级
```bash
pip install -U financial-analyst
```

### 验证
```bash
fa version                # → financial-analyst 1.9.4
fa agents                 # → 15 registered sub-agent(s)
fa data --help            # 看 status / update / bootstrap
financial-analyst-mcp     # 启 MCP stdio mode (没输出, Ctrl+C 退). 这个 binary 在
                          # Claude Desktop / Claude Code config 里引用
```

---

## B. Source (开发者, 5 分钟)

适合: **你想改 agent prompt / 加新工具 / 测试本地分支**.

```bash
# 1. 克隆 (or fork)
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst

# 2. venv
python -m venv .venv
.venv\Scripts\activate

# 3. editable install + dev extras (pytest, ruff, build, twine)
pip install -e .[dev]

# 4. 验证 (跑全部 pytest)
pytest tests/ -q
# 应该看到: XX passed in XXs

# 5. 用 (跟 A 路径一样)
fa init
fa report SH600519
```

**editable install (`-e .`) 关键好处**: 改 `src/financial_analyst/*.py` 立即生效, 不需要 reinstall.

不想要 dev 工具 (例如 CI 镜像):
```bash
pip install -e .           # 最小 editable, 不带 pytest/ruff/build/twine
```

### 用本地 wheel (CI / 离线安装)

```bash
# 装 build
pip install build

# 出 wheel
python -m build --wheel --outdir dist/

# 装本地 wheel (跟 PyPI 一致, 但来自本地)
pip install dist/financial_analyst-*.whl
```

---

## 系统要求

| 项 | 最低 | 推荐 |
|----|------|------|
| Python | 3.11 | 3.12 |
| OS | Windows 10 / macOS 12 / Linux | Windows 11 / macOS 14 / Ubuntu 24.04 |
| 内存 | 4 GB (单股研报) | 16 GB (并发 / 大批量) |
| 磁盘 | 1 GB (CLI + demo data) | 60 GB (全 A 股 + 5min) |
| 网络 | 国内直连即可 (pytdx 主站 / 腾讯 / 阿里云百炼) | + VPN (海外 LLM 可选) |

不需要 GPU. lightgbm 用 CPU 跑.

---

## 没有 LLM key 怎么办

| 选项 | 说明 |
|------|------|
| **阿里云百炼 (推荐)** | https://bailian.console.aliyun.com/ — 注册送 100w token (~150 份研报). DASHSCOPE_API_KEY |
| OpenAI | 海外信用卡, gpt-4o-mini 比较便宜 |
| Anthropic | claude-3-5-sonnet, 海外信用卡 |
| DeepSeek | 国内手机号, 便宜. 但 financial-analyst 环境实测 SSL fail |

`fa init` 会引导填. 至少要一个.

---

## 没有 Tushare token 怎么办

**不需要**. 默认走 pytdx 主站直连 + 腾讯实时, 0 token 0 注册.

如果你**有** Tushare Pro token, 走老路径用 ``incremental_update_tushare`` 拉完整 daily_basic 历史 (含 ps_ttm/dv_ttm), 配 `TUSHARE_TOKEN` 在 .env 即可. fa data update 仍走 pytdx (pytdx 数据更准, Tushare 更全).

---

## 常见故障

### `financial-analyst-mcp` 命令不存在

```bash
pip install --force-reinstall financial-analyst
# 或在已激活的 venv 里:
pip install --upgrade financial-analyst
```

### `import pytdx ModuleNotFoundError`

只发生在用很旧的 wheel install (≤ 1.9.4 之前). 升级到 ≥ 1.9.5:
```bash
pip install -U financial-analyst
```
(pytdx 在 1.9.5 加进 dependencies.)

### Docker build OOM (内存不足)

lightgbm 编译需 ~2 GB. Docker Desktop 默认给 2 GB, 跑到 90% 就杀. 改 docker resources → 6 GB:
- macOS: Docker Desktop → Settings → Resources → Memory → 6 GB
- Windows: 同上

### `fa init` 卡在数据下载

HF dataset repo 还没 publish, 或国内访问 hf.co 慢:
```bash
# 跳过数据包, 用 Tushare / 已有 Qlib bin
fa init --yes --preset skip
# 然后 cp 你的 cn_data → ~/.financial-analyst/data/cn_data/
# 或编辑 config/loaders.yaml 指向本地路径
```

详见 [`hf_publish_guide.md`](hf_publish_guide.md).

### Windows console 乱码

```cmd
set PYTHONIOENCODING=utf-8
chcp 65001
```
或装 Windows Terminal + PowerShell 7 (UTF-8 默认开).

---

## 下一步

装好后:
- [zero_to_report.md](zero_to_report.md) — 0 到第一份研报 60min walkthrough
- [data_pipeline.md](data_pipeline.md) — 数据流细节
- [mcp.md](../mcp.md) — MCP integration (Claude Desktop / Claude Code 等)
- [ui/guanlan_user_guide.md](../ui/guanlan_user_guide.md) — GuanLan UI 操作
