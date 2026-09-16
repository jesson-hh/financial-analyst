[CmdletBinding()]
param([switch]$Apply)

# Default is a read-only preflight. This script never changes runtime gates,
# environment variables, models, workflows, or configuration. The existing
# watchdog, not a new helper/service, owns startup after the lock is released.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$TaskSource = 'C:\Users\AI-PC2601018\.codex\worktrees\64c6\guanlan-v2'
$TaskTarget = 'G:\guanlan-v2'
$TaskRelative = 'guanlan_v2\screen\data_screen.py'
$TaskOldHash = '78515796d93b3fb8978203d60698c6e8fcf4e2992996ccf098d84822964f789a'
$TaskNewHash = '080d5a20066d51377235b19a1132e4c246f613f158c3becf46d8da9a898704f8'
$TaskLock = 'G:\guanlan-v2\var\deploy-9999.lock'
$TaskApi = 'http://127.0.0.1:9999'
$TaskServerScript = 'G:\guanlan-v2\guanlan_v2\server.py'
$TaskWatchdogScript = 'G:\guanlan-v2\scripts\check_9999.ps1'
$TaskActiveStates = @('queued', 'pending', 'starting', 'preparing', 'running', 'cancelling', 'stopping', 'steering', 'finalizing')

function Task-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Task-Value($Object, [string]$Name, $Default = $null) {
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}
function Task-Path([string]$Root, [string]$Relative) {
    $full = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $full.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes reviewed root: $full" }
    $cursor = $full
    while ($cursor -and $cursor.Length -gt 3) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point forbidden: $cursor" }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}
function Task-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Task-WriteReceipt([string]$Path, $Record) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Record | ConvertTo-Json -Depth 12) + "`n")
    $handle = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $handle.Write($bytes, 0, $bytes.Length); $handle.Flush($true) }
    finally { $handle.Dispose() }
}
function Task-AssertIdle {
    foreach ($endpoint in @('/model/promote/status', '/model/publish_dl/status', '/research/loop/status')) {
        $response = Invoke-RestMethod -Uri ($TaskApi + $endpoint) -TimeoutSec 15
        $state = Task-Value $response 'state'
        if ((Task-Value $response 'ok') -ne $true -or $null -eq $state -or $null -eq (Task-Value $state 'running')) { throw "Status contract unavailable: $endpoint" }
        if ((Task-Value $state 'running') -ne $false) { throw "Active job; deployment will not interrupt it: $endpoint" }
    }
    $sessions = Invoke-RestMethod -Uri ($TaskApi + '/console/sessions') -TimeoutSec 15
    if ($sessions.ok -ne $true) { throw 'Console state unavailable.' }
    foreach ($session in $sessions.sessions) {
        if ((Task-Value $session 'running') -ne $false) { throw "Active/unknown console turn: $($session.id)" }
        $background = Task-Value $session 'bg'
        if ($null -ne $background) {
            foreach ($property in $background.PSObject.Properties) {
                if ((Task-Value $property.Value 'status' '') -in $TaskActiveStates) { throw "Active console background job: $($session.id)/$($property.Name)" }
            }
        }
    }
    $runRoot = Task-Path $TaskTarget '.data\workflows\_runs'
    if (Test-Path -LiteralPath $runRoot) {
        foreach ($file in Get-ChildItem -LiteralPath $runRoot -File -Filter '*.json') {
            $run = Task-Json $file.FullName
            $status = Task-Value $run 'status' ''
            if (-not $status -or $status -in $TaskActiveStates) { throw "Active/unknown durable workflow: $($file.Name)" }
        }
    }
}
function Task-ServerProcess {
    $connections = @(Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue)
    $owners = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw 'Expected exactly one live port-9999 server.' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($owners[0])"
    if ($null -eq $process -or $process.Name -ne 'python.exe' -or -not $process.CommandLine.Contains('"' + $TaskServerScript + '"')) {
        throw 'Listener is not the reviewed G: server process; refuse to stop it.'
    }
    return $process
}

$from = Task-Path $TaskSource $TaskRelative
$target = Task-Path $TaskTarget $TaskRelative
$null = Task-Path $TaskTarget 'var\deploy-9999.lock'
if ((Task-Hash $from) -ne $TaskNewHash -or (Task-Hash $target) -ne $TaskOldHash) { throw 'Source/live hash drift; review the two-file diff before deployment.' }
if (Test-Path -LiteralPath $TaskLock) { throw 'Another deployment lock exists; do not remove or reuse it.' }

# The worktree has ww_data_screen, but the reviewed live tools.py does NOT.
# Never copy the divergent tools.py or synthesize a missing tool registration.
$liveTools = Task-Path $TaskTarget 'guanlan_v2\console\tools.py'
$toolsText = [IO.File]::ReadAllText($liveTools, [Text.Encoding]::UTF8)
if ($toolsText.Contains('ww_data_screen') -or $toolsText.Contains('rolling_mean')) { throw 'Live console tool contract changed; review its exact delta before including it.' }

Task-AssertIdle
$server = Task-ServerProcess
$watchdogs = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($TaskWatchdogScript) })
if (-not $watchdogs.Count) { throw 'Existing watchdog not found; root must arrange the reviewed restart before deployment.' }
if (-not $Apply) {
    [pscustomobject]@{dry_run=$true; target=$target; before=$TaskOldHash; after=$TaskNewHash; server_pid=$server.ProcessId; tools_action='unchanged: live has no ww_data_screen'; active_jobs=$false} | ConvertTo-Json
    return
}

$backupRelative = 'var\deploy-trend-pullback-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$backup = Task-Path $TaskTarget $backupRelative
if (Test-Path -LiteralPath $backup) { throw 'Backup already exists.' }
$original = Task-Path $backup ('originals\' + $TaskRelative)
$candidate = Task-Path $backup ('candidate\' + $TaskRelative)
$null = New-Item -ItemType Directory -Path (Split-Path -Parent $original)
$null = New-Item -ItemType Directory -Path (Split-Path -Parent $candidate)
[IO.File]::Copy($target, $original, $false)
[IO.File]::Copy($from, $candidate, $false)
if ((Task-Hash $original) -ne $TaskOldHash -or (Task-Hash $candidate) -ne $TaskNewHash) { throw 'Backup/candidate verification failed; server is still running.' }
Task-WriteReceipt (Join-Path $backup 'manifest.json') ([ordered]@{
    schema='trend-pullback-deploy-v1'; created_at=(Get-Date).ToString('o')
    server_pid=$server.ProcessId; server_creation=[string]$server.CreationDate
    files=@(@{path=$TaskRelative; before=$TaskOldHash; after=$TaskNewHash})
    live_tools_sha256=(Task-Hash $liveTools); live_tools_action='unchanged: registration absent'
    restart='existing watchdog only'; runtime_gates_changed=$false
})

$ownerToken = 'trend-pullback:' + [guid]::NewGuid().ToString('N')
$lockStream = $null
$stopped = $false
$copied = $false
try {
    $lockStream = [IO.File]::Open($TaskLock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $lockBytes = [Text.Encoding]::ASCII.GetBytes($ownerToken)
    $lockStream.Write($lockBytes, 0, $lockBytes.Length)
    $lockStream.Flush($true)
    # Recheck immediately before stopping. Never interrupt even this task's own
    # training; the deployment lock only prevents watchdog restarts, not jobs.
    Task-AssertIdle
    $fresh = Task-ServerProcess
    if ($fresh.ProcessId -ne $server.ProcessId -or $fresh.CreationDate -ne $server.CreationDate) { throw 'Server generation changed; abort.' }
    if ((Task-Hash $target) -ne $TaskOldHash -or (Task-Hash $original) -ne $TaskOldHash) { throw 'Live/backup bytes changed; abort.' }
    Stop-Process -Id $fresh.ProcessId -ErrorAction Stop
    $stopped = $true
    for ($n=0; $n -lt 50; $n++) {
        if (-not @(Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue).Count) { break }
        Start-Sleep -Milliseconds 100
    }
    if (@(Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue).Count) { throw 'Port 9999 did not close; do not copy files.' }
    [IO.File]::Copy($candidate, $target, $true)
    $copied = $true
    if ((Task-Hash $target) -ne $TaskNewHash) { throw 'Deployment hash verification failed.' }
} catch {
    if ($stopped -and (Task-Hash $original) -eq $TaskOldHash) {
        # Only restore our single target; no broad checkout/reset or data edits.
        [IO.File]::Copy($original, $target, $true)
        if ((Task-Hash $target) -ne $TaskOldHash) { throw "Original restore failed; backup remains at $original" }
    }
    throw
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        if ((Test-Path -LiteralPath $TaskLock) -and [IO.File]::ReadAllText($TaskLock) -ceq $ownerToken) {
            Remove-Item -LiteralPath $TaskLock -ErrorAction Stop
        }
    }
}
[pscustomobject]@{applied=$copied; backup=$backup; target=$target; sha256=(Task-Hash $target); restart='lock released; existing watchdog will restart the server'; tools_action='unchanged'; runtime_gates_changed=$false} | ConvertTo-Json
