"""TESTONLY total verifier resource bounds remain distinct from optional gaps."""
import json

import pytest

from goal_analysis.jobs.agent_snapshot import digest
from goal_analysis.jobs.workshop_verify import (
    SavedReader,
    SavedVerificationLimit,
    verify_new_cohort,
)


def test_total_byte_limit_is_fatal_to_reader_and_final_clock_expiry_cannot_be_verified(tmp_path):
    tiny = tmp_path / 'tiny.json'
    tiny.write_text('{}', encoding='utf-8')
    reader = SavedReader(tmp_path, maximum_bytes=1)
    with pytest.raises(SavedVerificationLimit, match='SAVED_VERIFICATION_BYTE_BOUND') as failure:
        reader.pin(tiny)
    assert not isinstance(failure.value, ValueError)
    assert reader.read_bytes == 0 and reader.files == {}

    report = {'run_id': '2026-11-14-TESTONLY-resource-bound', 'date': '2026-11-14'}
    report['report_sha256'] = digest(report)
    directory = tmp_path / 'reports/arthur' / report['run_id']
    directory.mkdir(parents=True)
    (directory / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    limited = verify_new_cohort(tmp_path, report, reader=SavedReader(tmp_path, maximum_bytes=1))
    assert limited['status'] == 'PARTIAL'
    assert any(gap.get('reason') == 'SAVED_VERIFICATION_BYTE_BOUND'
               and gap.get('verification_aborted') is True for gap in limited['gaps'])
    assert limited['physical_bytes_read'] == 0 and limited['new_model_or_tool_calls'] == 0
    clock = [0.0]

    class ExpiringReader(SavedReader):
        def pin(self, path, **kwargs):
            value = super().pin(path, **kwargs)
            clock[0] = 2.0
            return value

    reader = ExpiringReader(tmp_path, seconds=1, clock=lambda: clock[0])
    result = verify_new_cohort(tmp_path, report, reader=reader)
    assert result['status'] == 'PARTIAL'
    assert any(gap.get('reason') == 'SAVED_VERIFICATION_TIME_BOUND'
               and gap.get('verification_aborted') is True for gap in result['gaps'])
    assert result['new_model_or_tool_calls'] == 0
