# check_9999.ps1 -- guanlan-v2 backend (:9999) generational health checker.
#
# One GENERATION = up to $GenMinutes of $CycleSec-interval checks, then:
#   release mutex -> spawn successor via WMI Win32_Process.Create -> exit.
# No process lives longer than $GenMinutes => no long-resident freeze surface.
# NEVER Task Scheduler: on this box every Schedule-service child freezes at loader
# init (verified 2026-06-10, see register_watchdog_9999.ps1). WMI-spawned processes
# (parented to WmiPrvSE) are the verified-alive mechanism.
# Bootstrap: HKCU Run key (logon, see register_check_9999.ps1) + server-side heartbeat
# revive (guanlan_v2/server.py lifespan watches var\check_9999.heartbeat).
# State across generations: var\check_9999.state {"fails":n}.
# NOTE: ASCII-only comments -- PS 5.1 misparses BOM-less UTF-8 non-ASCII bytes.
param([switch]$Once)   # -Once: single check pass, no successor (for tests)

$ErrorActionPreference = 'Continue'
$Repo     = 'G:\guanlan-v2'
$Python   = 'G:\financial-analyst\.venv\Scripts\python.exe'
$ServerPy = Join-Path $Repo 'guanlan_v2\server.py'
$VarDir   = Join-Path $Repo 'var'
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
        $fails = Get-Fails
        if (-not (Get-ListenerPids)) {
            Log 'no listener -> start server'
            Stop-Listeners | Out-Null; Start-Server; Set-Fails 0
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
    } catch { Log ("check error: {0}" -f $_.Exception.Message) }
    if ($Once) { break }
    Start-Sleep -Seconds $CycleSec
} while ((Get-Date) -lt $deadline)

$mtx.ReleaseMutex()   # CRITICAL: release BEFORE spawning successor, else it loses the mutex race and the chain dies
if (-not $Once) {
    $cl = 'C:\Windows\System32\conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $PSCommandPath
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cl }
    Log ("successor spawned rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
}
