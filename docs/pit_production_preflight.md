# 严格 PIT 生产部署预检

严格历史研究依赖两个独立的信任条件：外置 HMAC 密钥用于证明证据未被篡改，可信时钟用于证明采集和签名时间没有倒拨。两者缺一时，不能把历史结果标成已通过 PIT。

## 一条命令检查

在仓库根目录执行：

```powershell
python scripts/check_pit_preflight.py
```

该脚本只读取当前进程继承的环境变量，刻意不自行打开任何 secret 文件。若 9999 服务的 HMAC 凭据仅存放在 `var/secrets.env`，应在服务启动后查看 `/data/health`；服务进程只会从该文件加载精确白名单内的凭据，再运行同一预检。NTP 声明不属于凭据，必须来自进程/部署环境。

也可在已安装仓内引擎的环境执行：

```powershell
python -m financial_analyst.cli pit-preflight
```

返回码 `0` 表示 `ready`，返回码 `2` 表示 `blocked`。输出只包含公开的 active key id、轮换密钥数量、时间状态和稳定 blocker code；不会显示密钥、编码后的密钥、实际密钥长度或无效 key id。

服务运行后，同一结果会出现在 `GET /data/health` 的 `items.pit_trust`，帷幄的 `ww_data_health` 也会显示它。该项属于部署运维状态，不会把当前行情等数据面的 overall 状态误判为故障。

## 需要配置的项目

| 环境变量 | 要求 | 是否秘密 |
|---|---|---|
| `GL_PIT_HMAC_KEY_ID` | 符合 `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}` 的 active key id | 否 |
| `GL_PIT_HMAC_KEY` | 原始 UTF-8、`hex:` 或 `base64:`；解码后至少 32 字节 | 是 |
| `GL_PIT_HMAC_VERIFY_KEYS` | key id 到旧密钥的 JSON 对象；暂时没有旧钥也显式填 `{}` | 是 |
| `GL_PIT_NTP_STATUS` | 完成 OS 校时检查后精确填 `synchronized` | 否 |
| `GL_PIT_NTP_SOURCE` | 校时来源，例如 `w32time` 或 `chrony` | 否 |
| `GL_PIT_NTP_CHECKED_AT` | 带时区的 ISO 时间，例如 `2026-09-03T12:00:00+08:00` | 否 |
| `GL_PIT_NTP_MAX_AGE_SECONDS` | 可选，60–604800；默认 86400 秒 | 否 |

生产 9999 服务由 `guanlan_v2/server.py` 读取进程环境，并兼容从 gitignored 的 `var/secrets.env` 读取三个 PIT HMAC 凭据。该文件不是通用 dotenv：未知项、feature/runtime/write gate 和 NTP 声明都会 fail closed，不进入进程环境；可用 `GET /runtime/secrets-env-status` 查看只含 key 名与计数的加载状态。不要把真实密钥写进 `.env.example`、PowerShell 守护脚本、日志或数据库。密钥轮换时，新 active key 放在前两个变量中，仍需验证的旧 key 放入 verify keyring。

首次部署且确认不存在既有 PIT key 时，可先预览、再显式创建一个本机 CSPRNG
信任锚：

```powershell
python scripts/provision_pit_hmac_key.py --key-id pit-prod-YYYY-MM
python scripts/provision_pit_hmac_key.py --key-id pit-prod-YYYY-MM --apply
```

该命令只允许从“完全未配置”到“新 active key + 显式 `{}` keyring”，不会覆盖或
轮换任何已有 key；候选文件先经生产白名单解析，备份与候选文件 ACL 收紧为当前用户、
SYSTEM 和 Administrators 后才原子替换。输出和审计 JSON 不含密钥。新 key 只证明此后
生成的收据；脚本明确不会回签旧资产。

## 可信时钟操作

预检不会访问公网时间服务器，也不会把“机器时间看起来正确”冒充 NTP 已同步。运维应先通过操作系统确认：

```powershell
w32tm /query /status
```

确认 Windows 时间服务已同步后，再更新 `GL_PIT_NTP_STATUS`、`GL_PIT_NTP_SOURCE` 和 `GL_PIT_NTP_CHECKED_AT`。Linux 可使用受管主机规定的 `chronyc tracking` 或 `timedatectl timesync-status` 流程。声明超过最大龄期、来自未来、缺少时区或字段不完整时，预检都会稳定返回 `blocked`。

NTP 声明必须由进程/服务管理器的部署环境更新，并在重启 9999 服务后进入该进程。
本机 9999 的 `check_9999.ps1` 每一代都会执行
`scripts/check_windows_time_attestation.py`：只有现场 `w32tm /query /status`
返回已同步、合法 stratum、外部时间源且最近一次同步不超过 24 小时，才会把新的
四个 `GL_PIT_NTP_*` 值写入该代进程环境；探测失败会先清除继承的旧声明，严格 PIT
保持黄色，但不会关闭当前/实时研究。可独立验证这条部署链：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/check_9999.ps1 -ValidatePitTimeOnly
```

watchdog 注入的是进程启动时的短期部署证明，不是永久租约；后续 checker 代际无法
反向修改已经运行的 9999 进程环境。为使长寿 server 不在 24 小时后仅因启动时间而
误过期，server 的 lifespan 会在启动后立即、此后每 5 分钟重跑同一个现场 Windows
Time 探针。只有新的现场探测通过才会续写本进程的四个非秘密声明；任何失败都会先
撤销 `GL_PIT_NTP_STATUS` 再清空其余三项，预检只可能短暂 fail closed，不能把半套
更新看成可信。探针通过绝对 `System32\w32tm.exe` 执行、15 秒超时，子进程仅继承
Windows 启动所需的最小消毒环境，不会继承 HMAC/API 凭据。该续期不写证明文件、
不信任 watchdog 心跳，也不靠周期重启 server，因此不会打断正在进行的研究。新代码
会在下一次受控重启后生效；不要为了续期强杀活跃的 9999 进程。

不要把一次 `synchronized` 声明永久写进凭据文件或系统环境。

应用自身的 PIT 截止时间固定使用 `Asia/Hong_Kong` aware datetime；预检会独立验证这一点。

## 状态边界

- `ready=true`：配置结构、active key 强度、完整 keyring、香港时区 aware clock 和近期 NTP 声明均通过。
- `ready=false`：部署门禁失败；`blockers` 给出可自动处理的原因码，且不含 secret。
- 该预检是只读的，不生成密钥、不修改环境、不写文件、不迁移数据。
- 当前模式继续可用；`pit_trust` 是部署就绪状态，并不是所有历史端点的统一执行门。严格历史证据路径现有的签名校验仍会在缺少可信 active key 时独立 fail closed；NTP 预检失败表示“不得把严格 PIT 标绿”，不要误报成已自动拦住所有历史执行路径。生产发布流程应把返回码 `2` 当作发布阻断。
