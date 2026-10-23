"""Keep the repaired club/paper DOM contract inside ordinary regression runs."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_current_catalogue_and_legacy_paper_frontend_contracts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js not installed; frontend gate not validated on this host')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, 'tests/tipster_frontend_check.cjs'], cwd=root,
        capture_output=True, text=True, encoding='utf-8', check=False, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '107 tipster and paper-accounting frontend assertions passed' in result.stdout
