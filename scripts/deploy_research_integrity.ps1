param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$Source = 'C:\Users\AI-PC2601018\.codex\worktrees\64c6\guanlan-v2'
$Target = 'G:\guanlan-v2'
$Files = @(
 'engine/financial_analyst/data/loaders/qlib_binary.py',
 'engine/financial_analyst/data/loaders/technical.py',
 'engine/financial_analyst/factors/zoo/panel.py',
 'engine/financial_analyst/factors/zoo/panel_cache.py',
 'engine/financial_analyst/factors/eval/portfolio.py',
 'guanlan_v2/strategy/compute/training_runtime.py',
 'guanlan_v2/strategy/compute/model_train.py',
 'guanlan_v2/strategy/compute/model_workflow.py',
 'guanlan_v2/strategy/compute/v4.py',
 'guanlan_v2/strategy/compute/cpcv.py',
 'guanlan_v2/screen/model_registry.py',
 'guanlan_v2/strategy/model_health.py',
 'guanlan_v2/workflow/api.py',
 'guanlan_v2/workflow/executor.py',
 'guanlan_v2/workflow/migrations.py',
 'guanlan_v2/workflow/temporal_split.py',
 'guanlan_v2/workflow/factor_artifact.py',
 'ui/factor/workflow.jsx',
 'scripts/migrate_saved_workflows.py'
)
# These diverge locally and must only receive separately reviewed small patches.
$PatchOnly = @('scripts/check_9999.ps1','guanlan_v2/screen/api.py','ui/factor/观澜 · AI 工作流.html')
$Backup = Join-Path $Target ('var/deploy-integrity-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$Receipt = @()
foreach ($relative in ($Files + $PatchOnly)) {
    $from = [IO.Path]::GetFullPath((Join-Path $Source $relative))
    $to = [IO.Path]::GetFullPath((Join-Path $Target $relative))
    if (-not $from.StartsWith($Source + '\') -or -not $to.StartsWith($Target + '\')) { throw 'Path outside reviewed roots' }
    if ($Files -contains $relative -and -not (Test-Path -LiteralPath $from)) { throw ('Missing source ' + $relative) }
    $oldHash = if (Test-Path -LiteralPath $to) { (Get-FileHash -LiteralPath $to).Hash } else { $null }
    $newHash = if ($Files -contains $relative) { (Get-FileHash -LiteralPath $from).Hash } else { 'PATCH_ONLY' }
    $Receipt += [pscustomobject]@{path=$relative;before=$oldHash;after=$newHash}
    if ($Apply -and $oldHash) {
        $backupFile = Join-Path $Backup $relative
        New-Item -ItemType Directory -Path (Split-Path $backupFile) -Force | Out-Null
        Copy-Item -LiteralPath $to -Destination $backupFile
    }
}
if ($Apply) {
    if (-not (Test-Path -LiteralPath (Join-Path $Target 'var/deploy-9999.lock'))) { throw 'Deployment lock required' }
    if (Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue) { throw 'Service must be stopped before applying' }
    foreach ($relative in $Files) {
        Copy-Item -LiteralPath (Join-Path $Source $relative) -Destination (Join-Path $Target $relative)
    }
    $Receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Backup 'manifest.json') -Encoding utf8
}
[pscustomobject]@{apply=[bool]$Apply;backup=$Backup;files=$Receipt} | ConvertTo-Json -Depth 4
