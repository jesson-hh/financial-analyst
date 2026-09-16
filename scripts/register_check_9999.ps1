# register_check_9999.ps1 -- switch guardianship to the generational checker.
# Idempotent. Removes the old resident watchdog (Run key + running instances),
# sets Run key for check_9999.ps1, and starts the first generation via WMI now.
# NEVER Task Scheduler (Schedule-service children freeze on this box, 2026-06-10).
param([string]$CheckScript = $env:GUANLAN_CHECK_9999_SCRIPT)
$ErrorActionPreference = 'Stop'
$CheckPs1 = if ([string]::IsNullOrWhiteSpace($CheckScript)) {
    Join-Path $PSScriptRoot 'check_9999.ps1'
} else {
    (Resolve-Path -LiteralPath $CheckScript).Path
}
if (-not (Test-Path -LiteralPath $CheckPs1 -PathType Leaf)) {
    throw "check_9999.ps1 not found: $CheckPs1"
}
$Conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$LaunchCmd = '"{0}" --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{1}"' -f $Conhost, $CheckPs1

# 1) retire old resident watchdog: Run key + live instances (frozen ones hold the old mutex)
try { Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'guanlan-v2-9999-watchdog' -ErrorAction Stop; Write-Output 'old watchdog Run key removed' } catch { }
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*watchdog_9999*' } |
    ForEach-Object { Write-Output ("killing old watchdog pid={0}" -f $_.ProcessId)
                     Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2) Run key for the checker (logon bootstrap)
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
    -Name 'guanlan-v2-9999-check' -Value $LaunchCmd -Type String
Write-Output 'Run key set (checker starts at user logon)'

# 3) start first generation now via WMI (survives this shell; mutex makes doubles harmless)
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $LaunchCmd }
Write-Output ("checker start via WMI: rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
