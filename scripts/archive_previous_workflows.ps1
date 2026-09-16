[CmdletBinding()]
param(
    [ValidateSet('Inventory', 'Archive', 'Restore')][string]$Mode = 'Inventory',
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$ArchivePath,
    [switch]$Execute
)

# Deliberately scoped to this installed repository. Never recurse when removing
# saved graphs. Run Inventory BEFORE creating replacements, then keep its bytes.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$LiveRoot = 'G:\guanlan-v2'
$GraphRoot = 'G:\guanlan-v2\.data\workflows'
$ArchiveRoot = 'G:\guanlan-v2\var\archive'
$ApiBase = 'http://127.0.0.1:9999'

function Assert-PlainPath([string]$Path, [string]$Parent, [switch]$File) {
    $full = [IO.Path]::GetFullPath($Path)
    $expectedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($full).TrimEnd('\') -ne $expectedParent) {
        throw "Not an immediate child of expected directory: $full"
    }
    $cursor = $full
    while ($cursor -and $cursor.Length -gt 3) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is forbidden: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    if ($File -and (Test-Path -LiteralPath $full -PathType Container)) {
        throw "Expected a file: $full"
    }
    return $full
}

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Value($Record, [string]$Name, $Default = $null) {
    if ($Record.PSObject.Properties.Name -contains $Name) { return $Record.$Name }
    return $Default
}

function Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-Record([string]$Path, $Record) {
    # Generated receipt, not application configuration. Refuse overwriting it.
    $json = $Record | ConvertTo-Json -Depth 35
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Assert-NoActiveRuns {
    $runRoot = Join-Path $GraphRoot '_runs'
    if (Test-Path -LiteralPath $runRoot) {
        foreach ($file in Get-ChildItem -LiteralPath $runRoot -File -Filter '*.json') {
            $run = Read-Json $file.FullName
            if ((Value $run 'status' '') -in @('queued', 'running', 'pending', 'starting')) {
                throw "Active durable run prevents cleanup: $($file.Name)"
            }
        }
    }
}

$null = Assert-PlainPath $GraphRoot (Join-Path $LiveRoot '.data')
$manifestFull = [IO.Path]::GetFullPath($ManifestPath)

if ($Mode -eq 'Inventory') {
    if ($Execute) { throw 'Inventory is read-only on the live installation; omit Execute.' }
    if ($manifestFull.StartsWith($LiveRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Write the inventory receipt into the worktree, not the live repository.'
    }
    if (Test-Path -LiteralPath $manifestFull) { throw 'Manifest already exists; do not silently refresh the frozen target set.' }
    Assert-NoActiveRuns
    $records = @()
    foreach ($file in Get-ChildItem -LiteralPath $GraphRoot -File -Filter '*.json' | Sort-Object Name) {
        $path = Assert-PlainPath $file.FullName $GraphRoot -File
        $record = Read-Json $path
        $id = [string](Value $record 'id' '')
        if ($id -notmatch '^w[0-9a-f]{12}$' -or $file.Name -cne "$id.json") {
            throw "Invalid or mismatched graph id: $($file.Name); preserve it."
        }
        $scope = 'global'; $workspace = $null
        if ((Value $record 'scope' '') -eq 'workspace') {
            $scope = 'workspace'; $workspace = [string](Value $record 'workspace_id' '')
            if ($workspace -notmatch '^rw_(general|[0-9a-f]{12})$') { throw "Bad scope: $id" }
        }
        $graph = Value $record 'graph'
        if ($null -eq $graph -or $null -eq (Value $graph 'nodes') -or $null -eq (Value $graph 'edges')) {
            throw "Malformed graph: $id; preserve it."
        }
        $records += [pscustomobject]@{
            id = $id; name = [string](Value $record 'name' ''); scope = $scope
            workspace_id = $workspace; filename = $file.Name; path = $path
            sha256 = Hash $path; bytes = $file.Length; ts = Value $record 'ts' 0
            node_count = @($graph.nodes).Count; edge_count = @($graph.edges).Count
        }
    }
    if ($records.Count -ne 76) { throw "Expected exactly 76 pre-existing graphs; found $($records.Count). Review before changing the target set." }
    $owners = @('') + @($records | Where-Object scope -eq 'workspace' | Select-Object -ExpandProperty workspace_id -Unique)
    $apiChecks = @()
    foreach ($owner in $owners) {
        $url = "$ApiBase/workflow/list"
        if ($owner) { $url += '?workspace_id=' + [Uri]::EscapeDataString($owner) }
        $response = Invoke-RestMethod -Uri $url -TimeoutSec 15
        if (-not $response.ok) { throw "Workflow API unavailable: $url" }
        $expected = @($records | Where-Object { if ($owner) { $_.workspace_id -eq $owner } else { $_.scope -eq 'global' } })
        $actual = @($response.items)
        if ($actual.Count -ne $expected.Count) { throw "API/disk count mismatch in scope '$owner'" }
        foreach ($rec in $expected) {
            $match = @($actual | Where-Object id -eq $rec.id)
            if ($match.Count -ne 1 -or $match[0].scope -ne $rec.scope -or $match[0].name -cne $rec.name -or $match[0].workspace_id -ne $rec.workspace_id) {
                throw "API/disk metadata mismatch: $($rec.id)"
            }
        }
        $apiChecks += [pscustomobject]@{workspace_id=$owner; count=$actual.Count; match=$true}
    }
    $dependencyFiles = @()
    foreach ($relative in @('config', 'var\orchestration', 'var\jobs', 'var\watch_alerts', 'var\console\workspaces')) {
        $dir = Join-Path $LiveRoot $relative
        if (Test-Path -LiteralPath $dir) {
            $dependencyFiles += @(Get-ChildItem -LiteralPath $dir -Recurse -File | Where-Object { $_.Extension -in @('.json', '.jsonl', '.yaml', '.yml') })
        }
    }
    foreach ($relative in @('var\seats_watch.json', 'var\seats_watch_assignments.json', 'var\watch_deep_authorizations.jsonl')) {
        $path = Join-Path $LiveRoot $relative
        if (Test-Path -LiteralPath $path) { $dependencyFiles += Get-Item -LiteralPath $path }
    }
    $references = @(); $workflowKeyFiles = @()
    $idPattern = '\b(' + (($records.id | ForEach-Object { [Regex]::Escape($_) }) -join '|') + ')\b'
    foreach ($file in $dependencyFiles | Sort-Object FullName -Unique) {
        # Only retain path/id, never emit or persist config content or secrets.
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        if (-not $content) { continue }
        foreach ($id in @([Regex]::Matches($content, $idPattern) | ForEach-Object Value | Sort-Object -Unique)) {
            $references += [pscustomobject]@{path=$file.FullName; workflow_id=$id}
        }
        if ($content -match '"(?:workflow_id|workflowId|workflow_ref)"\s*:') { $workflowKeyFiles += $file.FullName }
    }
    if ($references.Count) { throw "Existing workflow ids occur in operational metadata; review these dependencies before cleanup: $($references | ConvertTo-Json -Compress)" }
    $receipt = [ordered]@{
        schema='guanlan-workflow-cleanup-v1'; generated_at=(Get-Date).ToString('o')
        live_root=$LiveRoot; graph_root=$GraphRoot; count=$records.Count
        api_checks=$apiChecks; dependency_files_checked=$dependencyFiles.Count
        explicit_id_references=$references; workflow_key_files=$workflowKeyFiles
        preserved=@('_runs', '_migration_backups', 'models', 'factors', 'conversations', 'new graphs not in this manifest')
        records=$records
    }
    $parent = Split-Path -Parent $manifestFull
    if (-not (Test-Path -LiteralPath $parent)) { $null = New-Item -ItemType Directory -Path $parent }
    Write-Record $manifestFull $receipt
    [pscustomobject]@{mode=$Mode; manifest=$manifestFull; count=$records.Count; manifest_sha256=(Hash $manifestFull); dependency_files=$dependencyFiles.Count; explicit_id_references=$references.Count; api_match=$true} | ConvertTo-Json
    return
}

$manifest = Read-Json $manifestFull
if ($manifest.schema -ne 'guanlan-workflow-cleanup-v1' -or $manifest.graph_root -ne $GraphRoot -or $manifest.live_root -ne $LiveRoot -or $manifest.count -ne 76 -or @($manifest.records).Count -ne 76) {
    throw 'Manifest is not the frozen 76-graph inventory for this installation.'
}
if (-not $ArchivePath) { throw 'ArchivePath is required for Archive or Restore.' }
$archiveFull = Assert-PlainPath $ArchivePath $ArchiveRoot
if ([IO.Path]::GetFileName($archiveFull) -notmatch '^workflows-before-trend-pullback-[0-9-]+$') { throw 'Unexpected archive directory name.' }
$originals = Join-Path $archiveFull 'originals'
$removed = Join-Path $archiveFull 'removed'
$paths = @(); $seen = @{}
foreach ($rec in $manifest.records) {
    if ($rec.id -notmatch '^w[0-9a-f]{12}$' -or $rec.filename -cne ($rec.id + '.json') -or $rec.sha256 -notmatch '^[0-9a-f]{64}$' -or $seen.ContainsKey($rec.id)) { throw 'Invalid or duplicate manifest record.' }
    $seen[$rec.id] = $true
    $path = Assert-PlainPath (Join-Path $GraphRoot $rec.filename) $GraphRoot -File
    if ($path -ne $rec.path) { throw "Manifest target mismatch: $path" }
    $backup = Assert-PlainPath (Join-Path $originals $rec.filename) $originals -File
    $moved = Assert-PlainPath (Join-Path $removed $rec.filename) $removed -File
    if ($Mode -eq 'Archive') {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Hash $path) -ne $rec.sha256) { throw "Target missing or changed; nothing else will be archived: $path" }
    } else {
        if (-not (Test-Path -LiteralPath $backup -PathType Leaf) -or (Hash $backup) -ne $rec.sha256) { throw "Backup missing or corrupt: $backup" }
        if ((Test-Path -LiteralPath $path) -and (Hash $path) -ne $rec.sha256) { throw "Restore refuses to overwrite a newer graph: $path" }
    }
    $paths += [pscustomobject]@{record=$rec; source=$path; backup=$backup; moved=$moved}
}
Assert-NoActiveRuns
if (-not $Execute) {
    [pscustomobject]@{mode=$Mode; dry_run=$true; count=$paths.Count; archive=$archiveFull; manifest_sha256=(Hash $manifestFull); note='No live files changed. Pass -Execute only after dependency review.'} | ConvertTo-Json
    return
}
if ($Mode -eq 'Archive') {
    if (Test-Path -LiteralPath $archiveFull) { throw 'Archive directory already exists; never reuse it.' }
    $null = New-Item -ItemType Directory -Path $originals
    $null = New-Item -ItemType Directory -Path $removed
    [IO.File]::Copy($manifestFull, (Join-Path $archiveFull 'manifest.json'), $false)
    # Copy and hash ALL originals before any graph leaves the active list.
    foreach ($item in $paths) {
        [IO.File]::Copy($item.source, $item.backup, $false)
        if ((Hash $item.backup) -ne $item.record.sha256) { throw "Backup mismatch: $($item.source)" }
    }
    foreach ($item in $paths) {
        if ((Hash $item.source) -ne $item.record.sha256) { throw "Concurrent modification detected: $($item.source)" }
    }
    foreach ($item in $paths) {
        if ((Hash $item.source) -ne $item.record.sha256) { throw "Concurrent modification detected: $($item.source)" }
        Move-Item -LiteralPath $item.source -Destination $item.moved -ErrorAction Stop
        if ((Hash $item.moved) -ne $item.record.sha256) {
            if (-not (Test-Path -LiteralPath $item.source)) { Move-Item -LiteralPath $item.moved -Destination $item.source -ErrorAction Stop }
            throw "Move raced a graph update; changed bytes preserved: $($item.source)"
        }
    }
} else {
    # Keep the archive for audit; never overwrite a same-id newer graph.
    foreach ($item in $paths) {
        if (-not (Test-Path -LiteralPath $item.source)) { [IO.File]::Copy($item.backup, $item.source, $false) }
        if ((Hash $item.source) -ne $item.record.sha256) { throw "Restore verification failed: $($item.source)" }
    }
}
[pscustomobject]@{mode=$Mode; completed=$true; count=$paths.Count; archive=$archiveFull; preserved_new_files=@(Get-ChildItem -LiteralPath $GraphRoot -File -Filter '*.json' | Where-Object { -not $seen.ContainsKey($_.BaseName) }).Count} | ConvertTo-Json
