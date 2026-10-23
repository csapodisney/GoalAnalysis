[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Runner = Join-Path $PSScriptRoot "run-arthur-nightly.py"
# The diagnostic path must not decrypt credentials, create logs, or run the job.
if ($CheckOnly) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-Error "Python environment missing. Run scripts\install-arthur.ps1."
        exit 1
    }
    & $Python $Runner --check-schedule
    exit $LASTEXITCODE
}

$LogPath = Join-Path $ProjectRoot "logs\arthur-scheduler.log"
try {
    . (Join-Path $PSScriptRoot "arthur-secrets.ps1")
    # A missing sports key must not prevent local learning from saved results.
    # SavedOnly prevents stale inherited environment values masking a broken key.
    Remove-Item Env:THESTATSAPI_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:API_FOOTBALL_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:THE_ODDS_API_KEY -ErrorAction SilentlyContinue
    foreach ($Name in @("XAI_API_KEY", "THE_STATS_API_KEY")) {
        try {
            $Value = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName $Name -SavedOnly
            [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        } catch {
            [Environment]::SetEnvironmentVariable($Name, $null, "Process")
        } finally {
            $Value = $null
        }
    }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python environment missing. Run scripts\install-arthur.ps1."
    }
    # Python owns Europe/Berlin 22:00, catch-up cutoff, leases and idempotency.
    # Do not pass a Windows-local date or mirror provider/child error text to logs.
    $PreviousErrorPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 promotes native stderr to ErrorRecords. A harmless
        # library warning must not abort Python or replace its real exit code.
        $ErrorActionPreference = 'Continue'
        # Prices refresh independently of the once-nightly Astra generation.
        # Python enforces per-query 30-minute TTL, quotas and the 15-minute lockout.
        & $Python (Join-Path $PSScriptRoot 'run-arthur-odds-monitor.py') *> $null
        & $Python $Runner --scheduled *> $null
        $RunExitCode = $LASTEXITCODE
        # This optional bounded tick independently proves the saved daily freeze
        # and shared quota. It is disarmed until its validated runtime activation.
        & $Python (Join-Path $PSScriptRoot 'run-arthur-background-history.py') *> $null
    } finally {
        $ErrorActionPreference = $PreviousErrorPreference
    }
    # Successful no-op timer checks leave no log writes behind.
    if ($RunExitCode -ne 0) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
        Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] Arthur nightly check failed; exit code {1}. See the dashboard night-job status." -f [DateTime]::UtcNow.ToString("o"), $RunExitCode)
    }
    exit $RunExitCode
} catch {
    New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] Arthur nightly launcher failed. Check the Python environment and dashboard status." -f [DateTime]::UtcNow.ToString("o"))
    exit 1
}
