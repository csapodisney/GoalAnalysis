[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$LogDirectory = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogPath = Join-Path $LogDirectory "arthur-scheduler.log"

. (Join-Path $PSScriptRoot "arthur-secrets.ps1")

try {
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] Starting scheduled Arthur run." -f (Get-Date -Format o))
    $env:API_FOOTBALL_KEY = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "API_FOOTBALL_KEY" -SavedOnly
    try {
        $env:THE_ODDS_API_KEY = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName "THE_ODDS_API_KEY" -SavedOnly
    } catch {
        $env:THE_ODDS_API_KEY = $null
        Write-Warning "Odds key unavailable. Arthur will show unpriced previews; configure-arthur-sports.ps1 repairs the key."
    }
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python environment missing. Run scripts\install-arthur.ps1."
    }
    & $Python "scripts\run-arthur.py" --config "config\daily223-live.json" --settings "config\arthur-settings.json" --live --settle *>> $LogPath
    $RunExitCode = $LASTEXITCODE
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] Arthur finished; exit code {1}." -f (Get-Date -Format o), $RunExitCode)
    exit $RunExitCode
} catch {
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] Arthur failed: {1}" -f (Get-Date -Format o), $_.Exception.Message)
    exit 1
}
