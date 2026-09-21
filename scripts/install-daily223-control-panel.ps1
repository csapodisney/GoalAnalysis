$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Starter = Join-Path $ProjectRoot "scripts\start-daily223-dashboard.ps1"
$ScheduledRunner = Join-Path $ProjectRoot "scripts\run-daily223-scheduled.ps1"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "A Python-környezet nem található: $Python"
}

function Save-GoalAnalysisSecret {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][string]$Prompt
    )
    $SecretDirectory = Join-Path $ProjectRoot "data\secrets"
    $SecretPath = Join-Path $SecretDirectory "$EnvironmentName.txt"
    New-Item -ItemType Directory -Path $SecretDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $SecretPath) {
        return
    }
    $Current = [Environment]::GetEnvironmentVariable($EnvironmentName, "Process")
    if ([string]::IsNullOrWhiteSpace($Current)) {
        $SecureValue = Read-Host $Prompt -AsSecureString
    } else {
        $SecureValue = ConvertTo-SecureString $Current -AsPlainText -Force
    }
    $SecureValue | ConvertFrom-SecureString | Set-Content -LiteralPath $SecretPath
}

Save-GoalAnalysisSecret -EnvironmentName "API_FOOTBALL_KEY" -Prompt "API-Football kulcs"
Save-GoalAnalysisSecret -EnvironmentName "THE_ODDS_API_KEY" -Prompt "The Odds API kulcs"

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Arthur Goal Analysis.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Starter`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.Description = "Arthur Goal Analysis vezérlőpult"
$Shortcut.Save()

$ActionArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScheduledRunner`""
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ActionArguments
$Trigger = New-ScheduledTaskTrigger -Daily -At "07:30"
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName "Arthur Goal Analysis Daily 223" `
    -Description "Napi Goal Analysis futás 07:30-kor" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Force | Out-Null

Write-Host "A vezérlőpult parancsikonja elkészült: $ShortcutPath"
Write-Host "A napi feladat elkészült: 07:30"
Start-Process -FilePath "powershell.exe" -ArgumentList $Shortcut.Arguments -WorkingDirectory $ProjectRoot
