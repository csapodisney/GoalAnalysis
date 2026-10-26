"""Create or restore an exact supported Arthur configuration; no provider calls."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from goal_analysis.jobs import configuration_rollback as rollback


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='operation', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--label', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('release_id')
    restore = commands.add_parser('restore')
    restore.add_argument('release_id')
    restore.add_argument('--expect', required=True)
    restore.add_argument('--execute', action='store_true')
    recover = commands.add_parser('recover')
    recover.add_argument('operation_id')
    recover.add_argument('--execute', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'capture':
            result = rollback.capture(args.root, provenance={'kind': 'OPERATOR_CONFIGURATION_CAPTURE',
                                                              'label': args.label})
        elif args.operation == 'plan' or (args.operation == 'restore' and not args.execute):
            result = rollback.plan(args.root, args.release_id)
        elif args.operation == 'restore':
            result = rollback.restore(args.root, args.release_id, expected_configuration_id=args.expect)
        else:
            result = rollback.recover(args.root, args.operation_id)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        print(json.dumps({'status': 'BLOCKED', 'error_class': type(error).__name__,
                          'reason': str(error)[:300]}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
