<#
.SYNOPSIS
Register Arthur's native Windows timer; no credentials or API/model calls.
.DESCRIPTION
The UTC-aligned one-minute timer and current-user logon trigger invoke a cheap
Python gate. Python alone decides whether Europe/Berlin 22:00 is due, retries
unfinished stages before 06:00, and prevents duplicate/backfilled old runs.
Windows scheduling is best effort, not a real-time guarantee. The same user
must remain logged in. By default the PC must be awake; -WakeToRun permits
wake timers (subject to Windows power policy) every minute, including daytime.
No password is stored. A sleeping/offline PC may miss the overnight window.
Only the exact Arthur task and recognized DAILY_223 predecessors in the root
task folder are touched. Existing definitions are backed up before changes.
.PARAMETER CheckOnly
Read and validate existing definitions; report the proposal without any writes.
.PARAMETER DefinitionOnly
Print the proposed XML without querying or changing registered tasks or files.
.EXAMPLE
.\scripts\register-arthur-schedule.ps1 -CheckOnly
.EXAMPLE
.\scripts\register-arthur-schedule.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [switch]$CheckOnly,
    [switch]$DefinitionOnly,
    [switch]$WakeToRun
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 may not populate PSScriptRoot while evaluating param
# defaults under -File. Resolve it only after parameter binding has completed.
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

function New-ArthurScheduleXml {
    param([string]$Root, [string]$UserSid, [bool]$Wake = $false)
    $PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $Root 'scripts\run-arthur-scheduled.ps1')`""
    $EscapedCommand = [Security.SecurityElement]::Escape($PowerShell)
    $EscapedArguments = [Security.SecurityElement]::Escape($Arguments)
    $EscapedRoot = [Security.SecurityElement]::Escape($Root)
    $EscapedSid = [Security.SecurityElement]::Escape($UserSid)
    # Explicit UTC + indefinite repetition avoids any host-timezone/DST dependency.
    $Boundary = [DateTime]::UtcNow.Date.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
    $WakeValue = $Wake.ToString().ToLowerInvariant()
    return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Arthur nightly: Europe/Berlin 22:00; safe UTC timer, current-user session required.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>$Boundary</StartBoundary><Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>$EscapedSid</UserId></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>$EscapedSid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>$WakeValue</WakeToRun><ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author"><Exec><Command>$EscapedCommand</Command><Arguments>$EscapedArguments</Arguments><WorkingDirectory>$EscapedRoot</WorkingDirectory></Exec></Actions>
</Task>
"@
}

function Test-ArthurTaskOwnership {
    param($Task, [string]$Root, [string]$Runner)
    $Actions = @($Task.Actions)
    if ($Actions.Count -ne 1) { return $false }
    $Action = $Actions[0]
    $PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if ($Action.Execute -notin @($PowerShell, "powershell.exe")) { return $false }
    if ($Action.WorkingDirectory -and $Action.WorkingDirectory.TrimEnd('\') -ne $Root.TrimEnd('\')) { return $false }
    $RunnerPath = Join-Path $Root "scripts\$Runner"
    $Allowed = @(
        "-NoProfile -ExecutionPolicy Bypass -File `"$RunnerPath`"",
        "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RunnerPath`"",
        "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerPath`""
    )
    return $Action.Arguments -in $Allowed
}

function Resolve-ArthurUserSid {
    param([string]$Identity)
    if ([string]::IsNullOrWhiteSpace($Identity)) { return $null }
    try {
        if ($Identity -match '^S-1-') { return [Security.Principal.SecurityIdentifier]::new($Identity).Value }
        return [Security.Principal.NTAccount]::new($Identity).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch { return $null }
}

function Get-ArthurDefinitionIssue {
    param([xml]$Actual, [xml]$Expected, [string]$UserSid)
    # Task Scheduler may canonicalize account names/SIDs, UTC Z/+00:00, and
    # omit false/default booleans. Compare semantics, not its serialized spelling.
    if (@($Actual.Task.Triggers.ChildNodes).Count -ne 2 -or
        -not $Actual.Task.Triggers.TimeTrigger -or -not $Actual.Task.Triggers.LogonTrigger) { return 'TRIGGER_COUNT' }
    $Timer = $Actual.Task.Triggers.TimeTrigger
    if ($Timer.Repetition.Interval -ne 'PT1M' -or $Timer.Repetition.Duration) { return 'REPETITION' }
    $Boundary = [string]$Timer.StartBoundary
    if ($Boundary -notmatch '(Z|[+-][0-9]{2}:[0-9]{2})$') { return 'UTC_BOUNDARY' }
    try {
        if ([DateTimeOffset]::Parse($Boundary) -ne [DateTimeOffset]::Parse([string]$Expected.Task.Triggers.TimeTrigger.StartBoundary)) { return 'START_BOUNDARY' }
    } catch { return 'START_BOUNDARY' }
    foreach ($Trigger in @($Timer, $Actual.Task.Triggers.LogonTrigger)) {
        if ($Trigger.Enabled -and $Trigger.Enabled -notin @('true', '1')) { return 'TRIGGER_DISABLED' }
    }
    if ((Resolve-ArthurUserSid ([string]$Actual.Task.Triggers.LogonTrigger.UserId)) -ne $UserSid -or
        (Resolve-ArthurUserSid ([string]$Actual.Task.Principals.Principal.UserId)) -ne $UserSid -or
        $Actual.Task.Principals.Principal.LogonType -ne 'InteractiveToken') { return 'PRINCIPAL' }
    if ($Actual.Task.Principals.Principal.RunLevel -and $Actual.Task.Principals.Principal.RunLevel -ne 'LeastPrivilege') { return 'RUN_LEVEL' }
    foreach ($Setting in @('MultipleInstancesPolicy', 'ExecutionTimeLimit')) {
        if ($Actual.Task.Settings.$Setting -ne $Expected.Task.Settings.$Setting) { return ('SETTING_' + $Setting.ToUpperInvariant()) }
    }
    foreach ($Setting in @('StartWhenAvailable', 'WakeToRun', 'RunOnlyIfNetworkAvailable')) {
        $ActualValue = if ($Actual.Task.Settings.$Setting) { [string]$Actual.Task.Settings.$Setting } else { 'false' }
        try {
            if ([Xml.XmlConvert]::ToBoolean($ActualValue.ToLowerInvariant()) -ne [Xml.XmlConvert]::ToBoolean([string]$Expected.Task.Settings.$Setting)) { return ('SETTING_' + $Setting.ToUpperInvariant()) }
        } catch { return ('SETTING_' + $Setting.ToUpperInvariant()) }
    }
    if ($Actual.Task.Settings.Enabled -and $Actual.Task.Settings.Enabled -notin @('true', '1')) { return 'TASK_DISABLED' }
    return $null
}

function Get-ArthurScheduleFailure {
    param([string]$Stage, [Management.Automation.ErrorRecord]$Failure)
    # Never use ErrorRecord.ToString/Exception.Message/FullyQualifiedErrorId:
    # for a script throw these may contain arbitrary paths, arguments or secrets.
    $TypeName = $Failure.Exception.GetType().FullName
    if ($TypeName -notmatch '^[A-Za-z0-9_.+]+$') { $TypeName = 'UNKNOWN' }
    return @{
        failure_stage = $Stage; error_id = $TypeName
        hresult = ('0x{0:X8}' -f $Failure.Exception.HResult)
    }
}

function Write-ArthurScheduleStatus {
    param([string]$Root, [string]$Status, [Nullable[bool]]$Enabled, [bool]$Wake, [hashtable]$FailureDetails = @{})
    $Directory = Join-Path $Root "data\arthur"
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Path = Join-Path $Directory "schedule-status.json"
    $Temporary = Join-Path $Directory (([guid]::NewGuid().ToString('N')) + '.tmp')
    $Previous = Join-Path $Directory (([guid]::NewGuid().ToString('N')) + '.bak')
    $Payload = [ordered]@{
        status = $Status; task_name = "Arthur Goal Analysis Daily"
        verified_at = [DateTimeOffset]::UtcNow.ToString("o"); enabled = $Enabled
        heartbeat_minutes = 1; local_check_seconds = 60; provider_refresh_seconds = 1800
        requires_logged_in_user = $true; wake_to_run = $Wake
        time_zone = "Europe/Berlin"; daily_time = "22:00"; catchup_cutoff = "06:00"
    }
    foreach ($Key in @('failure_stage', 'error_id', 'hresult', 'trigger_boundary')) {
        if ($FailureDetails.ContainsKey($Key)) { $Payload[$Key] = $FailureDetails[$Key] }
    }
    $Payload = $Payload | ConvertTo-Json
    try {
        [IO.File]::WriteAllText($Temporary, $Payload, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($Temporary, $Path, $Previous) }
        else { [IO.File]::Move($Temporary, $Path) }
    } finally {
        if (Test-Path -LiteralPath $Temporary) { Remove-Item -LiteralPath $Temporary -Force }
        if (Test-Path -LiteralPath $Previous) { Remove-Item -LiteralPath $Previous -Force }
    }
}

function Invoke-ArthurScheduleRegistration {
    param([string]$Root, [switch]$InspectOnly, [switch]$XmlOnly, [switch]$Wake)
    $Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $Sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $Xml = New-ArthurScheduleXml -Root $Root -UserSid $Sid -Wake $Wake.IsPresent
    if ($XmlOnly) { return $Xml }
    $TaskName = "Arthur Goal Analysis Daily"
    $LegacyNames = @("Arthur Goal Analysis Daily 223", "Arthur Goal Analysis Daily223")
    $Backups = @{}
    $Changed = [Collections.Generic.List[string]]::new()
    $FailureStage = 'VALIDATE_FILES'
    try {
        foreach ($RelativePath in @('.venv\Scripts\python.exe', 'scripts\run-arthur-scheduled.ps1', 'scripts\run-arthur-nightly.py')) {
            if (-not (Test-Path -LiteralPath (Join-Path $Root $RelativePath) -PathType Leaf)) { throw "Arthur launch files are missing." }
        }
        # Read the exact root namespace once; never wildcard-delete/replace tasks.
        $FailureStage = 'READ_CURRENT_TASKS'
        $Existing = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object { $_.TaskName -in (@($TaskName) + $LegacyNames) })
        $FailureStage = 'VALIDATE_OWNERSHIP_AND_IDLE'
        foreach ($Task in $Existing) {
            $Runner = if ($Task.TaskName -eq $TaskName) { 'run-arthur-scheduled.ps1' } else { 'run-daily223-scheduled.ps1' }
            if (-not (Test-ArthurTaskOwnership -Task $Task -Root $Root -Runner $Runner)) { throw "An Arthur-named task has a custom/unrelated action. No tasks were changed." }
            if ($Task.State -eq 'Running') { throw "An Arthur task is running. Retry registration after it finishes." }
        }
        # COM parses the native schema but this in-memory definition is never saved.
        $FailureStage = 'VALIDATE_NATIVE_SCHEMA'
        $Service = New-Object -ComObject 'Schedule.Service'
        $Service.Connect()
        $Definition = $Service.NewTask(0)
        $Definition.XmlText = $Xml
        if ($InspectOnly) {
            return ([ordered]@{ status = 'DRY_RUN'; task_name = $TaskName; time_zone = 'Europe/Berlin'; daily_time = '22:00'; heartbeat_minutes = 1; local_check_seconds = 60; provider_refresh_seconds = 1800; requires_logged_in_user = $true; wake_to_run = $Wake.IsPresent; existing_tasks = @($Existing | ForEach-Object { $_.TaskName }) } | ConvertTo-Json)
        }
        $FailureStage = 'BACKUP_ORIGINAL_DEFINITIONS'
        $BackupDirectory = Join-Path $Root ('data\arthur\schedule-backups\' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
        foreach ($Task in $Existing) {
            $OldXml = Export-ScheduledTask -TaskName $Task.TaskName -TaskPath '\' -ErrorAction Stop
            [IO.File]::WriteAllText((Join-Path $BackupDirectory ($Task.TaskName + '.xml')), $OldXml, [Text.Encoding]::Unicode)
            $Backups[$Task.TaskName] = $OldXml
        }
        # Legacy jobs are disabled before the new enabled definition can trigger.
        $FailureStage = 'DISABLE_LEGACY'
        foreach ($Task in $Existing) {
            if ($Task.TaskName -in $LegacyNames -and $Task.State -ne 'Disabled') {
                $Changed.Add($Task.TaskName)
                Disable-ScheduledTask -TaskName $Task.TaskName -TaskPath '\' -ErrorAction Stop | Out-Null
            }
        }
        $Changed.Add($TaskName)
        $FailureStage = 'REGISTER_TASK'
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $Xml -Force -ErrorAction Stop | Out-Null
        $FailureStage = 'READ_REGISTERED_TASK'
        $Registered = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop
        [xml]$ReadBack = Export-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction Stop
        [xml]$Expected = $Xml
        $FailureStage = 'VERIFY_REGISTERED_ACTION'
        if ($Registered.State -eq 'Disabled' -or -not (Test-ArthurTaskOwnership -Task $Registered -Root $Root -Runner 'run-arthur-scheduled.ps1')) { throw "Registered action verification failed." }
        $FailureStage = 'VERIFY_REGISTERED_DEFINITION'
        $Issue = Get-ArthurDefinitionIssue -Actual $ReadBack -Expected $Expected -UserSid $Sid
        if ($Issue) { $FailureStage = 'VERIFY_' + $Issue; throw "Registered definition verification failed." }
        $FailureStage = 'WRITE_STATUS'
        Write-ArthurScheduleStatus -Root $Root -Status 'REGISTERED' -Enabled $true -Wake $Wake.IsPresent
        Write-Output "Arthur Windows schedule registered: Europe/Berlin 22:00; one-minute local checks, thirty-minute price cache and logon recovery, cutoff 06:00."
        Write-Output "The same Windows user must be logged in. Existing task XML backups were preserved under data\arthur\schedule-backups."
    } catch {
        $SafeFailure = Get-ArthurScheduleFailure -Stage $FailureStage -Failure $_
        if ($ReadBack) {
            $ObservedBoundary = [string]$ReadBack.Task.Triggers.TimeTrigger.StartBoundary
            if ($ObservedBoundary -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?(Z|[+-]\d{2}:\d{2})?$') {
                $SafeFailure.trigger_boundary = $ObservedBoundary
            }
        }
        # Restore definitions only for names this invocation attempted to change.
        foreach ($Name in $Changed) {
            try {
                if ($Backups.ContainsKey($Name)) { Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $Backups[$Name] -Force -ErrorAction Stop | Out-Null }
                elseif ($Name -eq $TaskName) {
                    $Partial = Get-ScheduledTask -TaskName $TaskName -TaskPath '\' -ErrorAction SilentlyContinue
                    if ($Partial -and (Test-ArthurTaskOwnership -Task $Partial -Root $Root -Runner 'run-arthur-scheduled.ps1')) { Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop }
                }
            } catch { Write-Warning "Task rollback needs manual review; preserved XML backups are available." }
        }
        if (-not $InspectOnly) {
            try { Write-ArthurScheduleStatus -Root $Root -Status 'ERROR' -Enabled $null -Wake $Wake.IsPresent -FailureDetails $SafeFailure } catch { }
        }
        # Never echo arbitrary COM/action/provider error text or credentials.
        throw ("Arthur schedule registration/validation failed; stage={0}; error_id={1}; hresult={2}. Inspect Task Scheduler and preserved backups." -f $SafeFailure.failure_stage, $SafeFailure.error_id, $SafeFailure.hresult)
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-ArthurScheduleRegistration -Root $ProjectRoot -InspectOnly:$CheckOnly -XmlOnly:$DefinitionOnly -Wake:$WakeToRun
}
