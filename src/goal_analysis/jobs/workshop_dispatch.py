"""Hidden durable worker dispatch. Only the fixed installed worker is executable."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from goal_analysis.agents.profile_source_host import source_environment
from goal_analysis.jobs.workshop_launch import LaunchStore, request_identity


class DispatchBeforeProcessError(RuntimeError):
    definitely_not_started = True


class ProductionDispatcher:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.worker = self.root / 'scripts/run-arthur-ten-job.py'
        self.python = self.root / '.venv/Scripts/python.exe'

    def __call__(self, job_id):
        request_identity(job_id)
        job = LaunchStore(self.root / 'data/arthur/workshop-launch.sqlite3').get(job_id, private=True)
        if job['state'] != 'RESERVED' or not self.worker.is_file() or not self.python.is_file():
            raise DispatchBeforeProcessError('EXACT_RESERVED_INSTALLED_WORKER_REQUIRED')
        # The standalone worker owns its child job. Dashboard death is not a retry signal.
        try:
            process = subprocess.Popen([str(self.python), '-B', str(self.worker), '--job-id', job_id],
                cwd=self.root, env=source_environment(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0x08000000 | 0x00000200, close_fds=True)
        except OSError as error:
            raise DispatchBeforeProcessError('OWNED_WORKER_CREATION_FAILED') from error
        # A PID is diagnostic only; no PID kill/reuse or claim of native success.
        return {'job_id': job_id, 'worker_pid': process.pid, 'native_exit': None}


def exclusive_json(path, value):
    import os

    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
