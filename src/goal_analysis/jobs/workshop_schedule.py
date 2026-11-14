"""Fixed new morning task adapter; no legacy mutation and no registration probe run."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from goal_analysis.agents.profile_source_host import source_environment
from goal_analysis.jobs.workshop_launch import TASK_NAME


class FixedMorningTask:
    def __init__(self, root, *, runner=subprocess.run):
        self.root = Path(root).resolve()
        self.runner = runner

    def __call__(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('BOOLEAN_TASK_STATE_REQUIRED')
        script = self.root / 'scripts/arthur-workshop-schedule.ps1'
        before = hashlib.sha256(script.read_bytes()).hexdigest()
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(script),
            '-EnabledValue', 'true' if enabled else 'false']
        completed = self.runner(command, cwd=self.root, env=source_environment(),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=45, creationflags=0x08000000)
        if (completed.returncode != 0 or len(completed.stdout) > 32768
                or len(completed.stderr) > 32768
                or hashlib.sha256(script.read_bytes()).hexdigest() != before):
            raise ValueError('FIXED_TASK_CHANGE_OR_READBACK_FAILED')
        value = json.loads(completed.stdout.decode('utf-8-sig'))
        if (value.get('task_name') != TASK_NAME or value.get('enabled') is not enabled
                or value.get('timezone') != 'Europe/Berlin'
                or value.get('local_time') != '08:00'
                or value.get('required_session') != 'INTERACTIVE_USER_LOGGED_IN'
                or value.get('wake_to_run') is not False or value.get('no_catchup') is not True
                or value.get('observation_status') != 'VERIFIED_FIXED_DEFINITION'):
            raise ValueError('EXACT_TASK_DEFINITION_READBACK_REQUIRED')
        return {**value, 'adapter_sha256': before}
