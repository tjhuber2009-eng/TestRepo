param(
  [Parameter(Mandatory=$true)][string]$CommonFiles,
  [string]$OutDir = ""
)
$ErrorActionPreference="Stop"
$root=Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $OutDir) {
  $OutDir=Join-Path $PSScriptRoot "reports"
}
python (Join-Path $PSScriptRoot "tournament_rank.py") --common-files $CommonFiles --out-dir $OutDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Tournament ranking written to $OutDir"
