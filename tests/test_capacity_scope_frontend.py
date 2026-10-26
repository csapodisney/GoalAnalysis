"""Actual dashboard count scopes and missingness, no browser/network/data writes."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('mode', ['v2', 'legacy', 'invalid'])
def test_dashboard_capacity_scope_and_unknown_boundaries(mode):
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    assert node, 'Node is required to validate the actual frontend boundary'
    result = subprocess.run([node, str(root / 'tests/capacity_scope_frontend_check.cjs'), mode],
                            cwd=root, capture_output=True, text=True, encoding='utf-8',
                            check=False, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CAPACITY_SCOPE_FRONTEND_PASS:' + mode in result.stdout
