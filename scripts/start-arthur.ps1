[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $PSScriptRoot "arthur-secrets.ps1")

try {
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python environment missing. Run scripts\install-arthur.ps1."
    }
    foreach ($RequiredConfig in @("config\daily223-live.json", "config\arthur-settings.json")) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $RequiredConfig) -PathType Leaf)) {
            throw "Missing configuration: $RequiredConfig. Run scripts\install-arthur.ps1."
        }
    }

    Remove-Item Env:API_FOOTBALL_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:THE_ODDS_API_KEY -ErrorAction SilentlyContinue

    # Optional whole-package adviser; absence must never prevent startup.
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot "data\secrets\XAI_API_KEY.txt")) {
        try {
            $env:XAI_API_KEY = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "XAI_API_KEY" -SavedOnly
        } catch {
            Remove-Item Env:XAI_API_KEY -ErrorAction SilentlyContinue
            Write-Warning "Grok is unavailable; Arthur will start without it."
        }
    }
    # Primary sports source; the dashboard remains viewable without its saved key.
    Remove-Item Env:THESTATSAPI_API_KEY -ErrorAction SilentlyContinue
    try {
        $StatsKeyValue = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "THE_STATS_API_KEY" -SavedOnly
        [Environment]::SetEnvironmentVariable("THE_STATS_API_KEY", $StatsKeyValue, "Process")
    } catch {
        Remove-Item Env:THE_STATS_API_KEY -ErrorAction SilentlyContinue
    } finally {
        $StatsKeyValue = $null
    }
    $DashboardArguments = @("scripts\arthur-dashboard.py", "--config", "config\daily223-live.json")
    if (-not $NoBrowser) {
        $DashboardArguments += "--open"
    }
    Write-Host "Arthur: http://127.0.0.1:8765 . Keep this window open while using the dashboard."
    & $Python @DashboardArguments
    exit $LASTEXITCODE
} catch {
    Write-Host ("Arthur could not start: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
