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
$ErrorActionPreference = 'Stop'

$Repo    = 'G:\guanlan-v2'
$Pythonw = 'G:\financial-analyst\.venv\Scripts\pythonw.exe'
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

