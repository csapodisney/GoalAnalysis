[CmdletBinding()]
param(
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$DailyTime = "22:00",
    [switch]$WakeToRun,
    [switch]$ReplaceKeys,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
if ($DailyTime -ne '22:00') {
    throw "Arthur now runs at 22:00 Europe/Berlin. Custom Windows-local DailyTime values are no longer supported."
}
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
. (Join-Path $PSScriptRoot "arthur-secrets.ps1")

function Save-ArthurSecret {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][string]$Prompt
    )

    $SecretDirectory = Join-Path $ProjectRoot "data\secrets"
    $SecretPath = Join-Path $SecretDirectory "$EnvironmentName.txt"
    $CurrentValue = [Environment]::GetEnvironmentVariable($EnvironmentName, "Process")
    if ($ReplaceKeys) {
        $SecureValue = Read-Host $Prompt -AsSecureString
    } elseif (-not [string]::IsNullOrWhiteSpace($CurrentValue)) {
        $SecureValue = ConvertTo-SecureString $CurrentValue -AsPlainText -Force
    } elseif (Test-Path -LiteralPath $SecretPath -PathType Leaf) {
        try {
            $Existing = Import-ArthurSecret -ProjectRoot $ProjectRoot -EnvironmentName $EnvironmentName -SavedOnly
            $SecureValue = ConvertTo-SecureString $Existing -AsPlainText -Force
        } catch {
            Write-Warning "Saved $EnvironmentName could not be read; enter only this key again."
            $SecureValue = Read-Host $Prompt -AsSecureString
        }
    } else {
        $SecureValue = Read-Host $Prompt -AsSecureString
    }
    Save-ArthurSecretValue -ProjectRoot $ProjectRoot -EnvironmentName $EnvironmentName -SecureValue $SecureValue
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "pyproject.toml") -PathType Leaf)) {
    throw "This installer must remain in the GoalAnalysis scripts directory."
}
foreach ($Pair in @(
    @{Source = "config\daily223-live.example.json"; Target = "config\daily223-live.json"},
    @{Source = "config\arthur-settings.example.json"; Target = "config\arthur-settings.json"}
)) {
    $TargetPath = Join-Path $ProjectRoot $Pair.Target
    if (-not (Test-Path -LiteralPath $TargetPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot $Pair.Source) -Destination $TargetPath
        Write-Host ("Created " + $Pair.Target + " from the example.")
    } else {
        Write-Host ("Preserved your existing " + $Pair.Target + ".")
    }
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if (-not $Launcher) {
        throw "Python environment missing. Install Python 3.11 or newer with the py launcher, then run this installer again."
    }
    & $Launcher.Source -3 -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the Python environment. Installation stopped."
    }
}
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw "Arthur needs Python 3.11 or newer. The existing environment was preserved."
}
Write-Host "Installing project dependencies into the project Python environment."
& $Python -m pip install -e ".[dev,models]"
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Installation stopped."
}
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Project tests failed. No new schedule was registered."
}

Save-ArthurSecret "THE_STATS_API_KEY" "TheStats API key (hidden input)"
& (Join-Path $PSScriptRoot "configure-arthur-codex.ps1") -SkipCheck

& $Python "scripts\run-arthur.py" --config "config\daily223-live.json" --settings "config\arthur-settings.json" --check-config
if ($LASTEXITCODE -ne 0) {
    throw "Configuration check failed. No new schedule was registered."
}
Write-Host "Checking Astra through Codex and your ChatGPT plan (uses included Codex allowance)."
& $Python "scripts\run-arthur.py" --config "config\daily223-live.json" --settings "config\arthur-settings.json" --check-openai
if ($LASTEXITCODE -ne 0) {
    throw "Codex/ChatGPT access check failed. Sports keys are saved; no new schedule was registered. See the error above."
}

$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$Starter = Join-Path $PSScriptRoot "start-arthur.ps1"
& (Join-Path $PSScriptRoot "register-arthur-schedule.ps1") -ProjectRoot $ProjectRoot -WakeToRun:$WakeToRun

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Arthur Goal Analysis.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShell
$Shortcut.Arguments = "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$Starter`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Arthur - daily tickets, Astra analysis and results"
$Shortcut.Save()

Write-Host "Arthur setup completed; the Codex/ChatGPT model check passed."
Write-Host "Desktop shortcut: $ShortcutPath"
Write-Host "Nightly run: 22:00 Europe/Berlin, including daylight saving time; recovery until 06:00."
Write-Host "The PC must be on, connected to the internet, and this Windows user logged in."
if ($WakeToRun) { Write-Host "Wake timers enabled for 15-minute checks, subject to Windows power policy; a logged-in session is still required." }
else { Write-Host "No cloud service or automatic wake-up was installed." }
if (-not $NoLaunch) {
    Start-Process -FilePath $PowerShell -ArgumentList $Shortcut.Arguments -WorkingDirectory $ProjectRoot
}
