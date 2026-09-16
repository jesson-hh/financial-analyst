# install_desktop_shortcut.ps1 -- create the Guanlan desktop shell shortcut.
#
# Idempotent: re-running overwrites the two .lnk files in place.
# ASCII-only comments -- PS 5.1 misparses BOM-less UTF-8 non-ASCII bytes.
# This file MUST be saved as UTF-8 WITH BOM because it contains CJK string
# literals (the shortcut display name). Without the BOM PS 5.1 reads them
# as ANSI and the shortcut name comes out as mojibake.
#
# Launch form is `-m guanlan_v2.desktop`, NOT a script path: running a file
# inside the package puts the PACKAGE dir on sys.path[0] and leaves the repo
# root off it. That is exactly how 9999 was brought down on 2026-07-26.
param(
    [string]$RepoRoot = $env:GUANLAN_REPO_ROOT,
    [string]$PythonwPath = $env:GUANLAN_PYTHONW
)

$ErrorActionPreference = 'Stop'

$Repo = if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
} else {
    (Resolve-Path -LiteralPath $RepoRoot).Path
}
$PythonwCandidates = @(
    $PythonwPath,
    $(if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\pythonw.exe' }),
    (Join-Path $Repo '.venv\Scripts\pythonw.exe'),
    $(try { (Get-Command pythonw.exe -ErrorAction Stop).Source } catch { $null })
) | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }
$Pythonw = $PythonwCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $Pythonw) {
    throw 'No pythonw.exe found. Pass -PythonwPath or set GUANLAN_PYTHONW/VIRTUAL_ENV.'
}
$Pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
$Icon    = Join-Path $Repo 'guanlan_v2\desktop\guanlan.ico'
$Name    = '观澜.lnk'

foreach ($p in @($Pythonw, $Icon)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) $Name),
    (Join-Path ([Environment]::GetFolderPath('Programs')) $Name)
)

$ws = New-Object -ComObject WScript.Shell
foreach ($t in $targets) {
    $dir = Split-Path $t -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $lnk = $ws.CreateShortcut($t)
    $lnk.TargetPath       = $Pythonw
    $lnk.Arguments        = '-m guanlan_v2.desktop'
    $lnk.WorkingDirectory = $Repo
    $lnk.IconLocation     = $Icon
    $lnk.Description      = 'Guanlan desktop shell'
    $lnk.WindowStyle      = 1
    $lnk.Save()
    Write-Output "wrote $t"
}

