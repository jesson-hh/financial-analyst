# HuggingFace 数据包发布指南

> 这一步只能 **maintainer 自己跑** (需要 HF token). 走完后 `fa init` 用户就能从
> 你 publish 的 dataset 下载历史数据.

## 1. 注册 HuggingFace + 拿 token

1. 浏览器开 https://huggingface.co/join (没账号) / https://huggingface.co/login
2. 完成注册 / 登录
3. Settings → Access Tokens → New token
4. Token type: **`Write`** (我们要 push dataset)
5. 名字: `financial-analyst-publish` (或随便)
6. 复制 token (`hf_...` 开头)

> Token 只显示一次. 记好.

## 2. 配 token

```bash
# 加到 .env (这样 publish_hf_dataset.py 自动读)
echo "HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxx" >> G:/financial-analyst/.env

# 或者临时 export 一次
export HUGGINGFACE_TOKEN=hf_xxxxxxxxxxxxxxxxx
```

验证:

```bash
G:/financial-analyst/.venv/Scripts/python.exe -c "
from huggingface_hub import HfApi
api = HfApi(token='你的_token')
print(api.whoami())
"
```

应输出 `{'name': '...', 'fullname': '...', 'email': '...'}`.

## 3. Publish 三档包

### 3a. demo (~500 MB, 优先发, 给 fa init 用)

```bash
G:/financial-analyst/.venv/Scripts/python.exe G:/financial-analyst/scripts/publish_hf_dataset.py \
    --preset demo \
    --repo jesson-hh/financial-analyst-data-demo
```

预期耗时:
- staging (本地打包): ~14s
- upload to HF: 10-30 min (看上行带宽, ~0.44 GB)

输出最后一行: `✓ Dataset live at https://huggingface.co/datasets/jesson-hh/financial-analyst-data-demo`

### 3b. lite (~5 GB, 给量化研究员)

```bash
python scripts/publish_hf_dataset.py \
    --preset lite \
    --repo jesson-hh/financial-analyst-data-lite
```

预期耗时:
- staging: ~3 min (含 5min 数据 copy)
- upload: 30 min - 2 hr (~5 GB)

### 3c. full (~50 GB, 给重度用户, 可选)

```bash
python scripts/publish_hf_dataset.py \
    --preset full \
    --repo jesson-hh/financial-analyst-data-full
```

预期耗时:
- staging: ~15 min
- upload: 2-8 hr (~50 GB, 看带宽)

> ⚠ HF dataset 单 LFS 仓库无硬上限, 但 50 GB 慎重 — 上传中断要重传整个. 建议先发 demo + lite, full 再说.

## 4. 验证 dataset 可访问

发布完后:

```bash
# 任何人不登录直接拉 (验证 public)
python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='jesson-hh/financial-analyst-data-demo',
    repo_type='dataset',
    local_dir='/tmp/test_download',
    allow_patterns=['cn_data/calendars/*', 'cn_data/instruments/*'],  # 只下小文件验证
)
import os
print(os.listdir('/tmp/test_download/cn_data/instruments'))
"
```

应能看到 `['all.txt']`. 失败 → repo 是 private 或者没 public 视图.

## 5. 让 fa init 用户能下到

`src/financial_analyst/init_cli.py` 里 `HF_PACKAGES` 字典的 `repo_id` 已经写好默认值
(`jesson-hh/financial-analyst-data-demo` 等), 这些 repo id 跟你发布时用的对齐, 用户跑
`fa init` 就能下到.

如果你用了不同的 repo id (例如自己 org), 改 `init_cli.py::HF_PACKAGES.{preset}.repo_id`
再发版.

## 6. 后续维护

- **每次数据 schema 改动** (加字段 / 改单位): 重新跑 publish, HF 自动覆盖. 老 commit 留在历史里
- **每月一次增量**: publish lite 包 (含本月 daily_basic 新增), 同步给用户
- **退市股清理**: 不需要主动删 — 历史 bin 文件留着对回测有用. 只在 instruments 文件标记 end_date

## 7. 故障排查

| 现象 | 原因 | 修 |
|------|------|------|
| `403 Forbidden` | token 没 Write 权限 | 重新生成 token 选 Write |
| `LFS upload failed` | 单文件 > 5GB 触发 LFS, 或 LFS quota 满 | 升级 HF Pro / 拆小文件 |
| upload 中途断 | 网络抖 | hf_hub 自动 resume, 重跑同命令即可 |
| `Repository not found` after create | HF 后台同步延迟 | 等 30s 重试 |
| dataset card README 没渲染 | YAML front-matter 错 | 检查脚本生成的 `staging/README.md` 头部 |

## 8. ModelScope 镜像 (可选, 国内更快)

阿里魔搭 ModelScope 跟 HF 协议兼容, 国内速度快 10x. 用 `modelscope_hub`:

```bash
pip install modelscope
modelscope login
modelscope hub create --type dataset jesson-hh/financial-analyst-data-demo
# 然后 git push 整个 staging dir
```

发布脚本里 `_upload` 函数已经预留, 加 `--mirror modelscope` 参数即可启用 (P3 工作量).

---

## 总结

```bash
# 一行 publish demo
HUGGINGFACE_TOKEN=hf_xxx python scripts/publish_hf_dataset.py \
    --preset demo --repo jesson-hh/financial-analyst-data-demo

# 验证
python -c "from huggingface_hub import snapshot_download; \
  snapshot_download('jesson-hh/financial-analyst-data-demo', repo_type='dataset', local_dir='/tmp/t', allow_patterns=['cn_data/instruments/*'])"

# 等 demo 跑通了, 再 lite + full
```
