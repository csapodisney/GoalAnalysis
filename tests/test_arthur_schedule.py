"""Native Windows task definitions; no test registers a real scheduled task."""

import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).parents[1]
REGISTER = ROOT / "scripts/register-arthur-schedule.ps1"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Native Windows PowerShell/Task Scheduler")


def _ps(script, *args):
    env = os.environ.copy()
    # PowerShell 7's inherited module paths can shadow Windows PowerShell 5.1 modules.
    env.pop("PSModulePath", None)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        env=env, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@WINDOWS
@pytest.mark.parametrize("name", [
    "run-arthur-scheduled.ps1", "register-arthur-schedule.ps1", "install-arthur.ps1",
])
def test_powershell_syntax(tmp_path, name):
    runner = tmp_path / "parse.ps1"
    runner.write_text(r'''
param([string]$Target)
$ErrorActionPreference = 'Stop'
$Tokens = $null; $Errors = $null
$null = [Management.Automation.Language.Parser]::ParseFile($Target, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count) { throw ($Errors | Out-String) }
Write-Output 'PASS'
''', encoding="utf-8")
    assert _ps(runner, ROOT / "scripts" / name) == "PASS"


@WINDOWS
@pytest.mark.parametrize("wake", [False, True])
def test_task_xml_native_schema_and_safe_settings(tmp_path, wake):
    test_root = tmp_path / 'Goal & Analysis'
    args = ["-ProjectRoot", test_root, "-DefinitionOnly"]
    if wake:
        args.append("-WakeToRun")
    xml = _ps(REGISTER, *args)
    task = ET.fromstring(xml)
    assert not test_root.exists()  # Definition generation has no file writes.
    find = lambda path: task.findtext(path, namespaces=NS)
    assert find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT1M"
    assert task.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Duration", NS) is None
    assert find("t:Triggers/t:TimeTrigger/t:StartBoundary").endswith("T00:00:00Z")
    assert find("t:Triggers/t:LogonTrigger/t:UserId") == find("t:Principals/t:Principal/t:UserId")
    assert find("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert find("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    for key, expected in {
        "MultipleInstancesPolicy": "IgnoreNew", "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT2H", "WakeToRun": str(wake).lower(),
        "RunOnlyIfNetworkAvailable": "false", "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false", "RunOnlyIfIdle": "false", "Enabled": "true",
    }.items():
        assert find(f"t:Settings/t:{key}") == expected
    assert find("t:Actions/t:Exec/t:WorkingDirectory") == str(test_root)
    assert find("t:Actions/t:Exec/t:Arguments") == (
        '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File '
        f'"{test_root}\\scripts\\run-arthur-scheduled.ps1"'
    )
    # The real Windows COM parser validates task XSD without saving/registering it.
    validator = tmp_path / "schema.ps1"
    validator.write_text(r'''
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
. $Source
$Xml = New-ArthurScheduleXml -Root $Root -UserSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
$Service = New-Object -ComObject 'Schedule.Service'
$Service.Connect()
$Definition = $Service.NewTask(0)
$Definition.XmlText = $Xml
if ($Definition.Triggers.Count -ne 2) { throw 'Trigger count' }
Write-Output 'PASS'
''', encoding="utf-8")
    assert _ps(validator, REGISTER, test_root) == "PASS"


@WINDOWS
def test_native_powershell_file_resolves_default_project_root():
    # Exercise the actual powershell.exe 5.1 -File entrypoint, with no explicit
    # -ProjectRoot: param defaults must not read an uninitialized PSScriptRoot.
    task = ET.fromstring(_ps(REGISTER, "-DefinitionOnly"))
    assert task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == str(ROOT)
    assert str(ROOT / "scripts/run-arthur-scheduled.ps1") in task.findtext(
        "t:Actions/t:Exec/t:Arguments", namespaces=NS
    )


@pytest.mark.parametrize("date,utc_hour", [
    ("2026-03-28", 21), ("2026-03-29", 20),
    ("2026-10-24", 20), ("2026-10-25", 21),
])
def test_utc_minute_grid_includes_berlin_22_across_dst(date, utc_hour):
    local_due = datetime.fromisoformat(date + "T22:00:00").replace(tzinfo=ZoneInfo("Europe/Berlin"))
    due = local_due.astimezone(UTC)
    assert due.hour == utc_hour
    for host in ("America/New_York", "Asia/Kathmandu", "Pacific/Chatham", "UTC"):
        host_now = due.astimezone(ZoneInfo(host))
        midnight = due.replace(hour=0, minute=0, second=0, microsecond=0)
        ticks = {midnight + timedelta(minutes=step) for step in range(1440)}
        assert host_now.astimezone(UTC) in ticks


@WINDOWS
def test_ownership_guard_and_atomic_status_metadata(tmp_path):
    script = tmp_path / "ownership.ps1"
    script.write_text(r'''
param([string]$Source, [string]$Root)
$ErrorActionPreference = 'Stop'
. $Source
$Path = Join-Path $Root 'scripts\run-arthur-scheduled.ps1'
$GoodArgs = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Path`""
$Task = [pscustomobject]@{ Actions = @([pscustomobject]@{ Execute = 'powershell.exe'; WorkingDirectory = $Root; Arguments = $GoodArgs }) }
if (-not (Test-ArthurTaskOwnership $Task $Root 'run-arthur-scheduled.ps1')) { throw 'Legacy Arthur refused' }
$Task.Actions[0].Arguments += ' -Command Get-Process'
if (Test-ArthurTaskOwnership $Task $Root 'run-arthur-scheduled.ps1') { throw 'Extra action accepted' }
$Task.Actions[0].Arguments = $GoodArgs.Replace($Root, ($Root + '-other'))
if (Test-ArthurTaskOwnership $Task $Root 'run-arthur-scheduled.ps1') { throw 'Other project accepted' }
$Task.Actions[0].Arguments = $GoodArgs
$Task.Actions[0].Execute = 'cmd.exe'
if (Test-ArthurTaskOwnership $Task $Root 'run-arthur-scheduled.ps1') { throw 'Other executable accepted' }
$Task.Actions[0].Execute = 'powershell.exe'
$Task.Actions += $Task.Actions[0]
if (Test-ArthurTaskOwnership $Task $Root 'run-arthur-scheduled.ps1') { throw 'Multiple actions accepted' }
Write-ArthurScheduleStatus -Root $Root -Status 'ERROR' -Enabled $false -Wake $false
Write-ArthurScheduleStatus -Root $Root -Status 'REGISTERED' -Enabled $true -Wake $false
Get-Content -Raw -LiteralPath (Join-Path $Root 'data\arthur\schedule-status.json')
''', encoding="utf-8")
    status = json.loads(_ps(script, REGISTER, tmp_path / "isolated"))
    assert status["status"] == "REGISTERED"
    assert status["task_name"] == "Arthur Goal Analysis Daily"
    assert status["enabled"] is True
    assert status["heartbeat_minutes"] == 1
    assert status["local_check_seconds"] == 60
    assert status["provider_refresh_seconds"] == 1800
    assert status["requires_logged_in_user"] is True
    assert datetime.fromisoformat(status["verified_at"]).tzinfo is not None
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.bak"))


def test_installer_delegates_schedule_and_wrapper_separates_odds_from_nightly_gate():
    installer = (ROOT / "scripts/install-arthur.ps1").read_text(encoding="utf-8-sig")
    wrapper = (ROOT / "scripts/run-arthur-scheduled.ps1").read_text(encoding="utf-8-sig")
    assert 'register-arthur-schedule.ps1' in installer
    assert 'New-ScheduledTaskTrigger' not in installer
    assert 'Register-ScheduledTask' not in installer
    assert 'DailyTime = "22:00"' in installer
    assert 'run-arthur-nightly.py' in wrapper
    assert 'run-arthur-odds-monitor.py' in wrapper
    assert '--scheduled' in wrapper and '--check-schedule' in wrapper
    assert '--live' not in wrapper and '--date' not in wrapper
    assert 'OPENAI_API_KEY' not in wrapper
    assert '-SavedOnly' in wrapper
    assert '$_.Exception' not in wrapper
    assert wrapper.index('if ($CheckOnly)') < wrapper.index('arthur-secrets.ps1')


@WINDOWS
@pytest.mark.parametrize("scenario", ["success", "dry", "custom", "running", "register_failure", "disable_failure"])
def test_registration_scoped_backup_dry_run_and_rollback(tmp_path, scenario):
    """Mock every scheduler read/write command; only COM schema parsing is native."""
    root = tmp_path / "isolated"
    for relative in (".venv/Scripts/python.exe", "scripts/run-arthur-scheduled.ps1", "scripts/run-arthur-nightly.py"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    script = tmp_path / "mock-scheduler.ps1"
    script.write_text(r'''
param([string]$Source, [string]$Root, [string]$Scenario)
$ErrorActionPreference = 'Stop'
. $Source
$Main = 'Arthur Goal Analysis Daily'; $Legacy = 'Arthur Goal Analysis Daily 223'
$Script:Events = [Collections.Generic.List[string]]::new()
$Script:Tasks = @{}
$Script:Xmls = @{}
$Script:FailedOnce = $false
foreach ($Name in @($Main, $Legacy)) {
    $Runner = if ($Name -eq $Main) { 'run-arthur-scheduled.ps1' } else { 'run-daily223-scheduled.ps1' }
    $Args = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$(Join-Path $Root "scripts\$Runner")`""
    $Script:Tasks[$Name] = [pscustomobject]@{ TaskName = $Name; State = 'Ready'; Actions = @([pscustomobject]@{ Execute = 'powershell.exe'; WorkingDirectory = $Root; Arguments = $Args }) }
    $Script:Xmls[$Name] = New-ArthurScheduleXml -Root $Root -UserSid ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
}
$OriginalMainXml = $Script:Xmls[$Main]
$OriginalLegacyXml = $Script:Xmls[$Legacy]
$Unrelated = [pscustomobject]@{ TaskName = 'Arthur Goal Analysis Daily custom'; State = 'Ready'; Actions = @() }
$Script:Tasks[$Unrelated.TaskName] = $Unrelated
if ($Scenario -eq 'custom') { $Script:Tasks[$Main].Actions[0].Arguments += ' -OtherWork' }
if ($Scenario -eq 'running') { $Script:Tasks[$Main].State = 'Running' }

function Get-ScheduledTask {
    [CmdletBinding()]param([string]$TaskName, [string]$TaskPath)
    if ($TaskPath -ne '\') { throw 'Unexpected namespace' }
    if ($TaskName) { return $Script:Tasks[$TaskName] }
    return @($Script:Tasks.Values)
}
function Export-ScheduledTask {
    [CmdletBinding()]param([string]$TaskName, [string]$TaskPath)
    if ($TaskPath -ne '\' -or $TaskName -notin @($Main, $Legacy)) { throw 'Out-of-scope export' }
    return $Script:Xmls[$TaskName]
}
function Disable-ScheduledTask {
    [CmdletBinding()]param([string]$TaskName, [string]$TaskPath)
    if ($TaskName -ne $Legacy -or $TaskPath -ne '\') { throw 'Out-of-scope disable' }
    if (@(Get-ChildItem -LiteralPath (Join-Path $Root 'data\arthur\schedule-backups') -Recurse -Filter '*.xml').Count -ne 2) { throw 'Backup not made before disable' }
    $Script:Events.Add('disable:' + $TaskName)
    if ($Scenario -eq 'disable_failure') { throw 'Synthetic secret DO_NOT_LOG' }
    $Script:Tasks[$TaskName].State = 'Disabled'
}
function Register-ScheduledTask {
    [CmdletBinding()]param([string]$TaskName, [string]$TaskPath, [string]$Xml, [switch]$Force)
    if ($TaskName -notin @($Main, $Legacy) -or $TaskPath -ne '\') { throw 'Out-of-scope register' }
    $Script:Events.Add('register:' + $TaskName)
    if ($Scenario -eq 'register_failure' -and -not $Script:FailedOnce) { $Script:FailedOnce = $true; throw 'Synthetic secret DO_NOT_LOG' }
    $Script:Xmls[$TaskName] = $Xml
    $Script:Tasks[$TaskName].State = 'Ready'
}
function Unregister-ScheduledTask { throw 'Unregister should not be called when originals exist' }
$Failed = $false
try { $Output = Invoke-ArthurScheduleRegistration -Root $Root -InspectOnly:($Scenario -eq 'dry') }
catch { $Failed = $true; if ($_.Exception.Message.Contains('DO_NOT_LOG')) { throw 'Raw error leaked' } }
if ($Scenario -eq 'dry') {
    if ($Failed -or $Script:Events.Count -or (Test-Path -LiteralPath (Join-Path $Root 'data'))) { throw 'Dry run wrote state' }
    if (($Output | ConvertFrom-Json).status -ne 'DRY_RUN') { throw 'Missing dry status' }
} elseif ($Scenario -eq 'success') {
    if ($Failed -or $Script:Events.Count -ne 2 -or $Script:Tasks[$Legacy].State -ne 'Disabled') { throw 'Registration not completed' }
    if ((Get-Content -Raw (Join-Path $Root 'data\arthur\schedule-status.json') | ConvertFrom-Json).status -ne 'REGISTERED') { throw 'Missing success status' }
} else {
    if (-not $Failed) { throw 'Unsafe operation did not fail' }
    if ($Scenario -in @('custom', 'running') -and $Script:Events.Count) { throw 'Rejected task was mutated' }
    if ($Scenario -in @('register_failure', 'disable_failure')) {
        if ($Script:Tasks[$Legacy].State -ne 'Ready' -or $Script:Xmls[$Legacy] -cne $OriginalLegacyXml -or $Script:Xmls[$Main] -cne $OriginalMainXml) { throw 'Original task not restored' }
    }
}
if ($Script:Tasks[$Unrelated.TaskName] -ne $Unrelated) { throw 'Unrelated task changed' }
Write-Output 'PASS'
''', encoding="utf-8")
    assert _ps(script, REGISTER, root, scenario) == "PASS"
    if scenario in {"success", "register_failure", "disable_failure"}:
        assert len(list(root.rglob("*.xml"))) == 2
    if scenario not in {"success", "dry"}:
        status = json.loads((root / "data/arthur/schedule-status.json").read_text())
        assert status["status"] == "ERROR" and status["enabled"] is None
        assert status["failure_stage"]
        assert status["error_id"].startswith("System.")
        assert status["hresult"].startswith("0x")
        assert "DO_NOT_LOG" not in json.dumps(status)


@WINDOWS
@pytest.mark.parametrize("check_only,child_exit", [(True, 0), (False, 0), (False, 2)])
def test_wrapper_diagnostic_read_only_and_missing_keys_do_not_skip_python(tmp_path, check_only, child_exit):
    root = tmp_path / "isolated"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    python_dir = root / ".venv/Scripts"
    python_dir.mkdir(parents=True)
    # A copied venv redirector uses the existing Python installation; no symlinks
    # or production paths are written by the child process.
    shutil.copy2(ROOT / ".venv/Scripts/python.exe", python_dir / "python.exe")
    shutil.copy2(ROOT / ".venv/pyvenv.cfg", root / ".venv/pyvenv.cfg")
    shutil.copy2(ROOT / "scripts/run-arthur-scheduled.ps1", scripts)
    (scripts / "arthur-secrets.ps1").write_text(r'''
function Import-ArthurSecret {
    param([string]$ProjectRoot, [string]$EnvironmentName, [switch]$SavedOnly)
    if (-not $SavedOnly) { throw 'SavedOnly was omitted' }
    Add-Content -LiteralPath (Join-Path $ProjectRoot 'credential-read.txt') -Value $EnvironmentName
    throw 'SYNTHETIC_SECRET_DO_NOT_LOG'
}
''', encoding="utf-8")
    (scripts / "run-arthur-nightly.py").write_text('''
import json
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
assert len(sys.argv) == 2
if sys.argv[1] == "--check-schedule":
    assert not (root / "credential-read.txt").exists()
    print(json.dumps({"status": "CHECK_ONLY"}))
else:
    assert sys.argv[1] == "--scheduled"
    assert not os.environ.get("API_FOOTBALL_KEY")
    assert not os.environ.get("THE_ODDS_API_KEY")
    assert not os.environ.get("XAI_API_KEY")
    assert not os.environ.get("THE_STATS_API_KEY")
    assert not os.environ.get("THESTATSAPI_API_KEY")
    (root / "called.txt").write_text("scheduled")
    print("SYNTHETIC_SECRET_DO_NOT_LOG")
    print("SYNTHETIC_SECRET_DO_NOT_LOG", file=sys.stderr)
    sys.exit(int(os.environ["ARTHUR_TEST_CHILD_EXIT"]))
''', encoding="utf-8")
    harness = tmp_path / "wrapper-test.ps1"
    harness.write_text(r'''
param([string]$Wrapper, [string]$Mode, [string]$Code)
$env:API_FOOTBALL_KEY = 'synthetic-stale-inherited'
$env:THE_ODDS_API_KEY = 'synthetic-stale-inherited'
$env:XAI_API_KEY = 'synthetic-stale-inherited'
$env:THE_STATS_API_KEY = 'synthetic-stale-inherited'
$env:THESTATSAPI_API_KEY = 'synthetic-stale-inherited'
$env:ARTHUR_TEST_CHILD_EXIT = $Code
if ($Mode -eq 'check') { & $Wrapper -CheckOnly }
else { & $Wrapper }
if ($LASTEXITCODE -ne [int]$Code) { throw "Unexpected wrapper exit: $LASTEXITCODE" }
Write-Output 'PASS'
exit 0
''', encoding="utf-8")
    output = _ps(harness, scripts / "run-arthur-scheduled.ps1", "check" if check_only else "scheduled", child_exit)
    assert output.endswith("PASS")
    assert "SYNTHETIC_SECRET" not in output
    if check_only:
        assert '"CHECK_ONLY"' in output
        assert not (root / "credential-read.txt").exists()
        assert not (root / "called.txt").exists()
    else:
        assert (root / "called.txt").read_text() == "scheduled"
        assert set((root / "credential-read.txt").read_text().splitlines()) == {
            "XAI_API_KEY", "THE_STATS_API_KEY",
        }
    if child_exit:
        log = (root / "logs/arthur-scheduler.log").read_text(encoding="utf-8-sig")
        assert "exit code 2" in log
        assert "SYNTHETIC_SECRET" not in log
    else:
        assert not (root / "logs").exists()


@WINDOWS
@pytest.mark.parametrize("scenario,expected_issue", [
    ("normalized", "PASS"), ("explicit_offset", "PASS"), ("wrong_user", "PRINCIPAL"),
    ("wrong_interval", "REPETITION"), ("local_boundary", "UTC_BOUNDARY"),
    ("disabled", "TASK_DISABLED"),
])
def test_readback_accepts_native_normalization_without_weakening_invariants(tmp_path, scenario, expected_issue):
    script = tmp_path / "normalization.ps1"
    script.write_text(r'''
param([string]$Source, [string]$Root, [string]$Scenario)
$ErrorActionPreference = 'Stop'
. $Source
$User = [Security.Principal.WindowsIdentity]::GetCurrent()
[xml]$Expected = New-ArthurScheduleXml -Root $Root -UserSid $User.User.Value
[xml]$Actual = $Expected.OuterXml
$Actual.Task.Triggers.TimeTrigger.StartBoundary = $Actual.Task.Triggers.TimeTrigger.StartBoundary.Replace('Z', '+00:00')
$Actual.Task.Triggers.LogonTrigger.UserId = $User.Name
$Actual.Task.Principals.Principal.UserId = $User.Name
foreach ($Field in @('WakeToRun', 'RunOnlyIfNetworkAvailable')) {
    $Node = $Actual.Task.Settings.SelectSingleNode("*[local-name()='$Field']")
    $null = $Actual.Task.Settings.RemoveChild($Node)
}
switch ($Scenario) {
    'explicit_offset' { $Actual.Task.Triggers.TimeTrigger.StartBoundary = ([DateTimeOffset]::Parse([string]$Actual.Task.Triggers.TimeTrigger.StartBoundary)).ToOffset([TimeSpan]::FromHours(2)).ToString('yyyy-MM-ddTHH:mm:sszzz') }
    'wrong_user' { $Actual.Task.Principals.Principal.UserId = 'S-1-5-18' }
    'wrong_interval' { $Actual.Task.Triggers.TimeTrigger.Repetition.Interval = 'PT30M' }
    'local_boundary' { $Actual.Task.Triggers.TimeTrigger.StartBoundary = $Actual.Task.Triggers.TimeTrigger.StartBoundary.Replace('+00:00', '') }
    'disabled' { $Actual.Task.Settings.Enabled = 'false' }
}
$Issue = Get-ArthurDefinitionIssue -Actual $Actual -Expected $Expected -UserSid $User.User.Value
if ($Issue) { Write-Output $Issue } else { Write-Output 'PASS' }
''', encoding="utf-8")
    assert _ps(script, REGISTER, tmp_path / "isolated", scenario) == expected_issue
