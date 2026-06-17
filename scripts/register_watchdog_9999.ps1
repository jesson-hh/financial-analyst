# register_watchdog_9999.ps1 -- set up autostart for watchdog_9999.ps1 and start it now.
# Idempotent: safe to re-run anytime.
#
# Mechanism (chosen after on-machine testing, 2026-06-10):
#   - HKCU Run key            -> starts the watchdog at user logon
#                                (conhost --headless = no console window flash)
#   - WMI Win32_Process.Create -> starts it immediately, parented to WmiPrvSE.exe so it
#                                does NOT die when the shell/session running this exits
#
# Task Scheduler is deliberately NOT used: on this machine every process spawned by the
# Schedule service (powershell.exe and even cmd.exe) freezes at early loader init
# (0 CPU, only 4 modules mapped, single thread in Executive wait). Verified 2026-06-10.
# The watchdog's named mutex makes double starts harmless (second instance exits).

$WatchdogPs1 = 'G:\guanlan-v2\scripts\watchdog_9999.ps1'
$LaunchCmd   = 'C:\Windows\System32\conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $WatchdogPs1

# remove the legacy (broken) scheduled task if an older version of this script created it
try {
    Unregister-ScheduledTask -TaskName 'guanlan-v2-9999-watchdog' -Confirm:$false -ErrorAction Stop
    Write-Output 'removed legacy scheduled task'
} catch { }

Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
    -Name 'guanlan-v2-9999-watchdog' -Value $LaunchCmd -Type String
Write-Output 'Run key set (watchdog starts at user logon)'

$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $LaunchCmd }
Write-Output ("watchdog start via WMI: rv={0} pid={1} (rv=0 ok; instance exits at once if one is already running)" -f $r.ReturnValue, $r.ProcessId)
