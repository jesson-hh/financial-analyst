@echo off
REM ============================================================
REM  financial-analyst + GuanLan UI 一键启动 (Windows)
REM  - 检查 .env 存在
REM  - 检查 venv 可用
REM  - 起 buddy SSE 后端 (port 9999)
REM  - 起 GuanLan 前端 (port 5173)
REM  - 打开浏览器
REM  关闭: stop.bat
REM ============================================================
setlocal enabledelayedexpansion

REM 切到脚本所在目录, 确保后续相对路径正确
cd /d "%~dp0"

echo.
echo === financial-analyst + GuanLan UI ===
echo.

REM ── 1. 环境检查 ──────────────────────────────────────────────
if not exist ".env" (
    echo [ERROR] .env 不存在. 请先 copy .env.example .env 并填入 API key.
    echo         详见 docs\setup\zero_to_report.md
    pause
    exit /b 1
)

if not exist ".venv\Scripts\financial-analyst.exe" (
    echo [ERROR] .venv 不存在或未装 financial-analyst.
    echo         请先 python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -e .[serve]
    pause
    exit /b 1
)

if not exist "G:\stocks\fa_ui_ready\index.html" (
    echo [ERROR] G:\stocks\fa_ui_ready 不存在.
    echo         前端目录被移动或删除了, 检查路径.
    pause
    exit /b 1
)

echo [OK]    .env, venv, fa_ui_ready 都在.

REM ── 2. 端口占用检查 ─────────────────────────────────────────
netstat -an | findstr "0.0.0.0:9999" >nul
if %errorlevel% equ 0 (
    echo [WARN]  端口 9999 已被占用. 先跑 stop.bat?
    pause
    exit /b 1
)

netstat -an | findstr "0.0.0.0:5173" >nul
if %errorlevel% equ 0 (
    echo [WARN]  端口 5173 已被占用. 先跑 stop.bat?
    pause
    exit /b 1
)

REM ── 3. 启后端 (SSE bridge) ─────────────────────────────────
echo [BOOT]  启动 buddy SSE 后端 (port 9999)...
start "fa-serve" /MIN cmd /k ""%~dp0.venv\Scripts\financial-analyst.exe" serve --port 9999"

REM 给后端 3 秒初始化
ping -n 4 127.0.0.1 >nul

REM 验证后端起来了
powershell -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:9999/health' -TimeoutSec 5; if ($r.ok) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% neq 0 (
    echo [ERROR] 后端启动失败. 看 fa-serve 窗口的报错.
    pause
    exit /b 1
)
echo [OK]    后端就绪 ^(http://127.0.0.1:9999/health^)

REM ── 4. 启前端 (静态文件服务) ─────────────────────────────────
echo [BOOT]  启动 GuanLan UI 前端 (port 5173)...
start "fa-ui" /MIN cmd /k "cd /d G:\stocks\fa_ui_ready ^&^& python -m http.server 5173"

REM 给前端 2 秒初始化
ping -n 3 127.0.0.1 >nul
echo [OK]    前端就绪.

REM ── 5. 打开浏览器 ───────────────────────────────────────────
echo [OPEN]  http://localhost:5173
start "" "http://localhost:5173"

echo.
echo ============================================================
echo  ^✓ 启动完成. 两个窗口 (fa-serve / fa-ui) 已最小化到任务栏.
echo  ^✓ 浏览器已打开 http://localhost:5173
echo
echo  关闭: 跑 stop.bat
echo ============================================================
echo.
endlocal
