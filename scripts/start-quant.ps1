# start-quant.ps1
#
# 一键启动量化工作台 (財經分析觀瀾 quant.html):
#   1. fa serve 后端 (port 9999)
#   2. python -m http.server UI 静态 (port 5173)
#   3. 等 8s cold start (lazy imports + ChromaDB / BGE 加载)
#   4. 自动打开浏览器到 quant.html
#
# 两个后端进程跑在最小化窗口里 — 点窗口标题栏 × 关掉即停服务,
# 或在主窗口跑: Get-Process python | Where-Object {$_.MainWindowTitle -match 'fa serve|http.server'} | Stop-Process
#
# 用法:
#   .\scripts\start-quant.ps1                    # 默认端口 9999 + 5173
#   .\scripts\start-quant.ps1 -BackendPort 8888  # 自定义后端端口
#   .\scripts\start-quant.ps1 -NoOpen            # 不自动开浏览器 (CI / 调试)
#   .\scripts\start-quant.ps1 -KillExisting      # 先杀所有 python 进程 (清场)

[CmdletBinding()]
param(
    [int]$BackendPort = 9999,
    [int]$UiPort = 5173,
    [switch]$NoOpen,
    [switch]$KillExisting
)

$ErrorActionPreference = 'Stop'

# 项目根 (脚本所在目录的父目录)
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$UiDir = Join-Path $ProjectRoot 'src/financial_analyst/ui'

if (-not (Test-Path $UiDir)) {
    Write-Error "UI 目录不存在: $UiDir (项目根可能错位)"
    exit 1
}

if ($KillExisting) {
    Write-Host "[1/4] 清理旧 python 进程..." -ForegroundColor Yellow
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# 端口占用检查 (本地常见: 旧 fa serve 没退干净)
function Test-PortInUse([int]$Port) {
    $tcp = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $tcp)
}

if (Test-PortInUse $BackendPort) {
    Write-Warning "端口 $BackendPort 已占用 — 旧 fa serve 还在? 跑 -KillExisting 清场或换 -BackendPort"
    exit 1
}
if (Test-PortInUse $UiPort) {
    Write-Warning "端口 $UiPort 已占用 — 跑 -KillExisting 清场或换 -UiPort"
    exit 1
}

Write-Host "[2/4] 启动 fa serve (port $BackendPort)..." -ForegroundColor Cyan
$beProc = Start-Process -FilePath 'fa' -ArgumentList 'serve', '--port', $BackendPort `
    -WorkingDirectory $ProjectRoot -WindowStyle Minimized -PassThru
Write-Host "      PID $($beProc.Id)" -ForegroundColor DarkGray

Write-Host "[3/4] 启动 UI 静态服务 (port $UiPort)..." -ForegroundColor Cyan
$uiProc = Start-Process -FilePath 'python' -ArgumentList '-m', 'http.server', $UiPort `
    -WorkingDirectory $UiDir -WindowStyle Minimized -PassThru
Write-Host "      PID $($uiProc.Id)" -ForegroundColor DarkGray

Write-Host "[4/4] 等待 cold start (8s)..." -ForegroundColor Cyan
Start-Sleep -Seconds 8

# Probe backend ready
$backendOk = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:$BackendPort/openapi.json" -TimeoutSec 5 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $backendOk = $true }
} catch {}

if ($backendOk) {
    Write-Host ""
    Write-Host "  [OK] 后端就绪  http://localhost:$BackendPort" -ForegroundColor Green
    Write-Host "  [OK] UI 就绪   http://localhost:$UiPort/quant.html" -ForegroundColor Green
} else {
    Write-Warning "  后端 probe 失败 (可能还在 cold start) — 等几秒后浏览器刷新"
}

$Url = "http://localhost:$UiPort/quant.html?v=20260603c"

if (-not $NoOpen) {
    Write-Host ""
    Write-Host "打开浏览器: $Url" -ForegroundColor Magenta
    Start-Process $Url
}

Write-Host ""
Write-Host "提示:" -ForegroundColor DarkGray
Write-Host "  - 两个后台窗口最小化跑着, 关窗 = 停服务" -ForegroundColor DarkGray
Write-Host "  - 一键停所有: Get-Process python,fa -ErrorAction SilentlyContinue | Stop-Process -Force" -ForegroundColor DarkGray
Write-Host "  - 调用 stocks streamlit (另一个 UI, 4 page): cd G:/stocks; streamlit run app/app.py" -ForegroundColor DarkGray
