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

    foreach ($Name in @("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")) {
        try {
            $PlainValue = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName $Name
            [Environment]::SetEnvironmentVariable($Name, $PlainValue, "Process")
        } catch {
            [Environment]::SetEnvironmentVariable($Name, $null, "Process")
            Write-Warning "The dashboard will open, but $Name is unavailable. New analysis needs this key."
            Write-Host "Repair: powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\configure-arthur-sports.ps1`" -Name $Name"
            Write-Host "Restart Arthur after repairing the key. Saved reports remain viewable."
        }
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
