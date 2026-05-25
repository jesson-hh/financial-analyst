# Release Checklist — v1.9.5

> 这份 checklist 给 **maintainer** 用. 走完一遍把 v1.9.5 发布到 PyPI + DockerHub + HF.
> 用户不需要看, 用户跑 `pip install -U financial-analyst[serve]` 即可.

## 0. 前置: 验证

```bash
# 跑全部 pytest 三遍 (mocked LLM, 不需要 key)
cd G:/financial-analyst
pytest tests/ -q
# 应见: ~50+ passed

# 验证 CLI 全通
fa version                    # → 1.9.5
fa agents | head -3           # → 15 registered
fa data --help
fa init --help
fa dream --help

# 起 serve + smoke
financial-analyst serve --port 9999 &
sleep 3
bash scripts/smoke_test_serve.sh
# → "✓ All smoke tests passed."
```

如果有任一项失败, **不发版**.

---

## 1. PyPI — 最重要

### 1a. 装 twine (一次性)

```bash
pip install twine
```

### 1b. 上传到 TestPyPI 先 (强烈推荐)

```bash
# 1. 在 https://test.pypi.org 注册账号, 生成 token
# 2. 配 ~/.pypirc:
[testpypi]
  username = __token__
  password = pypi-xxx_test_token

[pypi]
  username = __token__
  password = pypi-xxx_real_token

# 3. 上传 testpypi
twine upload --repository testpypi dist/financial_analyst-1.9.5*

# 4. 验证 testpypi 装
python -m venv /tmp/test_pypi
/tmp/test_pypi/Scripts/pip install -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ financial-analyst==1.9.5
/tmp/test_pypi/Scripts/financial-analyst version    # → 1.9.5
```

### 1c. 上传到真 PyPI

```bash
twine upload dist/financial_analyst-1.9.5*
# 输入账号 + token (从 ~/.pypirc 或交互)

# 上传完去 https://pypi.org/project/financial-analyst/1.9.5/ 验证
```

### 1d. 通知用户

```
现有用户升级:
    pip install -U financial-analyst[serve]
新用户:
    pip install financial-analyst[serve]
    fa init
```

---

## 2. HuggingFace 数据包 (P1 工作, 一次性建)

参考 [`hf_publish_guide.md`](hf_publish_guide.md). 3 档:

```bash
# 1. 拿 HUGGINGFACE_TOKEN (Write 权限)
# 2. publish demo (~500MB)
python scripts/publish_hf_dataset.py \
    --preset demo \
    --repo jesson-hh/financial-analyst-data-demo

# 3. publish lite (~5GB)
python scripts/publish_hf_dataset.py \
    --preset lite \
    --repo jesson-hh/financial-analyst-data-lite

# 4. (可选) full (~50GB)
python scripts/publish_hf_dataset.py \
    --preset full \
    --repo jesson-hh/financial-analyst-data-full
```

发完用户跑 `fa init --preset demo` 自动下数据.

---

## 4. GitHub Release (可选)

```bash
# 1. Tag
git tag v1.9.5
git push origin v1.9.5

# 2. release.yml workflow 自动触发, build 三平台 .msi/.dmg/.AppImage
#    → 草稿 release 在 https://github.com/jesson-hh/financial-analyst/releases
# 3. 检查产物, 改 release notes, publish
```

要求装好 Rust toolchain + Tauri-cli, 见 [`tauri_packaging.md`](tauri_packaging.md).

---

## 5. 公告

发完之后通知:

### 5a. README badge 自动更新
- PyPI version badge 自动反映新版
- Tests badge 看 CI 是否绿

### 5b. CHANGELOG.md
已写好 v1.9.5 entry, push commit:

```bash
git add -A
git commit -m "release: v1.9.5"
git push
```

### 5c. 用户社群 (可选)
- 微博 / 知乎 / V2EX
- 模板:
> financial-analyst v1.9.5 发布:
> - 14-agent + Tier-4 introspector 自省层
> - pytdx 主站直连, 不再需要 Tushare token
> - dream loop 自动从 Tier-4 introspector 聚类升级规则
> - 完整 Docker + MCP + 三平台 .msi/.dmg/.AppImage
> - `pip install -U financial-analyst[serve]`

---

## 6. 回滚预案

```bash
# 如果发现 v1.9.5 严重 bug:
# a. PyPI 不能删 release, 但可以 yank (用户 pip install 不再用):
twine upload --skip-existing --repository pypi dist/financial_analyst-1.9.5*
# 控制台 https://pypi.org/manage/project/financial-analyst/release/1.9.5/ → Yank release

# b. DockerHub 删 tag:
docker manifest rm jessonhh/financial-analyst:1.9.5

# c. 立刻发 v1.9.6 patch fix
```

---

## 7. Post-release 跟踪

发后 24h 关注:
- PyPI 下载量 (https://pypistats.org/packages/financial-analyst)
- GitHub Issues 新增数
- 用户反馈 (微博/知乎/邮件)

如果 24h 内 ≥3 个用户报同一 bug → hot patch 1.9.6.

---

## 当前 v1.9.5 改动 summary

详见 [`CHANGELOG.md`](../../CHANGELOG.md) v1.9.5 段. 主要:

1. **Tier-4 introspector** + bull/bear retry + writer Pydantic validators (14 agents 总)
2. **pytdx 主站直连** + `fa data update` (替代 Tushare 路径, 0 token)
3. **dream aggregator** 自动从 _pending_introspections 聚类 → _proposed/
4. **MCP +1 tool** (dream_aggregate), 总 13
5. **buddy SSE +5 endpoints** (/diag /lesson /report-progress /xueqiu/* /resolve)
6. **buddy +1 tool** (update_data), 总 30
7. **fa init wizard** + Docker multi-stage + PyInstaller 129MB exe + Tauri 骨架
8. **9 篇新 docs** + CHANGELOG v1.9.5

---

## Quick-Reference: 一行命令

```bash
# 一切 OK 的话, 发布全套:
pytest tests/ -q && \
    python -m build --wheel --sdist --outdir dist/ && \
    twine upload dist/financial_analyst-1.9.5* && \
    git tag v1.9.5 && git push origin v1.9.5

# DONE 🎉
```
