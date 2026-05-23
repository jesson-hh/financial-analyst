@echo off
REM ============================================================
REM  关闭 financial-analyst + GuanLan UI
REM  - 关 start.bat 启的两个窗口 (按窗口标题 taskkill)
REM ============================================================
echo.
echo === 关闭 financial-analyst + GuanLan UI ===
echo.

REM 关后端窗口
taskkill /FI "WINDOWTITLE eq fa-serve*" /T /F >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK]    后端 ^(fa-serve^) 已关
) else (
    echo [SKIP]  后端 ^(fa-serve^) 没运行
)

REM 关前端窗口
taskkill /FI "WINDOWTITLE eq fa-ui*" /T /F >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK]    前端 ^(fa-ui^) 已关
) else (
    echo [SKIP]  前端 ^(fa-ui^) 没运行
)

REM 兜底: 按端口 PID 杀 (taskkill 漏掉时用)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "0.0.0.0:9999"') do (
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "0.0.0.0:5173"') do (
    taskkill /PID %%a /F >nul 2>&1
)

echo.
echo ============================================================
echo  ^✓ 关闭完成.
echo ============================================================
echo.
