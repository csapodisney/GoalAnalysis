"""Fixed poststop reader; owned parent Job supplies the 180-second hard deadline."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from goal_analysis.jobs.workshop_dispatch import exclusive_json
from goal_analysis.jobs.workshop_verify import SavedReader, verify_new_cohort


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if (Path(args.run_id).name != args.run_id or output.name != 'physical-verification.json'
            or output.exists() or output.is_relative_to(ROOT)
            or not output.parent.name.startswith('arthur-ten-')):
        raise ValueError('EXACT_OWNED_POSTSTOP_OUTPUT_REQUIRED')

    def guard(event, values):
        if event.startswith(('socket.', 'subprocess.')) or event in {'os.system', 'os.posix_spawn', 'sqlite3.connect'}:
            raise PermissionError('POSTSTOP_READER_EXTERNAL_OR_DATABASE_OPERATION_DENIED')
        if event == 'open':
            path, mode, flags = values
            if (isinstance(path, (str, bytes)) and ((isinstance(mode, str) and any(k in mode for k in 'wax+'))
                    or isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL))
                    and Path(path).resolve() != output):
                raise PermissionError('POSTSTOP_READER_WRITE_OUTSIDE_OWN_RESULT')

    sys.addaudithook(guard)
    reader = SavedReader(ROOT)
    report = reader.pin(ROOT / 'reports/arthur' / args.run_id / 'report.json')
    if report.get('run_id') != args.run_id:
        raise ValueError('POSTSTOP_RUN_ID_CHANGED')
    result = verify_new_cohort(ROOT, report, reader=reader)
    exclusive_json(output, result)
    return 0  # Reader completion is independent from semantic VERIFIED/PARTIAL.


if __name__ == '__main__':
    raise SystemExit(main())
