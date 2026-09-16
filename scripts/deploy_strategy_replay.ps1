[CmdletBinding()]
param([switch]$Apply)

# Read-only by default. The only writable live targets are the eight reviewed
# source/UI files in staging_manifest.json. No models, data, graphs, settings,
# permissions, or runtime gates are modified. Existing watchdog owns restart.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$TaskWorkspace = 'C:\Users\AI-PC2601018\.codex\worktrees\64c6\guanlan-v2'
$TaskStage = Join-Path $TaskWorkspace 'var\trend_strategy_comparison_20260912\staging'
$TaskTarget = 'G:\guanlan-v2'
$TaskManifestPath = Join-Path $TaskWorkspace 'var\trend_strategy_comparison_20260912\staging_manifest.json'
$TaskLock = 'G:\guanlan-v2\var\deploy-9999.lock'
$TaskApi = 'http://127.0.0.1:9999'
$TaskServerScript = 'G:\guanlan-v2\guanlan_v2\server.py'
$TaskWatchdogScript = 'G:\guanlan-v2\scripts\check_9999.ps1'
$TaskAllowed = @(
    'guanlan_v2/workflow/api.py', 'guanlan_v2/workflow/executor.py',
    'guanlan_v2/workflow/authoring.py', 'guanlan_v2/screen/historical_screen.py',
    'guanlan_v2/workflow/daily_simulator.py', 'guanlan_v2/workflow/strategy_replay.py',
    'ui/factor/workflow.jsx', 'ui/factor/观澜 · AI 工作流.html'
)
$TaskActiveStates = @('queued','pending','starting','preparing','running','cancelling','stopping','steering','finalizing')

function Task-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Task-Value($Object, [string]$Name, $Default = $null) {
    if ($null -ne $Object -and $Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $Default
}
function Task-Path([string]$Root, [string]$Relative) {
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $full = [IO.Path]::GetFullPath((Join-Path $rootFull $Relative))
    if (-not $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path escapes reviewed root: $full" }
    $cursor = $full
    while ($cursor -and $cursor.Length -gt 3) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse point forbidden: $cursor" }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    return $full
}
function Task-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Task-WriteReceipt([string]$Path, $Record) {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Record | ConvertTo-Json -Depth 16) + "`n")
    $handle = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $handle.Write($bytes, 0, $bytes.Length); $handle.Flush($true) }
    finally { $handle.Dispose() }
}
function Task-AssertIdle {
    foreach ($endpoint in @('/model/promote/status','/model/publish_dl/status','/research/loop/status')) {
        $response = Invoke-RestMethod -Uri ($TaskApi + $endpoint) -TimeoutSec 15
        $state = Task-Value $response 'state'
        if ((Task-Value $response 'ok') -ne $true -or $null -eq $state -or $null -eq (Task-Value $state 'running')) { throw "Unavailable status: $endpoint" }
        if ((Task-Value $state 'running') -ne $false) { throw "Active job; not interrupted: $endpoint" }
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
    if ($null -eq $process -or $process.Name -ne 'python.exe' -or -not $process.CommandLine.Contains('"' + $TaskServerScript + '"')) { throw 'Listener is not the reviewed G: server process.' }
    return $process
}
function Task-AssertFiles($Files) {
    foreach ($item in $Files) {
        $from = Task-Path $TaskStage $item.path
        $target = Task-Path $TaskTarget $item.path
        if ((Task-Hash $from) -ne $item.after) { throw "Stage hash drift: $($item.path)" }
        if ($null -eq $item.before) {
            if (Test-Path -LiteralPath $target) { throw "New live target already exists: $($item.path)" }
        } elseif (-not (Test-Path -LiteralPath $target) -or (Task-Hash $target) -ne $item.before) {
            throw "Live hash drift: $($item.path)"
        }
    }
}

$manifest = Task-Json $TaskManifestPath
if ($manifest.schema -ne 'strategy-replay-stage-v1' -or $manifest.ready -ne $true) { throw 'Staging manifest is not approved-ready.' }
$files = @($manifest.files)
if ($files.Count -ne $TaskAllowed.Count) { throw 'Manifest must contain exactly the eight reviewed backend/UI files.' }
$actualPaths = @($files | ForEach-Object { [string]$_.path })
if (@($actualPaths | Select-Object -Unique).Count -ne $actualPaths.Count) { throw 'Duplicate target path.' }
foreach ($path in $actualPaths) { if ($path -cnotin $TaskAllowed) { throw "Unreviewed target: $path" } }
foreach ($item in $files) {
    if ($item.after -cnotmatch '^[0-9a-f]{64}$' -or ($null -ne $item.before -and $item.before -cnotmatch '^[0-9a-f]{64}$')) { throw 'Invalid frozen file hash.' }
}
$null = Task-Path $TaskTarget 'var\deploy-9999.lock'
if (Test-Path -LiteralPath $TaskLock) { throw 'Another deployment lock exists; do not remove/reuse it.' }
Task-AssertFiles $files
Task-AssertIdle
$server = Task-ServerProcess
$watchdogs = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($TaskWatchdogScript) })
if (-not $watchdogs.Count) { throw 'Existing watchdog not found.' }
if (-not $Apply) {
    [pscustomobject]@{dry_run=$true; files=$files; server_pid=$server.ProcessId; active_jobs=$false; runtime_gates_changed=$false; direct_history_calls='New endpoints absent before initial deployment; caller must not dispatch concurrent work.'} | ConvertTo-Json -Depth 12
    return
}

$backup = Task-Path $TaskTarget ('var\deploy-strategy-replay-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if (Test-Path -LiteralPath $backup) { throw 'Backup already exists.' }
foreach ($item in $files) {
    $candidate = Task-Path $backup ('candidate\' + $item.path)
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $candidate) -Force
    [IO.File]::Copy((Task-Path $TaskStage $item.path), $candidate, $false)
    if ((Task-Hash $candidate) -ne $item.after) { throw 'Candidate verification failed; server remains running.' }
    if ($null -ne $item.before) {
        $original = Task-Path $backup ('originals\' + $item.path)
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $original) -Force
        [IO.File]::Copy((Task-Path $TaskTarget $item.path), $original, $false)
        if ((Task-Hash $original) -ne $item.before) { throw 'Backup verification failed; server remains running.' }
    }
}
Task-WriteReceipt (Join-Path $backup 'manifest.json') ([ordered]@{
    schema='strategy-replay-deploy-v1'; created_at=(Get-Date).ToString('o'); files=$files
    staging_manifest_sha256=(Task-Hash $TaskManifestPath); server_pid=$server.ProcessId
    restart='existing watchdog only'; runtime_gates_changed=$false; original_files_preserved=$true
})

$ownerToken = 'strategy-replay:' + [guid]::NewGuid().ToString('N')
$lockStream = $null
$changed = [Collections.Generic.List[object]]::new()
$stopped = $false
try {
    $lockStream = [IO.File]::Open($TaskLock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $lockBytes = [Text.Encoding]::ASCII.GetBytes($ownerToken)
    $lockStream.Write($lockBytes, 0, $lockBytes.Length)
    $lockStream.Flush($true)
    Task-AssertIdle
    Task-AssertFiles $files
    $fresh = Task-ServerProcess
    if ($fresh.ProcessId -ne $server.ProcessId -or $fresh.CreationDate -ne $server.CreationDate) { throw 'Server generation changed; abort.' }
    Stop-Process -Id $fresh.ProcessId -ErrorAction Stop
    $stopped = $true
    for ($n=0; $n -lt 50; $n++) {
        if (-not @(Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue).Count) { break }
        Start-Sleep -Milliseconds 100
    }
    if (@(Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue).Count) { throw 'Port 9999 did not close.' }
    foreach ($item in $files) {
        $target = Task-Path $TaskTarget $item.path
        $candidate = Task-Path $backup ('candidate\' + $item.path)
        if ((Task-Hash $candidate) -ne $item.after) { throw 'Reviewed candidate changed.' }
        $changed.Add($item)
        [IO.File]::Copy($candidate, $target, ($null -ne $item.before))
        if ((Task-Hash $target) -ne $item.after) { throw "Deployed hash mismatch: $($item.path)" }
    }
} catch {
    if ($stopped) {
        foreach ($item in $changed) {
            $target = Task-Path $TaskTarget $item.path
            if ($null -ne $item.before) {
                $original = Task-Path $backup ('originals\' + $item.path)
                if ((Task-Hash $original) -ne $item.before) { throw "Rollback backup corrupted: $original" }
                [IO.File]::Copy($original, $target, $true)
                if ((Task-Hash $target) -ne $item.before) { throw "Rollback verification failed: $target" }
            } elseif (Test-Path -LiteralPath $target) {
                # Exact newly-created source file only; its recoverable copy is
                # retained under candidate. No recursive removal or broad glob.
                if ((Task-Hash $target) -ne $item.after) { throw "Unknown new-file bytes; manual review required: $target" }
                Remove-Item -LiteralPath $target -ErrorAction Stop
            }
        }
    }
    throw
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        if ((Test-Path -LiteralPath $TaskLock) -and [IO.File]::ReadAllText($TaskLock) -ceq $ownerToken) { Remove-Item -LiteralPath $TaskLock -ErrorAction Stop }
    }
}
[pscustomobject]@{applied=$true; files=$files; backup=$backup; restart='lock released; existing watchdog owns restart'; runtime_gates_changed=$false} | ConvertTo-Json -Depth 12
