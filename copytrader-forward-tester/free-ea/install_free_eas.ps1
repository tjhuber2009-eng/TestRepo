param(
  [string]$Mql5Path,
  [switch]$ListTerminals,
  [switch]$SkipSafeScalper,
  [switch]$SkipApex
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
  if ($terms.Count -eq 1) {
    $Mql5Path = $terms[0]
  } else {
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
  Write-Host "Installing SafeScalper CodeBase v1.20 from MQL5..."
  $srcUrl = "https://www.mql5.com/en/code/download/71189/ASQ_SafeScalping_CodeBase.mq5"
  $setUrl = "https://www.mql5.com/en/code/download/71189/ASQ_SafeScalping_XAUUSD_M5_v2.set"
  Invoke-WebRequest -UseBasicParsing $srcUrl -OutFile (Join-Path $experts "ASQ_SafeScalping_CodeBase.mq5")
  Invoke-WebRequest -UseBasicParsing $setUrl -OutFile (Join-Path $presets "ASQ_SafeScalping_XAUUSD_M5_v2.set")
}

if (-not $SkipApex) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install the pinned ApexBreakout upstream. Install Git for Windows or rerun with -SkipApex."
  }
  $sha = "3dab20e20a846edae9ac6fcf56d6b090dbba9f98"
  $tmp = Join-Path $env:TEMP ("copytrader-apex-" + [guid]::NewGuid().ToString("N"))
  try {
    git clone --quiet --no-checkout https://github.com/sbrakni/MQL5-trading-bot-claude-experiment.git $tmp
    Push-Location $tmp
    git checkout --quiet $sha
    $actual = (git rev-parse HEAD).Trim()
    if ($actual -ne $sha) { throw "Pinned Apex commit mismatch: $actual" }
    Pop-Location

    Copy-Item (Join-Path $tmp "MQL5\Experts\ApexBreakout.mq5") (Join-Path $experts "ApexBreakout.mq5") -Force
    foreach ($p in @(
      "ApexBreakout_XAUUSD_H1_Donchian.set",
      "ApexBreakout_USDJPY_H1_Session_V3_Turbo.set",
      "ApexBreakout_USDJPY_H1_Session_V2_Guarded.set"
    )) {
      Copy-Item (Join-Path $tmp "MQL5\Presets\$p") (Join-Path $presets $p) -Force
    }

    if (Test-Path (Join-Path $experts "ApexBreakoutRecovery.mq5")) {
      throw "Recovery/martingale EA appeared in tournament install path; refusing."
    }
  } finally {
    if ((Get-Location).Path -eq $tmp) { Pop-Location }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  }
}

Write-Host ""
Write-Host "Installed tournament files to:"
Write-Host "  Experts: $experts"
Write-Host "  Presets: $presets"
Write-Host ""
Write-Host "Next:"
Write-Host "1. Open MetaEditor and compile each .mq5 under Experts\CopyTraderFree."
Write-Host "2. Use separate DEMO accounts for each candidate."
Write-Host "3. Load the exact preset from FREE_EA_START_MANIFEST.json."
Write-Host "4. Attach CopyTraderDemoReporter to a spare chart with the matching CandidateId."
Write-Host "5. Do not deposit, withdraw, or manually trade after baseline."
