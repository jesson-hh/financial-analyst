# 部署指南 (Production Deployment)

> 把 financial-analyst 跑成长期服务的几种方式. 包括 Docker 自托管 / 云部署 /
> 监控 / 安全 / 备份. 个人用走 ``fa init`` 单机就够, 这份文档是给**多用户共享 /
> 团队/小机构** 部署用.

## 一、部署形态对比

| 形态 | 用户数 | 数据规模 | 运维成本 | 适合 |
|------|------|---------|---------|------|
| **A. 单机桌面** (``fa init``) | 1 | demo ~500MB | 0 | 个人研究员 |
| **B. Docker Compose 自托管** | 1-5 | lite ~5GB | 小 | 团队内部 NAS / 工作站 |
| **C. 云 VPS (Aliyun ECS / DigitalOcean)** | 5-20 | full ~50GB | 中 | 小机构 / 群组 |
| **D. K8s 集群** | 20+ | full + 多副本 | 高 | 商业部署 |

本文档主讲 B 和 C. D 留给后续 (P5).

---

## 二、Docker Compose 自托管 (推荐)

### 2.1 现状 (已 ship 的 docker-compose.yml)

```yaml
services:
  fa:
    build: .
    image: financial-analyst:latest
    env_file: [.env]
    volumes:
      - ./out:/app/out
      - ./news:/app/news
      - ./f10:/app/f10
      - ./memories:/app/memories
      - ~/.financial-analyst/cache:/root/.financial-analyst/cache
    stdin_open: true
    tty: true
```

跑:
```bash
docker compose up                              # 默认 TUI 模式
docker compose run --rm fa report SH600519     # 一次性研报
docker compose run --rm fa data update         # 增量更新
docker compose run --rm fa ask "PE of SH600519"
```

### 2.2 扩展: 加 SSE backend service

UI 桌面工作站需要 fastapi/uvicorn HTTP/SSE. 在 docker-compose.yml 加 ``serve`` service:

```yaml
services:
  fa:
    build: .
    # ...上面的 fa service 保留, 用于 CLI 一次性...

  serve:
    image: financial-analyst:latest
    depends_on: [fa]    # 借用 build
    command: serve --port 9999 --host 0.0.0.0
    env_file: [.env]
    ports:
      - "127.0.0.1:9999:9999"   # 只暴露 localhost, 不对外
    volumes:
      - ./out:/app/out
      - ./memories:/app/memories
      - ~/.financial-analyst/cache:/root/.financial-analyst/cache
      - ~/.financial-analyst/data:/root/.financial-analyst/data   # Qlib bin (只读)
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9999/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Dockerfile 改成默认装 [serve]:
```dockerfile
RUN pip install -e .[serve]
```

启动:
```bash
docker compose up -d serve                  # 后台跑后端
curl http://127.0.0.1:9999/health           # 验证
```

### 2.3 数据初始化

```bash
# 在容器外手工 copy Qlib bin 到 ~/.financial-analyst/data/
mkdir -p ~/.financial-analyst/data
cp -r /path/to/cn_data ~/.financial-analyst/data/

# 或在容器内跑 wizard (需要交互 TTY)
docker compose run --rm fa init --preset demo

# 或跑 bootstrap (非交互)
docker compose run --rm fa data bootstrap --preset demo
```

### 2.4 每日 cron 增量

```cron
# crontab -e
0 16 * * 1-5 docker compose run --rm fa data update --skip-5min --skip-basic
0 17 * * 5   docker compose run --rm fa data update    # 周五全量含 5min
```

---

## 三、云部署 (Aliyun ECS 示例)

### 3.1 实例规格

| 用户数 | CPU | 内存 | 磁盘 | 价格估 |
|------|------|------|------|------|
| 1-3 | 2 vCPU | 4 GB | 60 GB SSD | ¥120/月 |
| 5-10 | 4 vCPU | 8 GB | 100 GB SSD | ¥300/月 |
| 10-20 | 8 vCPU | 16 GB | 200 GB SSD | ¥600/月 |

LLM 是主要 IO 等待源, CPU 利用率低. 4 vCPU + 8 GB 跑全量 14-agent 没问题.

### 3.2 部署步骤

```bash
# 1. ssh 进 ECS (Ubuntu 22.04)
ssh root@<your-ecs-ip>

# 2. 装 docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# 3. clone
cd /opt
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst

# 4. 配 .env
cp .env.example .env
vim .env   # 填 DASHSCOPE_API_KEY + TUSHARE_TOKEN (可选)

# 5. 数据
fa data bootstrap --preset lite          # ~5GB, 国内 HF CDN 偶尔慢
# 或: scp 本地的 cn_data 到 /opt/financial-analyst/data/

# 6. 起 serve
docker compose up -d serve

# 7. 反向代理 (见 §4)
```

### 3.3 反向代理 + HTTPS (Caddy 推荐)

`/etc/caddy/Caddyfile`:
```
fa.yourdomain.com {
    # 限定来源 IP (可选)
    @allowed {
        remote_ip 1.2.3.4/32 5.6.7.0/24   # 你的家庭网段
    }
    handle @allowed {
        reverse_proxy localhost:9999
    }
    respond 403
    # Basic auth (可选)
    basicauth /* {
        admin $2a$14$...   # caddy hash-password
    }
}
```

Caddy 自动签 Let's Encrypt 证书, HTTPS 零配置. nginx 也行但要自己折腾 certbot.

### 3.4 前端 (GuanLan UI)

后端跑在 ECS, 前端可以:
- **A. 一起跑** — 装 ``python -m http.server`` 8888 端口给 fa_ui_ready, Caddy 反代到 /
- **B. 用户本地 Tauri 桌面包** — 桌面 app 配 `GUANLAN_BACKEND=https://fa.yourdomain.com:443` 远端连服务器, 数据 + LLM 全在云端

B 体验最好 (用户机器 0 配置), 但前端 build 时配置远端 URL.

---

## 四、监控

### 4.1 健康检查

```bash
# 简单 health
curl https://fa.yourdomain.com/health
# {"ok": true, "version": "1.9.4", "tools": 30}

# 深度探活 (5 源 + LLM)
curl https://fa.yourdomain.com/diag?quick=1
```

### 4.2 日志

financial-analyst 本身用 Python logging. Docker Compose 默认到 stdout:

```bash
docker compose logs -f serve         # tail logs
docker compose logs --since 1h fa    # 过去 1h
```

进生产: 走 Loki / journald / CloudWatch (按平台).

### 4.3 Prometheus (可选)

加 metrics endpoint (未来工作 P5). 当前 ``/diag`` 已经返回 rate_limit_stats:

```bash
curl /diag?quick=1 | jq '.rate_limit_stats'
# {
#   "xueqiu": {"calls": 12, "retries": 0, "throttled": 0, ...},
#   "tushare": {...}, ...
# }
```

写个轻量 exporter 解析这个 + 推 Prometheus.

### 4.4 报告产出监控

```bash
# 看每日 batch 跑完没
ls -la out/*$(date +%Y-%m-%d)*.md | wc -l

# alert: 今天 0 份 → cron + curl 推 webhook
```

---

## 五、Secrets 管理

### 5.1 .env 文件

最简单. **不要 commit**:
```
.env
.env.local
.env.bak*
```
已在 .gitignore.

### 5.2 容器 secrets

更稳: 用 Docker secrets 或 Aliyun KMS:

```yaml
# docker-compose with secrets
services:
  serve:
    secrets:
      - dashscope_key
      - tushare_token
secrets:
  dashscope_key:
    file: /run/keys/dashscope
  tushare_token:
    file: /run/keys/tushare
```

容器内读 `/run/secrets/dashscope_key`. financial-analyst 还没集成 (走 env 变量), P4 工作.

### 5.3 LLM cost 控制

`config/llm.yaml` 可设 per-agent 模型偏好. 跑 cheap 模型 (qwen3.5-flash if available) 降本.

设硬上限:
```bash
# pre-commit hook 防止意外大 batch
# 或 cron 监控本月 token 用量, 超阈值告警
```

---

## 六、备份策略

### 6.1 啥要备份

**必须**:
- `memories/` — 用户经验沉淀, 没了重建成本极高
- `out/` — 研报历史
- `~/.financial-analyst/data/conversations/` — 多会话历史

**可选**:
- `cn_data/` Qlib bin — 可从 HF 重下, 但全量 ~50GB 重下 1-2 小时
- LLM cache (`~/.financial-analyst/cache/`) — 完全可重建, 不备份

### 6.2 备份方案

#### 简单: rsync to NAS / OneDrive
```cron
0 3 * * * rsync -av --delete /opt/financial-analyst/memories /opt/financial-analyst/out user@nas:/backup/fa/
```

#### 中: borgbackup (增量 + 加密 + 去重)
```bash
borg init --encryption=repokey-blake2 user@backup:/opt/borg/fa
borg create user@backup:/opt/borg/fa::'{hostname}-{now}' \
    /opt/financial-analyst/memories /opt/financial-analyst/out
```

#### 高: 对象存储 (Aliyun OSS / S3)
- restic + S3 backend
- 每日增量, 跨地域复制
- 保留策略: 日 7 / 周 4 / 月 12

---

## 七、安全 checklist

| 项 | 实施 |
|----|------|
| `.env` 不 commit | ✓ .gitignore |
| `127.0.0.1:9999` 不对外 | ✓ docker compose 默认 |
| CORS 限定 origin (生产) | ⚠ 当前 `*`, P4 改 |
| HTTPS | Caddy / nginx + Let's Encrypt |
| Basic auth (公网暴露时) | Caddy basicauth |
| Tushare token 限 IP | tushare.pro 控制台设白名单 |
| LLM token 配额告警 | 阿里云 / OpenAI 控制台 |
| 容器 non-root | Dockerfile 加 `USER` (P4) |
| Image vulnerability scan | trivy / docker scan |
| Image signing | cosign (P4) |

---

## 八、扩展性

### 8.1 多副本 LLM 并行

现在 14-agent 是单进程 asyncio.gather. 一份研报 ~7 min. 10 个用户并发 → 10 个进程, 内存 ~30GB.

横向扩展:
- 多个 `serve` 副本 (port 9999, 9998, ...)
- 前端 nginx round-robin
- session_id 走 sticky session (用户 IP 哈希)
- 共享 memory + out (NFS / 对象存储)

### 8.2 LLM 集中化

```yaml
# config/llm.yaml
default:
  provider: dashscope
  model: qwen3.5-plus
  base_url: http://internal-llm-proxy:8080   # 内部统一网关
```

内部 LLM gateway 做:
- 请求合并 (相似 prompt 缓存)
- Cost tracking per-user
- Rate limiting per-team

---

## 九、Cost 估算 (单用户云部署月度)

| 项 | 金额 (¥) |
|----|---------|
| ECS 4 vCPU 8 GB | 300 |
| 公网带宽 5 Mbps | 80 |
| 阿里云盘 100 GB SSD | 50 |
| LLM (qwen3.5-plus ~150 份研报) | 100 |
| Tushare (免费等级) | 0 |
| 域名 + Caddy 证书 | 5 |
| **总月度** | **~535** |

5 个用户共享同一实例: 增量 ~¥80 (LLM). **总 ~¥620 / 5 用户 = ¥124/人/月**.

商业部署需要利润空间, 单用户定价 ¥300-500/月合理.

---

## 十、常见故障 + 应对

| 故障 | 表现 | 排查 |
|------|------|------|
| LLM 超时 / 限速 | report 卡在某个 Tier 几十分钟 | 看 /diag 的 llm latency_ms; 切备用 provider |
| Tushare token 失效 | data update 报 "您的token不对" | tushare.pro 控制台重生成 + 改 .env |
| Disk full | `cn_data/` 撑爆 | 删 ~/.financial-analyst/cache/ + parquet 老备份 |
| Memory leak (rare) | serve 内存渐增 | restart container (docker compose restart serve) |
| /report-progress 返回空 | tui 没写 progress.json | 确认 out/ 是 volume 而非 ephemeral |
| 中文乱码 | Windows console GBK | LANG=zh_CN.UTF-8; chcp 65001; PYTHONIOENCODING=utf-8 |

---

## 十一、Roadmap

- **P4 (已 in roadmap)**: 桌面 .exe / .msi / .dmg / .AppImage 自动 build (CI ready)
- **P5**: Prometheus exporter + Grafana dashboard
- **P5**: 内部 LLM gateway (cost tracking + 缓存)
- **P5**: 容器签名 + image attestation (cosign)
- **P6**: K8s helm chart
- **P6**: 多租户 (user namespace)

---

## 十二、Quick Reference

```bash
# 起服务
docker compose up -d serve

# 健康
curl localhost:9999/health
curl localhost:9999/diag?quick=1

# 日志
docker compose logs -f serve

# 跑研报
docker compose run --rm fa report SH600519

# 数据更新
docker compose run --rm fa data update --skip-5min --skip-basic

# 备份
rsync -av memories/ out/ user@nas:/backup/fa/

# 升级
git pull && docker compose build && docker compose up -d serve
```
