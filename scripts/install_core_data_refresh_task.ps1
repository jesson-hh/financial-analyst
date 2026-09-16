# Install the reviewed weekday after-close core-data producer.
#
# Important host constraint: the task action launches Python directly.  It does
# not put powershell.exe/cmd.exe in the scheduled child chain because this host
# has previously frozen those scheduler-derived loader processes.
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$TaskName = 'Guanlan Core Data Refresh',
    [string]$At = '18:30'
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = 'D:\app\miniconda\envs\stocks\python.exe'
$Runner = Join-Path $Repo 'scripts\run_scheduled_core_data_refresh.py'
$Arguments = '"' + $Runner + '" --execute'
$BackupDir = Join-Path $Repo 'var\core-data-refresh\task-backups'
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Scheduled Python runtime is missing"
}
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Scheduled refresh wrapper is missing"
}

$Plan = [ordered]@{
    schema_version = 'core-data-refresh-task/v1'
    task_name = $TaskName
    user = $CurrentUser
    trigger = "Monday-Friday $At local"
    executable = $Python
    arguments = $Arguments
    working_directory = $Repo
    direct_python = $true
    apply = [bool]$Apply
}

if (-not $Apply) {
    $Plan.status = 'ready'
    $Plan | ConvertTo-Json -Compress
    exit 0
}

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $Existing) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $Backup = Join-Path $BackupDir ($TaskName.Replace(' ', '-') + ".before.$Stamp.xml")
    Export-ScheduledTask -TaskName $TaskName | Set-Content -LiteralPath $Backup -Encoding Unicode
    $Plan.backup_name = Split-Path -Leaf $Backup
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument $Arguments `
    -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At $At
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'Guanlan reviewed after-close OHLCV, daily_basic, FinCast and PIT refresh.' `
    -Force | Out-Null

$Installed = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$Info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
$Plan.status = 'installed'
$Plan.state = [string]$Installed.State
$Plan.next_run_time = $Info.NextRunTime.ToString('o')
$Plan | ConvertTo-Json -Compress
