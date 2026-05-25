# 离线数据安装 · 国内网盘下载

> **何时用这份**: 你跑 `fa init` 选数据包时, HuggingFace **下不动** (直连断 / 挂梯子也慢到无法忍). 国内用户从我们提供的网盘下载, 然后用 `fa data link` 接入工作目录, **完全跳过 HF**.

---

## 1. 为什么会下不动?

`huggingface.co` 在大陆没被完全封, 但:
- **TLS 干扰**: GFW 对 SNI 含 `huggingface` 的握手随机 RST, 表现是连一会儿就断, 下到 30% / 60% stuck
- **CDN 物理远**: HF 的 CDN 在 AWS us-east, 国内直连物理延迟 200ms+
- **挂 VPN 也慢**: VPN 节点本身带宽是瓶颈 (50-300 Mbps), HF 多连接并发反而不如单线; 部分商用 VPN 对大下载 throttle

我们在国内**阿里云盘**和**夸克网盘**做了镜像, 同源同 hash, 国内带宽全速。

---

## 2. 下载链接 (国内网盘)

| 数据包 | 体量 | 阿里云盘 | 夸克网盘 |
|--------|------|---------|----------|
| **demo** (CSI300, 演示) | ~155 MB | _[即将填入]_ | _[即将填入]_ |
| **lite** (CSI800 + 5min, 日常) | ~3 GB | _[即将填入]_ | _[即将填入]_ |
| **full** (全 A 股, 量化重度) | ~14 GB | _[即将填入]_ | _[即将填入]_ |

> 链接还在传, 后续补上. 也可以**先用 hf-mirror 镜像**直接 fa init: `set HF_ENDPOINT=https://hf-mirror.com && fa init`.

**用阿里云盘还是夸克?**

| | 阿里云盘 | 夸克 |
|---|---------|------|
| 速度 | 满速 (10+ MB/s 常见) | 满速 |
| 是否需要 VIP | 不需要 | 不需要 (普通账号能下) |
| 文件 zip 限制 | 单文件 ≤ 100 GB | 单文件较小, 我们拆成 zip 分卷 |
| 需要客户端? | 网页直接下, 大文件建议装 App | 同上 |

二选一即可, 都是同一份数据 (md5 校验过).

---

## 3. 下载 + 解压 (Windows 步骤)

### 3.1 下载

1. 打开上面表格中你选的网盘链接
2. (如果是夸克或阿里云盘 App 推荐链接) 装好客户端后, 把文件加入到自己的网盘 → 下载到本地
3. 下载位置选**剩余空间最多的盘**, 例如 `D:\` (不要下到 C 盘!)

下载完后你会得到一个 `.zip` 文件, 例如:
- `financial-analyst-data-demo.zip` (~155 MB)
- `financial-analyst-data-lite.zip` (~3 GB)
- `financial-analyst-data-full.zip` (~14 GB)

### 3.2 校验完整性 (可选但强烈建议)

下完 zip 之后, 验证一下没下坏. 在 `D:\` 黑窗口跑 (Windows 自带 `certutil`):

```cmd
certutil -hashfile financial-analyst-data-demo.zip SHA256
```

应该输出一长串 hex, 比对**网盘说明文字里贴的 hash**. 不一致 = 下崩了, 重下.

> hash 值我们会发布在网盘说明里, 例如 demo 的 SHA256 是 `[即将填入]`.

### 3.3 解压

右键 `.zip` → "全部解压缩" → 选目标目录, 例如 `D:\fa-data\`. **不要解到 OneDrive / 桌面**, 不然每次启动都会跟云同步, 巨慢.

解压后 `D:\fa-data\` 下应该有:

```
D:\fa-data\
├── cn_data\                ← 日线 OHLCV + 估值 + 因子 (Qlib bin)
│   ├── calendars\day.txt
│   ├── instruments\all.txt
│   └── features\sh600519\... (一只股票一个文件夹)
├── cn_data_5min\           ← 5min K 线 (lite / full 才有)
├── parquet\                ← 财务报表 / 板块 / F10 (lite / full)
└── news_data\              ← 新闻 SQLite (full)
```

**核对**: 必须有 `cn_data\calendars\day.txt` 和 `cn_data\instruments\all.txt` 这两个文件. 没有就是解压错了 (多套了一层目录) 或下载不完整.

---

## 4. 接入工作目录 (一行命令)

```cmd
fa data link --src D:\fa-data
```

预期输出:

```
=== fa data link ===
  源目录: D:\fa-data

  目录扫描:
    ✓ cn_data         (必需) [3,200,000 files]
    ✓ cn_data_5min    (可选) [180,000 files]
    ✓ parquet         (可选) [1,200 files]
    ✓ news_data       (可选) [3 files]

  ✓ cn_data 校验通过: 5,450 只 instruments, 8,797 天日历

  ✓ 写入 C:\Users\你\.financial-analyst\config\loaders.yaml
  ✓ last-update 时间戳已写 (day, 5min)

=== Link 完成 ===
  下一步:
    fa data status      # 验证数据接通
    fa report SH600519  # 跑第一份研报
```

如果某个**必需**目录 (`cn_data/`) 缺失, `link` 会拒绝继续, 提示你检查解压. 可选目录缺失会 warn, 加 `--force` 跳过.

**注意**: `fa data link` 不 copy 不 symlink, 只是在 `config/loaders.yaml` 里把路径指向你的 `D:\fa-data`. 所以**别删源目录, 否则数据就丢了**. 想要"完全独立 / 想删源" 的话, 把 `D:\fa-data\*` 复制到 `C:\Users\你\.financial-analyst\data\` 即可。

---

## 5. 验证

```cmd
fa data status
```

预期看到:

```
provider_uri (day): D:/fa-data/cn_data
  ✓ instruments: 5,450
  ✓ calendar:    8,797 days (1990-12-19 → 2026-XX-XX)
  ✓ sample SH600519 close.day.bin: range [4669, 8639] = 3,971 days

  上次更新:
    ✓ day           just now
    ✓ 5min          just now
    ...
```

跑成功就跑第一份研报:

```cmd
fa report SH600519
```

10 分钟左右出贵州茅台的完整研报.

---

## 6. 数据更新

网盘下来的是**快照** (截止到打包那天). 想拿到最新数据增量, 跑:

```cmd
fa data update         # 增量更新近 30 天日线 + 240 根 5min + 今日 PE/PB/MV
```

这一步走的是 **pytdx 主站直连** + **腾讯实时报价**, 国内直连, 0 token. 跟 HF 数据无关。

之后每天**只跑这一条**就行 — 网盘只用来 onboard 新机器, 跑起来后日常增量靠 `fa data update`.

---

## 7. 常见问题

### Q1: `fa data link` 报 "缺少必需子目录: ['cn_data']"

你解压时多套了一层目录. 进 `D:\fa-data\` 看一下, 是不是结构变成了 `D:\fa-data\financial-analyst-data-demo\cn_data\` 这种? 把里面那一层拽出来即可, 或者直接换路径:

```cmd
fa data link --src D:\fa-data\financial-analyst-data-demo
```

### Q2: 我已经跑过 `fa init` 下了点 HF 数据, 现在能切到网盘版吗

可以. `fa data link` 会覆盖 `config/loaders.yaml`, 自动把 provider_uri 切到你的网盘目录. 旧的 HF 下载残片你可以手动 `rmdir /s C:\Users\你\.financial-analyst\data` 删掉 (注意备份), 或者保留也无妨, 反正不被用。

### Q3: 解压 zip 进度卡在 99%

zip 大文件解压慢正常, 别打断. lite (3GB) 大约 5-10 分钟, full (14GB) 30-60 分钟. 用 **7-Zip** 比 Windows 自带解压快 3-5 倍, 推荐装一下。

### Q4: 没装 fa CLI, 想直接用网盘数据怎么办

先装包再 link:
```cmd
pip install financial-analyst
fa data link --src D:\fa-data
```

详细安装见 [`beginner_zh.md`](beginner_zh.md).

### Q5: 5min / parquet 缺失警告 (`fa data link --force`)

如果你下的是 **demo** 包, 它**本来就没有** 5min 和 financials, 这时加 `--force` 跳过警告:

```cmd
fa data link --src D:\fa-data\demo --force
```

之后 `fa report` 涉及 5min 因子的部分会 skip, 但日线研报正常.

### Q6: 想换回 HF 下载

跑 `fa init`, 数据包步骤选 1/2/3 (不要选 4 = skip), 会重新走 HF. 同时 `set HF_ENDPOINT=https://hf-mirror.com` 用镜像加速:

```cmd
set HF_ENDPOINT=https://hf-mirror.com
fa init --preset demo
```

---

## 8. 我自己想做镜像 / 改 ETL 流程

数据格式见 [`data_contract.md`](../data_contract.md). 简言之:
- 日线 / 5min: **Qlib 二进制** (`[4-byte float32 start_index] + [float32 array]`)
- 财务 / 新闻 / F10: **Parquet** (列存, 用 `pd.read_parquet()` 直接读)

复制 / 同步 工具推荐:
- 服务器之间: **rsync** (linux/mac) 或 **robocopy** (Windows)
- 移动盘装机: 直接拷 `cn_data/` 整个目录, 不需要打包

---

> *Last updated 2026-05-25 · financial-analyst v1.0.3*
