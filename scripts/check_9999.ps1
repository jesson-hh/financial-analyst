# check_9999.ps1 -- guanlan-v2 backend (:9999) generational health checker.
#
# One GENERATION = up to $GenMinutes of $CycleSec-interval checks, then:
#   release mutex -> spawn successor via WMI Win32_Process.Create -> exit.
# No process lives longer than $GenMinutes => no long-resident freeze surface.
# NEVER Task Scheduler for this PowerShell/cmd-based 9999 watchdog: those
# Schedule-service shell children froze at loader init on this box (verified
# 2026-06-10, see register_watchdog_9999.ps1). WMI-spawned processes (parented
# to WmiPrvSE) are the verified-alive mechanism for server guardianship.
# Bootstrap: HKCU Run key (logon, see register_check_9999.ps1) + server-side heartbeat
# revive (guanlan_v2/server.py lifespan watches var\check_9999.heartbeat).
# State across generations: var\check_9999.state {"fails":n}.
# NOTE: ASCII-only comments -- PS 5.1 misparses BOM-less UTF-8 non-ASCII bytes.
param(
    [switch]$Once,   # -Once: single check pass, no successor (for tests)
    [switch]$ValidateRuntimeProfileOnly,
    [switch]$ValidatePitTimeOnly,
    [string]$RepoRoot = $env:GUANLAN_REPO_ROOT,
    [string]$PythonPath = $env:GUANLAN_PYTHON
)

$ErrorActionPreference = 'Stop'

# Reviewed, non-secret deployment profile.  This is deliberately a closed
# schema rather than a dotenv import: watchdog generations must inherit the
# same feature posture without ever treating var/secrets.env as runtime config.
$RuntimeGateProfile = [ordered]@{
    CONSOLE_REVIEW_MODE       = 'enforce'
    GUANLAN_DL_DAILY          = '1'
    GUANLAN_EVENTS_INGEST     = '1'
    GUANLAN_MCP_WRITE         = '1'
    GUANLAN_ORCH_LAUNCHER     = '1'
    GUANLAN_REGEN_DAILY       = '1'
    GUANLAN_RERANK_DAILY      = '1'
    GUANLAN_REVIEW_DAILY      = '1'
    GUANLAN_SEATS_DEEP        = '1'
    GUANLAN_SEATS_WATCH       = '1'
    GUANLAN_SNAPSHOT_ARCHIVE  = '1'
}
$BooleanRuntimeGates = @(
    'GUANLAN_DL_DAILY',
    'GUANLAN_EVENTS_INGEST',
    'GUANLAN_MCP_WRITE',
    'GUANLAN_ORCH_LAUNCHER',
    'GUANLAN_REGEN_DAILY',
    'GUANLAN_RERANK_DAILY',
    'GUANLAN_REVIEW_DAILY',
    'GUANLAN_SEATS_DEEP',
    'GUANLAN_SEATS_WATCH',
    'GUANLAN_SNAPSHOT_ARCHIVE'
)
$ExpectedRuntimeGates = @('CONSOLE_REVIEW_MODE') + $BooleanRuntimeGates

function Set-ReviewedRuntimeGateProfile {
    $actual = @($RuntimeGateProfile.Keys | Sort-Object)
    $expected = @($ExpectedRuntimeGates | Sort-Object)
    if (($actual -join "`n") -cne ($expected -join "`n")) {
        throw '[runtime-config-blocker] runtime gate schema is not the reviewed closed set'
    }
    if (@('off', 'monitor', 'enforce') -cnotcontains [string]$RuntimeGateProfile.CONSOLE_REVIEW_MODE) {
        throw '[runtime-config-blocker] CONSOLE_REVIEW_MODE is outside off/monitor/enforce'
    }
    foreach ($name in $BooleanRuntimeGates) {
        if (@('0', '1') -cnotcontains [string]$RuntimeGateProfile[$name]) {
            throw ("[runtime-config-blocker] {0} must be exactly 0 or 1" -f $name)
        }
    }
    if ($RuntimeGateProfile.GUANLAN_MCP_WRITE -cne '1') {
        throw '[runtime-config-blocker] GUANLAN_MCP_WRITE must match the user-approved 2026-09-05 profile'
    }
    if (($RuntimeGateProfile.GUANLAN_DL_DAILY -ceq '1' -or
         $RuntimeGateProfile.GUANLAN_RERANK_DAILY -ceq '1') -and
        $RuntimeGateProfile.GUANLAN_REGEN_DAILY -cne '1') {
        throw '[runtime-config-blocker] DL/RERANK daily require GUANLAN_REGEN_DAILY=1'
    }
    if ($RuntimeGateProfile.GUANLAN_REVIEW_DAILY -ceq '1' -and
        $RuntimeGateProfile.GUANLAN_RERANK_DAILY -cne '1') {
        throw '[runtime-config-blocker] review daily requires GUANLAN_RERANK_DAILY=1'
    }
    if ($RuntimeGateProfile.GUANLAN_SEATS_DEEP -ceq '1' -and
        $RuntimeGateProfile.GUANLAN_SEATS_WATCH -cne '1') {
        throw '[runtime-config-blocker] deep seats require GUANLAN_SEATS_WATCH=1'
    }

    # Apply only after the entire profile passes, so an invalid profile cannot
    # leave a partially widened process environment.
    foreach ($name in $ExpectedRuntimeGates) {
        [Environment]::SetEnvironmentVariable(
            $name, [string]$RuntimeGateProfile[$name], 'Process')
    }
}

try {
    Set-ReviewedRuntimeGateProfile
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 78
}

if ($ValidateRuntimeProfileOnly) {
    [pscustomobject]@{
        schema_version = 'runtime-gate-profile@1'
        states = $RuntimeGateProfile
    } | ConvertTo-Json -Depth 3 -Compress
    exit 0
}

$ErrorActionPreference = 'Continue'
# Persist the Telegram feature gate across every WMI-spawned checker generation.
$env:GUANLAN_TELEGRAM_ENABLED = '1'
# Route Weiwo conversations through the locally signed-in Codex App Server.
# The backend discovers the current Codex Desktop binary and never stores auth tokens.
$env:GUANLAN_CONSOLE_BACKEND = 'codex'
# Codex confirmed workspace writes use the short-lived /gl-mcp-codex scope.
# User approved ordinary MCP writes on 2026-09-05 for repair and candidate
# training. Workspace mode, review and per-action authorization still apply.
# Strict-PIT secrets are deliberately not embedded in this restart chain.
# server.py reads them from the inherited environment or gitignored
# var/secrets.env; readiness is visible at /data/health -> items.pit_trust.
# Run scripts/check_pit_preflight.py in the same deployment environment before
# marking historical PIT ready.  A blocked PIT preflight must not take the
# current/live research workspace offline.
$Repo = if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
} else {
    (Resolve-Path -LiteralPath $RepoRoot -ErrorAction Stop).Path
}
$PythonCandidates = @(
    $PythonPath,
    $(if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' }),
    (Join-Path $Repo '.venv\Scripts\python.exe'),
    $(try { (Get-Command python.exe -ErrorAction Stop).Source } catch { $null })
) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
$Python = $PythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $Python) {
    throw 'No Python interpreter found. Pass -PythonPath or set GUANLAN_PYTHON/VIRTUAL_ENV.'
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$ServerPy = Join-Path $Repo 'guanlan_v2\server.py'
if (-not (Test-Path -LiteralPath $ServerPy -PathType Leaf)) {
    throw "Invalid Guanlan repository root (missing guanlan_v2\server.py): $Repo"
}
$VarDir   = Join-Path $Repo 'var'
$DeployLock = Join-Path $VarDir 'deploy-9999.lock'
$Log      = Join-Path $VarDir 'watchdog-9999.log'
$State    = Join-Path $VarDir 'check_9999.state'
$Heart    = Join-Path $VarDir 'check_9999.heartbeat'
$SrvLog   = Join-Path $VarDir 'server-9999.log'
$Port     = 9999
$Health   = "http://127.0.0.1:$Port/workflow/list"
$GenMinutes = 5; $CycleSec = 30; $HangLimit = 6   # 6 consecutive HTTP fails (~3 min) -> force restart

if (-not (Test-Path $VarDir)) { New-Item -ItemType Directory -Force -Path $VarDir | Out-Null }
function Log([string]$m) {
    try {
        if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 5MB)) {
            $b = "$Log.1"; if (Test-Path $b) { Remove-Item $b -Force }; Move-Item $Log $b -Force }
        Add-Content -Path $Log -Value ('{0} [check] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
    } catch { }
}

function Set-VerifiedPitTimeAttestation {
    $names = @(
        'GL_PIT_NTP_STATUS', 'GL_PIT_NTP_SOURCE',
        'GL_PIT_NTP_CHECKED_AT', 'GL_PIT_NTP_MAX_AGE_SECONDS'
    )
    # A failed live probe must clear any attestation inherited from an older
    # watchdog generation; stale process state can never keep PIT green.
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    try {
        $raw = & $Python (Join-Path $Repo 'scripts\check_windows_time_attestation.py') 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        $attestation = $raw | ConvertFrom-Json
        if ($attestation.schema_version -cne 'windows-time-attestation/v1' -or
            $attestation.ready -ne $true -or
            [string]::IsNullOrWhiteSpace([string]$attestation.checked_at)) {
            return $false
        }
        [Environment]::SetEnvironmentVariable('GL_PIT_NTP_STATUS', 'synchronized', 'Process')
        [Environment]::SetEnvironmentVariable('GL_PIT_NTP_SOURCE', 'w32time-os-status', 'Process')
        [Environment]::SetEnvironmentVariable('GL_PIT_NTP_CHECKED_AT', [string]$attestation.checked_at, 'Process')
        [Environment]::SetEnvironmentVariable('GL_PIT_NTP_MAX_AGE_SECONDS', '86400', 'Process')
        return $true
    } catch {
        return $false
    }
}

$PitTimeReady = Set-VerifiedPitTimeAttestation
if ($ValidatePitTimeOnly) {
    [pscustomobject]@{
        schema_version = 'watchdog-pit-time/v1'
        ready = [bool]$PitTimeReady
        checked_at = if ($PitTimeReady) { $env:GL_PIT_NTP_CHECKED_AT } else { $null }
        source = if ($PitTimeReady) { $env:GL_PIT_NTP_SOURCE } else { $null }
    } | ConvertTo-Json -Depth 3 -Compress
    exit $(if ($PitTimeReady) { 0 } else { 2 })
}
if (-not $PitTimeReady) {
    Log 'strict PIT time attestation blocked; live/current service remains available'
}
function Get-Fails { try { [int](Get-Content $State -Raw -ErrorAction Stop | ConvertFrom-Json).fails } catch { 0 } }
function Set-Fails([int]$n) { try { ('{"fails":' + $n + '}') | Set-Content -Path $State -Encoding ascii } catch { } }
function Get-ListenerPids {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique) }
function Test-Health {
    try { $q = [System.Net.WebRequest]::Create($Health); $q.Timeout = 5000; $q.Proxy = $null
          $r = $q.GetResponse(); $c = [int]$r.StatusCode; $r.Close(); return ($c -eq 200)
    } catch { return $false } }
function Stop-Listeners {
    foreach ($p in Get-ListenerPids) {
        try { Log ("killing pid {0} on {1}" -f $p, $Port); Stop-Process -Id $p -Force -ErrorAction Stop }
        catch { Log ("kill {0} failed: {1}" -f $p, $_.Exception.Message) } }
    $dl = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $dl) { if (-not (Get-ListenerPids)) { return $true }; Start-Sleep -Milliseconds 500 }
    return $false }
function Start-Server {
    $cl = '/S /C ""{0}" "{1}" >> "{2}" 2>&1"' -f $Python, $ServerPy, $SrvLog
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList $cl -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
    Log ("server start requested (wrapper pid {0})" -f $p.Id) }

# generation mutex: cannot acquire = previous generation alive -> exit
# (double bootstrap / server-side revive spawning extras is harmless)
$mtx = New-Object System.Threading.Mutex($false, 'Global\guanlan_v2_9999_check')
if (-not $mtx.WaitOne(0)) { exit 0 }
Log ("generation start (pid {0})" -f $PID)
$deadline = (Get-Date).AddMinutes($GenMinutes)
do {
    try {
        New-Item -ItemType File -Path $Heart -Force | Out-Null       # heartbeat (server-side revive signal)
        if (Test-Path $DeployLock) {
            # A generation must not end the successor chain merely because a
            # deployment lock happens to span its startup. Keep handing off;
            # the first unlocked generation resumes normal health ownership.
            Set-Fails 0
        } else {
            $fails = Get-Fails
            if (-not (Get-ListenerPids)) {
                Log 'no listener -> start server'
                Stop-Listeners | Out-Null; Start-Server; Set-Fails 0
                # create_app performs several cold imports before binding :9999.
                # Give that one launch a full window so the next 30s pass does not
                # spawn a duplicate server while the first process is still healthy.
                if (-not $Once) { Start-Sleep -Seconds 60 }
            } elseif (Test-Health) {
                if ($fails -gt 0) { Log 'health recovered' }
                Set-Fails 0
            } else {
                $fails++; Set-Fails $fails
                Log ("health fail {0}/{1}" -f $fails, $HangLimit)
                if ($fails -ge $HangLimit) {
                    Log 'hang limit -> force restart'
                    Stop-Listeners | Out-Null; Start-Server; Set-Fails 0
                }
            }
        }
    } catch { Log ("check error: {0}" -f $_.Exception.Message) }
    if ($Once) { break }
    Start-Sleep -Seconds $CycleSec
} while ((Get-Date) -lt $deadline)

$mtx.ReleaseMutex()   # CRITICAL: release BEFORE spawning successor, else it loses the mutex race and the chain dies
if (-not $Once) {
    $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $cl = '"{0}" --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{1}" -RepoRoot "{2}" -PythonPath "{3}"' -f `
        $conhost, $PSCommandPath, $Repo, $Python
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cl }
    Log ("successor spawned rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
}
