# Build a reviewer-ready ZIP (tracked Git files only via git archive).

param([string]$OutputPath = "")

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not (Test-Path ".git")) {
    Write-Error "Not a git repository: $repoRoot"
}

git rev-parse --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "No commit on HEAD; make at least one commit before exporting."
}

$date = Get-Date -Format "yyyy-MM-dd"
$exportsDir = Join-Path $repoRoot "exports"
if (-not (Test-Path $exportsDir)) {
    New-Item -ItemType Directory -Path $exportsDir | Out-Null
}

if (-not $OutputPath) {
    $OutputPath = Join-Path $exportsDir "mdhhs-poc-builder-review-$date.zip"
}

$parent = Split-Path -Parent $OutputPath
if ($parent -and -not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}

git archive --format=zip --output="$OutputPath" HEAD
$item = Get-Item $OutputPath
$mb = [math]::Round($item.Length / 1MB, 2)

Write-Host "Review export written:"
Write-Host ('  ' + $item.FullName + '  (' + [string]$mb + ' MB)')
Write-Host ''
Write-Host 'Exports match the Git tree only. Commit desired files before zipping.'
Write-Host 'Do not commit PHI, backend uploads (storage/), or secrets.'
