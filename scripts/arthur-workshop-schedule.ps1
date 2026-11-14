param([Parameter(Mandatory=$true)][ValidateSet('true','false')][string]$EnabledValue)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $Root = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent)).TrimEnd('\')
    $TaskName = 'Arthur Ten Agents Morning'
    $Enabled = $EnabledValue -ceq 'true'
    if ([TimeZoneInfo]::Local.Id -cne 'W. Europe Standard Time') { throw 'Berlin Windows timezone required.' }
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
    $Entry = Join-Path $Root 'scripts\run-arthur-workshop.py'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or -not (Test-Path -LiteralPath $Entry -PathType Leaf)) { throw 'Installed entry required.' }
    $Legacy = @()
    $Tasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop)
    foreach ($Name in @('Arthur Goal Analysis Daily','Arthur Goal Analysis Daily 223','Arthur Goal Analysis Daily223')) {
        $Found = @($Tasks | Where-Object { $_.TaskName -ceq $Name })
        if ($Found.Count -gt 1 -or ($Found.Count -eq 1 -and $Found[0].State -ne 'Disabled')) { throw 'Legacy tasks must remain OFF.' }
        $Legacy += @{name=$Name;present=($Found.Count -eq 1);enabled=$false}
    }
    $Service = New-Object -ComObject 'Schedule.Service'
    $Service.Connect()
    $Folder = $Service.GetFolder('\')
    $Definition = $Service.NewTask(0)
    $User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Definition.RegistrationInfo.Description = 'Arthur fixed full-day 08:00 Europe/Berlin shared durable once entry; no catchup or retries.'
    $Definition.Principal.UserId = $User
    $Definition.Principal.LogonType = 3
    $Definition.Principal.RunLevel = 0
    $Definition.Settings.Enabled = $Enabled
    $Definition.Settings.Hidden = $true
    $Definition.Settings.StartWhenAvailable = $false
    $Definition.Settings.MultipleInstances = 2
    $Definition.Settings.RestartCount = 0
    $Definition.Settings.ExecutionTimeLimit = 'PT135M'
    $Definition.Settings.DisallowStartIfOnBatteries = $false
    $Definition.Settings.StopIfGoingOnBatteries = $false
    $Definition.Settings.WakeToRun = $false
    $Definition.Settings.RunOnlyIfNetworkAvailable = $false
    $Trigger = $Definition.Triggers.Create(2)
    $Trigger.DaysInterval = 1
    $Trigger.StartBoundary = [DateTime]::Today.AddHours(8).ToString('yyyy-MM-ddTHH:mm:ss')
    $Trigger.Enabled = $true
    $Action = $Definition.Actions.Create(0)
    $Action.Path = $Python
    $Action.Arguments = '-B "' + $Entry + '" --morning'
    $Action.WorkingDirectory = $Root
    # Fixed name only. No Run() or registration test and no credential acquisition.
    $Registered = $Folder.RegisterTaskDefinition($TaskName,$Definition,6,$User,$null,3,$null)
    $Read = $Folder.GetTask($TaskName)
    $Actual = $Read.Definition
    if ($Read.Enabled -ne $Enabled -or $Actual.Triggers.Count -ne 1 -or $Actual.Actions.Count -ne 1) { throw 'Task shape changed.' }
    $T = $Actual.Triggers.Item(1)
    $A = $Actual.Actions.Item(1)
    if ($T.Type -ne 2 -or $T.DaysInterval -ne 1 -or $T.StartBoundary -cne $Trigger.StartBoundary -or -not $T.Enabled -or $T.EndBoundary -or $T.Repetition.Interval) { throw 'Trigger changed.' }
    if ($A.Type -ne 0 -or $A.Path -cne $Python -or $A.Arguments -cne $Action.Arguments -or $A.WorkingDirectory -cne $Root) { throw 'Action changed.' }
    if ($Actual.Principal.UserId -ine $User -or $Actual.Principal.LogonType -ne 3 -or $Actual.Principal.RunLevel -ne 0) { throw 'Principal changed.' }
    $S = $Actual.Settings
    if ($S.StartWhenAvailable -or $S.MultipleInstances -ne 2 -or $S.RestartCount -ne 0 -or $S.ExecutionTimeLimit -cne 'PT135M' -or $S.Enabled -ne $Enabled -or $S.WakeToRun -or $S.RunOnlyIfNetworkAvailable) { throw 'Task settings changed.' }
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try { $Hash = [BitConverter]::ToString($Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Actual.XmlText))).Replace('-','').ToLowerInvariant() } finally { $Hasher.Dispose() }
    @{task_name=$TaskName;enabled=$Enabled;timezone='Europe/Berlin';required_session='INTERACTIVE_USER_LOGGED_IN';wake_to_run=$false;no_catchup=$true;windows_timezone=[TimeZoneInfo]::Local.Id;local_time='08:00';latest_start_local_time='08:15';observation_status='VERIFIED_FIXED_DEFINITION';legacy_tasks=$Legacy;definition_sha256=$Hash;observed_at=[DateTime]::UtcNow.ToString('o');actual_task_execution_performed=$false} | ConvertTo-Json -Depth 5 -Compress
} catch {
    Write-Error 'Fixed Arthur morning task change/readback failed; outcome requires reconciliation.' -ErrorAction Continue
    exit 1
}
