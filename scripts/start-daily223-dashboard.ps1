throw "This retired launcher is disabled. Use scripts/start-arthur.ps1 for the current Arthur dashboard."

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

Remove-Item Env:API_FOOTBALL_KEY -ErrorAction SilentlyContinue
Remove-Item Env:THE_ODDS_API_KEY -ErrorAction SilentlyContinue
$env:THE_STATS_API_KEY = Import-GoalAnalysisSecret `
    -EnvironmentName "THE_STATS_API_KEY" `
    -Prompt "TheStats API kulcs"

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "A Python-környezet nem található: $Python"
}

& $Python "scripts\daily223-dashboard.py" --open-browser
