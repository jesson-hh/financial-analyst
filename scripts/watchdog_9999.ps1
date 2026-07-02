# [DEPRECATED 2026-07-02] Replaced by check_9999.ps1 (generational self-rotation)
# + register_check_9999.ps1. This resident loop freezes on this box over time and
# its frozen instance holds the global mutex, blocking new ones (dual-end outage
# observed 2026-07-02). Kept for history/rollback only.
#
# watchdog_9999.ps1 -- keeps the guanlan-v2 backend (127.0.0.1:9999) alive.
#
# Registered as scheduled task 'guanlan-v2-9999-watchdog' (see register_watchdog_9999.ps1).
# Loop: every $CheckEvery seconds check the listener + HTTP health of :9999.
#   - no listener            -> kill leftovers, wait for port release (10048 guard), start server
#   - listener but HTTP dead -> after $HangLimit consecutive failures, kill by PID and restart
# Logs: var\watchdog-9999.log (watchdog) and var\server-9999.log (server stdout/stderr).
# The server itself (guanlan_v2/server.py) is never modified.

$ErrorActionPreference = 'Continue'

$Repo       = 'G:\guanlan-v2'
$Python     = 'G:\financial-analyst\.venv\Scripts\python.exe'
$ServerPy   = Join-Path $Repo 'guanlan_v2\server.py'
$VarDir     = Join-Path $Repo 'var'
$WdLog      = Join-Path $VarDir 'watchdog-9999.log'
$SrvLog     = Join-Path $VarDir 'server-9999.log'
$Port       = 9999
$HealthUrl  = "http://127.0.0.1:$Port/workflow/list"
$CheckEvery = 10     # seconds between checks
$HangLimit  = 6      # consecutive HTTP failures (port still open) before forced restart (~60s)
$BootWait   = 120    # seconds to wait for the server to become healthy after a start

# LLM calls from the server need the Clash proxy; mirror the interactive environment.
$env:HTTP_PROXY  = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY    = 'localhost,127.0.0.1,::1,.local'

# single-instance guard (keepalive trigger may fire while we are already running)
$script:mtx = New-Object System.Threading.Mutex($false, 'Global\guanlan_v2_9999_watchdog')
if (-not $script:mtx.WaitOne(0)) { exit 0 }

if (-not (Test-Path $VarDir)) { New-Item -ItemType Directory -Force -Path $VarDir | Out-Null }

function Rotate-Log([string]$path, [int]$maxMB) {
    try {
        if ((Test-Path $path) -and ((Get-Item $path).Length -gt ($maxMB * 1MB))) {
            $bak = "$path.1"
            if (Test-Path $bak) { Remove-Item $bak -Force }
            Move-Item $path $bak -Force
        }
    } catch { }
}

function Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    try { Add-Content -Path $WdLog -Value $line } catch { }
}

function Get-ListenerPids {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-Health {
    try {
        $req = [System.Net.WebRequest]::Create($HealthUrl)
        $req.Timeout = 5000
        $req.Proxy = $null
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $resp.Close()
        return ($code -eq 200)
    } catch { return $false }
}

# Kill every process listening on $Port, then wait for the OS to release the port
# (avoids WinError 10048 when the new server binds).
function Stop-Listeners {
    foreach ($lpid in Get-ListenerPids) {
        try {
            $name = (Get-Process -Id $lpid -ErrorAction Stop).ProcessName
            Log ("killing pid {0} ({1}) listening on {2}" -f $lpid, $name, $Port)
            Stop-Process -Id $lpid -Force -ErrorAction Stop
        } catch { Log ("kill pid {0} failed: {1}" -f $lpid, $_.Exception.Message) }
    }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-ListenerPids)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-Server {
    Rotate-Log $SrvLog 20
    # cmd /S /C handles the quoting and gives us append-redirection of stdout+stderr.
    $cmdLine = '/S /C ""{0}" "{1}" >> "{2}" 2>&1"' -f $Python, $ServerPy, $SrvLog
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine `
        -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
    Log ("server start requested (wrapper pid {0})" -f $p.Id)
}

Log ("watchdog started (pid {0})" -f $PID)
$hangCount = 0

while ($true) {
    try {
        Rotate-Log $WdLog 5
        $listeners = Get-ListenerPids
        if (-not $listeners) {
            Log ("no listener on {0} -> starting server" -f $Port)
            Stop-Listeners | Out-Null   # no-op when empty; guards half-dead leftovers
            Start-Server
            $deadline = (Get-Date).AddSeconds($BootWait)
            $healthy = $false
            while ((Get-Date) -lt $deadline) {
                if (Test-Health) { $healthy = $true; break }
                Start-Sleep -Seconds 3
            }
            if ($healthy) { Log 'server healthy'; $hangCount = 0 }
            else { Log ("WARN server not healthy after {0}s, will keep watching" -f $BootWait) }
        }
        elseif (Test-Health) {
            if ($hangCount -gt 0) { Log 'health recovered' }
            $hangCount = 0
        }
        else {
            $hangCount++
            Log ("health check failed ({0}/{1}), listener pid {2} still up" -f $hangCount, $HangLimit, ($listeners -join ','))
            if ($hangCount -ge $HangLimit) {
                Log 'hang limit reached -> force restart'
                Stop-Listeners | Out-Null
                $hangCount = 0
                continue    # next iteration sees no listener and starts the server
            }
        }
    } catch {
        Log ("watchdog error: {0}" -f $_.Exception.Message)
    }
    Start-Sleep -Seconds $CheckEvery
}
