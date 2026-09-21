[CmdletBinding()]
param([switch]$SkipCheck)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python environment missing. Run install-arthur.ps1."
}

# API credentials must not choose a different billing route for this subprocess.
# Restore the caller's environment when this script finishes.
$SavedEnvironment = @{}
foreach ($Name in @("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "API_FOOTBALL_KEY", "THE_ODDS_API_KEY")) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    [Environment]::SetEnvironmentVariable($Name, $null, "Process")
}
try {
    $Codex = Get-Command "codex.cmd" -ErrorAction SilentlyContinue
    if (-not $Codex) { $Codex = Get-Command "codex.exe" -ErrorAction SilentlyContinue }
    $NeedsInstall = -not $Codex
    if ($Codex) {
        $VersionText = (& $Codex.Source --version 2>&1 | Out-String)
        if ($VersionText -match 'codex-cli (\d+\.\d+\.\d+)') {
            $NeedsInstall = [version]$Matches[1] -lt [version]"0.155.1"
        } else { $NeedsInstall = $true }
    }
    if ($NeedsInstall) {
        $Npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
        if (-not $Npm) {
            throw "Node.js/npm is missing. Install Node.js LTS from https://nodejs.org/ and open a new PowerShell window, then run the Arthur installer again."
        }
        Write-Host "Installing the official Codex CLI (0.155.1). No API subscription is created."
        & $Npm.Source install --global "@openai/codex@0.155.1"
        if ($LASTEXITCODE -ne 0) { throw "Codex installation failed. No new schedule was registered." }
        $Codex = Get-Command "codex.cmd" -ErrorAction SilentlyContinue
        if (-not $Codex) { $Codex = Get-Command "codex.exe" -ErrorAction SilentlyContinue }
        if (-not $Codex) { throw "Codex was installed but is not on PATH. Open a new PowerShell window and rerun the installer." }
    }
    $StatusJson = (& $Python "scripts\run-arthur.py" --check-codex-login | Out-String)
    $LoginStatus = $StatusJson | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $LoginStatus.login_available) {
        Write-Host "Sign in with your existing ChatGPT account in the browser opened by Codex. Do not choose API-key login."
        & $Codex.Source login
        if ($LASTEXITCODE -ne 0) { throw "ChatGPT login did not complete. No API fallback was attempted." }
        $StatusJson = (& $Python "scripts\run-arthur.py" --check-codex-login | Out-String)
        $LoginStatus = $StatusJson | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or -not $LoginStatus.login_available) {
            throw "Codex has not confirmed ChatGPT login. API-key authentication is not used by Arthur."
        }
    }
    Write-Host "Codex ChatGPT login is saved. Model access will be checked separately."
    if (-not $SkipCheck) {
        & $Python "scripts\run-arthur.py" --check-openai
        if ($LASTEXITCODE -ne 0) { throw "Codex/ChatGPT model check failed. No paid API request was attempted." }
    }
} finally {
    foreach ($Name in $SavedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name], "Process")
    }
}
