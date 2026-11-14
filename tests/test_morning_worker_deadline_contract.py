"""The fixed morning wrapper must outlive the same worker used by manual launch."""
import ast
import re
from pathlib import Path


def test_morning_limit_covers_business_verifier_and_finish_without_old_timer_activation():
    root = Path(__file__).resolve().parents[1]
    task = (root / 'scripts/arthur-workshop-schedule.ps1').read_text('utf-8-sig')
    worker = ast.parse((root / 'scripts/run-arthur-ten-job.py').read_text('utf-8-sig'))
    main = next(n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    durations = {kw.value.value for n in ast.walk(main) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'supervise'
                 for kw in n.keywords if kw.arg == 'seconds' and isinstance(kw.value, ast.Constant)}
    declared = re.search(r"ExecutionTimeLimit = 'PT(\d+)M'", task)
    assert declared is not None and durations == {7200, 180}
    minutes = int(declared.group(1))
    assert sum(durations) + 300 <= minutes * 60 <= 9000
    assert f"ExecutionTimeLimit -cne 'PT{minutes}M'" in task
    assert "$TaskName = 'Arthur Ten Agents Morning'" in task
    assert '$Definition.Settings.StartWhenAvailable = $false' in task
    assert '$Definition.Settings.RestartCount = 0' in task
    assert '$Definition.Settings.WakeToRun = $false' in task
    assert "'Arthur Goal Analysis Daily','Arthur Goal Analysis Daily 223'" in task
    assert "$Found[0].State -ne 'Disabled'" in task
    assert '$Registered.Run(' not in task and 'Enable-ScheduledTask' not in task
