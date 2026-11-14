"""Functions-only owned phase worker guardian; no old business entry is imported."""
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

LOG_LIMIT = 16 * 1024 * 1024

def require(value, reason):
    if not value:
        raise ValueError(reason)

def resource_cancel_decision(raw, now):
    """A missing sentinel permits work; any malformed present sentinel fails closed."""
    if raw is None:
        return None
    require(type(raw) is bytes and 0 < len(raw) <= 4096, 'BOUNDED_RESOURCE_HARDSTOP_JSON_REQUIRED')
    try:
        value = json.loads(raw.decode('utf-8-sig'))
        require(isinstance(value, dict) and value.get('status') == 'HUMAN_RESOURCE_HARDSTOP_5_PERCENT',
                'EXACT_HUMAN_RESOURCE_HARDSTOP_REQUIRED')
        stamp = datetime.fromisoformat(value['requested_at_utc'])
        require(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0)
                and now.tzinfo is not None and now.utcoffset() == timedelta(0)
                and stamp <= now, 'AWARE_UTC_RESOURCE_HARDSTOP_CLOCK_REQUIRED')
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('INVALID_RESOURCE_HARDSTOP_SENTINEL') from error
    return {'requested_at_utc': value['requested_at_utc'], 'request_sha256': sha256(raw).hexdigest()}



class WindowsJob:
    """Suspend before assignment; no descendant can escape the owned job."""

    def __init__(self):
        require(os.name == 'nt', 'WINDOWS_OWNED_JOB_REQUIRED')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('active_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('r_ops', 'w_ops', 'o_ops', 'r_bytes', 'w_bytes', 'o_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.handle = self.api.CreateJobObjectW(None, None)
        require(self.handle, 'OWN_JOB_CREATE_FAILED')
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError('OWN_JOB_LIMIT_FAILED')

    def assign(self, child):
        require(self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(child._handle))), 'OWN_JOB_ASSIGN_FAILED')

    @staticmethod
    def resume(child):
        native = ctypes.WinDLL('ntdll').NtResumeProcess
        native.argtypes = [wintypes.HANDLE]
        native.restype = wintypes.LONG
        require(native(wintypes.HANDLE(int(child._handle))) == 0, 'OWN_SUSPENDED_CHILD_RESUME_FAILED')

    def active(self):
        class Accounting(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64),
                        ('period_user', ctypes.c_int64), ('period_kernel', ctypes.c_int64),
                        ('page_faults', wintypes.DWORD), ('total', wintypes.DWORD),
                        ('active', wintypes.DWORD), ('terminated', wintypes.DWORD)]
        value = Accounting()
        require(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(value), ctypes.sizeof(value), None), 'OWN_JOB_QUERY_FAILED')
        return value.active

    def terminate(self):
        require(self.api.TerminateJobObject(self.handle, 124), 'OWN_JOB_TERMINATE_FAILED')

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None



def drain(pipe, path, result):
    received, stored, hashed = 0, 0, sha256()
    try:
        with Path(path).open('xb') as stream:
            while block := pipe.read(65536):
                received += len(block)
                hashed.update(block)
                if stored < LOG_LIMIT:
                    part = block[:LOG_LIMIT - stored]
                    stream.write(part)
                    stored += len(part)
        result.update(received_bytes=received, stored_bytes=stored, received_sha256=hashed.hexdigest(), truncated=received > stored)
    except OSError as error:
        result.update(error_type=type(error).__name__, received_bytes=received, stored_bytes=stored)
    finally:
        pipe.close()



def run(command, *, cwd, folder, seconds, env, stop_path):
    require(type(seconds) is int and seconds in {600, 900}, 'FINITE_FIXED_PHASE_BOUND')
    folder = Path(folder)
    require(folder.is_dir() and not folder.resolve().is_relative_to(Path('C:/AI-Work/GoalAnalysis')),
            'EXTERNAL_NEW_OWNED_PHASE_OUTPUT')
    def stop():
        path = Path(stop_path)
        if not path.exists():
            return None
        require(path.resolve() == path and not path.is_symlink(), 'EXACT_RESOURCE_SENTINEL')
        with path.open('rb') as stream:
            raw = stream.read(4097)
        return resource_cancel_decision(raw, datetime.now(UTC))
    started, child, job, assigned, pumps = time.monotonic(), None, None, False, []
    streams = {'stdout': {}, 'stderr': {}}
    result = {'status': 'PRESPAWN_FAILURE', 'native_exit_code': None,
              'owned_worker_tree_stopped': False, 'automatic_retry': False}
    try:
        request = stop()
        if request is not None:
            result.update(status='RESOURCE_HARDSTOP_BEFORE_SPAWN', resource_hardstop=request,
                          owned_worker_tree_stopped=True)
            return result
        job = WindowsJob()
        child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=0x00000004 | 0x00000200, close_fds=True)
        job.assign(child)
        assigned = True
        result.update(pid=child.pid, owned_job_assigned_before_resume=True)
        with (folder/'child-start.json').open('x',encoding='utf-8') as stream:
            json.dump({'pid':child.pid,'argv':command,'assigned_before_resume':True,
                       'at_utc':datetime.now(UTC).isoformat()},stream)
            stream.flush()
            os.fsync(stream.fileno())
        for name, record in streams.items():
            pump = threading.Thread(target=drain,
                args=(getattr(child, name), folder / (name + '.log'), record), daemon=True)
            pump.start()
            pumps.append(pump)
        job.resume(child)
        while True:
            request = stop()
            code, active = child.poll(), job.active()
            if code is not None and active == 0:
                result.update(status='OWNED_PHASE_WORKER_STOPPED', native_exit_code=code,
                              owned_worker_tree_stopped=True)
                break
            if request is not None or time.monotonic() - started >= seconds:
                job.terminate()
                child.wait(timeout=10)
                result.update(status='RESOURCE_HARDSTOP' if request else 'PHASE_TIMEOUT',
                    native_exit_code=child.returncode, owned_worker_tree_stopped=job.active() == 0,
                    runtime_task_state='REQUIRES_FRESH_INSPECTION_NO_RETRY')
                break
            time.sleep(0.1)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(status='PHASE_STOP_REQUIRES_INSPECTION', error_type=type(error).__name__)
        if child is not None and not assigned:
            child.kill()  # Exact still-suspended Popen handle, no PID lookup.
            child.wait(timeout=10)
        if job is not None:
            job.terminate()
            if child is not None:
                child.wait(timeout=10)
            result['owned_worker_tree_stopped'] = job.active() == 0
        elif child is None:
            result['owned_worker_tree_stopped'] = True
        if child is not None:
            result['native_exit_code'] = child.returncode
    finally:
        if job is not None:
            job.close()
        for pump in pumps:
            pump.join(timeout=10)
        result.update(log_pumps_stopped=all(not p.is_alive() for p in pumps),
            streams=streams, elapsed_seconds=time.monotonic()-started,
            at_utc=datetime.now(UTC).isoformat(), dashboard_Task_lifetime_not_job_attested=True)
    return result
