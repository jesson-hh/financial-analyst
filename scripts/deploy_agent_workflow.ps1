param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$TaskSource = 'C:\Users\AI-PC2601018\.codex\worktrees\64c6\guanlan-v2'
$TaskTarget = 'G:\guanlan-v2'
$TaskFiles = @(
 'guanlan_v2/workflow/authoring.py',
 'guanlan_v2/workflow/expression_safety.py',
 'guanlan_v2/workflow/api.py',
 'guanlan_v2/workflow/executor.py',
 'guanlan_v2/workflow/migrations.py',
 'guanlan_v2/workflow/run_store.py',
 'guanlan_v2/console/workflow_tools.py',
 'ui/factor/workflow.jsx',
 'scripts/recover_legacy_workflows.py'
)
$TaskPatchOnly = @(
 'guanlan_v2/console/api.py', 'guanlan_v2/console/tools.py',
 'guanlan_v2/console/workspace_tools.py', 'guanlan_v2/glmcp/server.py',
 'guanlan_v2/console/codex_backend.py', 'guanlan_v2/console/artifact_bridge.py',
 'ui/console/console-app.jsx', 'ui/console/console-data.jsx',
 'ui/console/console-thread.jsx', 'ui/console/console-bench.jsx',
 'ui/console/console-workspace-dock.jsx',
 'ui/console/观澜 · 帷幄.html', 'ui/factor/观澜 · AI 工作流.html'
)
$TaskBackup = Join-Path $TaskTarget ('var/deploy-agent-workflow-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$TaskReceipt = @()
if ($Apply) {
 if (-not (Test-Path -LiteralPath (Join-Path $TaskTarget 'var/deploy-9999.lock'))) {throw 'Reviewed deployment lock required'}
 if (Get-NetTCPConnection -LocalPort 9999 -State Listen -ErrorAction SilentlyContinue) {throw 'Service must be idle and stopped before applying'}
}
foreach ($TaskRelative in ($TaskFiles + $TaskPatchOnly)) {
 $TaskFrom = [IO.Path]::GetFullPath((Join-Path $TaskSource $TaskRelative))
 $TaskTo = [IO.Path]::GetFullPath((Join-Path $TaskTarget $TaskRelative))
 if (-not $TaskFrom.StartsWith($TaskSource + '\') -or -not $TaskTo.StartsWith($TaskTarget + '\')) {throw 'Outside reviewed roots'}
 $TaskOldHash = if (Test-Path -LiteralPath $TaskTo) {(Get-FileHash -LiteralPath $TaskTo).Hash} else {$null}
 $TaskNewHash = if ($TaskFiles -contains $TaskRelative) {(Get-FileHash -LiteralPath $TaskFrom).Hash} else {'PATCH_ONLY'}
 $TaskReceipt += [pscustomobject]@{path=$TaskRelative;before=$TaskOldHash;after=$TaskNewHash}
 if ($Apply -and $TaskOldHash) {
  $TaskBackupFile = Join-Path $TaskBackup $TaskRelative
  New-Item -ItemType Directory -Path (Split-Path $TaskBackupFile) -Force | Out-Null
  Copy-Item -LiteralPath $TaskTo -Destination $TaskBackupFile
 }
}
if ($Apply) {
 foreach ($TaskRelative in $TaskFiles) {
  Copy-Item -LiteralPath (Join-Path $TaskSource $TaskRelative) -Destination (Join-Path $TaskTarget $TaskRelative)
 }
 $TaskReceipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $TaskBackup 'manifest.json') -Encoding utf8
}
[pscustomobject]@{applied=[bool]$Apply;backup=$TaskBackup;files=$TaskReceipt} | ConvertTo-Json -Depth 4
