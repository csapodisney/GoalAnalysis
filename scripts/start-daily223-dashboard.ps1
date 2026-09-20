$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Import-GoalAnalysisSecret {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][string]$Prompt
    )

    $Current = [Environment]::GetEnvironmentVariable($EnvironmentName, "Process")
    if (-not [string]::IsNullOrWhiteSpace($Current)) {
        return $Current
    }

    $SecretDirectory = Join-Path $ProjectRoot "data\secrets"
    $SecretPath = Join-Path $SecretDirectory "$EnvironmentName.txt"
    if (Test-Path -LiteralPath $SecretPath) {
        $SecureValue = Get-Content -Raw -LiteralPath $SecretPath | ConvertTo-SecureString
    } else {
        New-Item -ItemType Directory -Path $SecretDirectory -Force | Out-Null
        $SecureValue = Read-Host $Prompt -AsSecureString
        $SecureValue | ConvertFrom-SecureString | Set-Content -LiteralPath $SecretPath
    }
    return [System.Net.NetworkCredential]::new("", $SecureValue).Password
}

$env:API_FOOTBALL_KEY = Import-GoalAnalysisSecret `
    -EnvironmentName "API_FOOTBALL_KEY" `
    -Prompt "API-Football kulcs"
$env:THE_ODDS_API_KEY = Import-GoalAnalysisSecret `
    -EnvironmentName "THE_ODDS_API_KEY" `
    -Prompt "The Odds API kulcs"

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "A Python-környezet nem található: $Python"
}

& $Python "scripts\daily223-dashboard.py" --open-browser
