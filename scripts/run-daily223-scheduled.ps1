$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Import-GoalAnalysisSecret {
    param([Parameter(Mandatory = $true)][string]$EnvironmentName)
    $SecretPath = Join-Path $ProjectRoot "data\secrets\$EnvironmentName.txt"
    if (-not (Test-Path -LiteralPath $SecretPath)) {
        throw "Hiányzó titkosított kulcs: $EnvironmentName"
    }
    $SecureValue = Get-Content -Raw -LiteralPath $SecretPath | ConvertTo-SecureString
    return [System.Net.NetworkCredential]::new("", $SecureValue).Password
}

$env:API_FOOTBALL_KEY = Import-GoalAnalysisSecret "API_FOOTBALL_KEY"
$env:THE_ODDS_API_KEY = Import-GoalAnalysisSecret "THE_ODDS_API_KEY"

$LogDirectory = Join-Path $ProjectRoot "reports\daily223"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogPath = Join-Path $LogDirectory "scheduler.log"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

& $Python "scripts\run-daily-223-live.py" `
    --config "config\daily223-live.json" `
    --live *>> $LogPath

exit $LASTEXITCODE
