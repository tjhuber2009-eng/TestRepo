param(
  [string]$Mql5Path,
  [switch]$ListTerminals,
  [switch]$SkipSafeScalper,
  [switch]$SkipFvgGold
)

$ErrorActionPreference = "Stop"

function Get-Terminals {
  $root = Join-Path $env:APPDATA "MetaQuotes\Terminal"
  if (-not (Test-Path $root)) { return @() }
  Get-ChildItem $root -Directory | ForEach-Object {
    $m = Join-Path $_.FullName "MQL5"
    if (Test-Path $m) { $m }
  }
}

if ($ListTerminals) {
  Get-Terminals | ForEach-Object { Write-Host $_ }
  exit 0
}

if (-not $Mql5Path) {
  $terms = @(Get-Terminals)
  if ($terms.Count -eq 1) { $Mql5Path = $terms[0] }
  else {
    Write-Host "Found $($terms.Count) MT5 data folders:"
    $terms | ForEach-Object { Write-Host "  $_" }
    throw "Specify -Mql5Path '...\MQL5' so the installer cannot choose the wrong terminal."
  }
}

$Mql5Path = (Resolve-Path $Mql5Path).Path
$experts = Join-Path $Mql5Path "Experts\CopyTraderFree"
$presets = Join-Path $Mql5Path "Presets"
New-Item -ItemType Directory -Force -Path $experts,$presets | Out-Null

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$reporter = Join-Path $repoRoot "copytrader-forward-tester\mt5-demo\CopyTraderDemoReporter.mq5"
if (Test-Path $reporter) {
  Copy-Item $reporter (Join-Path $experts "CopyTraderDemoReporter.mq5") -Force
}

if (-not $SkipSafeScalper) {
  Write-Host "Installing official MQL5 CodeBase SafeScalper v1.20..."
  Invoke-WebRequest -UseBasicParsing "https://www.mql5.com/en/code/download/71189/ASQ_SafeScalping_CodeBase.mq5" -OutFile (Join-Path $experts "ASQ_SafeScalping_CodeBase.mq5")
  Invoke-WebRequest -UseBasicParsing "https://www.mql5.com/en/code/download/71189/ASQ_SafeScalping_XAUUSD_M5_v2.set" -OutFile (Join-Path $presets "ASQ_SafeScalping_XAUUSD_M5_v2.set")
}

if (-not $SkipFvgGold) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required for the pinned MIT FvgGold install. Install Git for Windows or rerun with -SkipFvgGold."
  }
  $sha = "a8a521c2c6e619a5f9fc7f80cad63242d1e236b5"
  $tmp = Join-Path $env:TEMP ("copytrader-fvggold-" + [guid]::NewGuid().ToString("N"))
  try {
    git clone --quiet --no-checkout https://github.com/foeed/FvgGold-EA.git $tmp
    Push-Location $tmp
    git checkout --quiet $sha
    $actual = (git rev-parse HEAD).Trim()
    if ($actual -ne $sha) { throw "Pinned FvgGold commit mismatch: $actual" }
    if (-not (Select-String -Path "LICENSE" -Pattern "MIT License" -Quiet)) {
      throw "Pinned FvgGold license verification failed."
    }
    Pop-Location
    Copy-Item (Join-Path $tmp "FvgGold.mq5") (Join-Path $experts "FvgGold.mq5") -Force
  } finally {
    if ((Get-Location).Path -eq $tmp) { Pop-Location }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# Fail closed: legacy no-license candidates are not installed by this script.
foreach ($legacy in @("ApexBreakout.mq5","ApexBreakoutRecovery.mq5")) {
  if (Test-Path (Join-Path $experts $legacy)) {
    Write-Warning "$legacy already exists from an older launch pack. It is not an active licensed tournament candidate; remove or isolate it before baseline."
  }
}

Write-Host ""
Write-Host "Automatic licensed/official-source installation complete."
Write-Host "Experts: $experts"
Write-Host "Presets: $presets"
Write-Host ""
Write-Host "Manual MQL5 Market candidates must be installed inside MT5 only after the product page shows FREE."
Write-Host "Freeze the exact version at baseline; do not silently substitute versions."
