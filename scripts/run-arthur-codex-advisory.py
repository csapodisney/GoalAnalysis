"""Review one saved Arthur artifact through Codex without regenerating tickets."""

from __future__ import annotations

import runpy
from pathlib import Path


def main(argv=None):
    legacy_entrypoint = Path(__file__).with_name("run-arthur-nemotron.py")
    return runpy.run_path(str(legacy_entrypoint))["main"](argv)


if __name__ == "__main__":
    raise SystemExit(main())
