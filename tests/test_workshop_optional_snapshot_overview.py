"""Two new inert overview controls; no DB, model, provider, or network."""
import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from goal_analysis.agents.profile_roster import AGENT_IDS
from goal_analysis.jobs.agent_snapshot import FAMILIES, digest
from goal_analysis.workshop_service import SavedWorkshop, SnapshotOverviewReadBound, WorkshopService
from goal_analysis.workshop_views import COMPANION_BINDING_FIELDS, COMPANION_ROLES

DAY = '2026-11-14'
NOW = datetime(2026, 11, 14, 11, tzinfo=UTC)


def _report(variants=1):
    run, snapshot = '2026-11-14-testonly1', 'a' * 64
    fixtures = ['the_stats_api:mt_1', 'the_stats_api:mt_2']
    report = {'run_id': run, 'date': DAY, 'observed_at': NOW.isoformat(),
        'generated_tickets': [], 'full_day_calendar': {'provider_fixture_ids': fixtures},
        'ten_agent_decisions': {'snapshot_id': snapshot, 'owners': []},
        'companion_ticket_channels': {'version': 'companion-ticket-channels-v1', 'channels': []}}
    for owner in AGENT_IDS:
        session = 'TESTONLY-session-' + owner
        options = []
        for index in range(variants):
            option = owner + '-variant-' + str(index)
            source = {'ticket_id': run + '-' + option, 'profile_id': owner, 'date': DAY,
                'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
                'legs': [{'fixture_id': fixtures[0], 'decimal_price': 2.0}],
                'combined_price': 16.0, 'agent_product_binding': {'agent_id': owner,
                    'option_id': option, 'snapshot_id': snapshot, 'run_id': run, 'target_date': DAY}}
            options.append({'option_id': option, 'canonical_card': source,
                            'canonical_card_sha256': digest(source)})
        decision = {'status': 'FINAL_DECISION', 'agent_id': owner, 'session_id': session,
            'snapshot_id': snapshot, 'option_ids': [row['option_id'] for row in options],
            'selected_options': options}
        report['ten_agent_decisions']['owners'].append({'agent_id': owner,
            'session_id': session, 'snapshot_id': snapshot, 'status': 'DECISION_COMPLETED',
            'decision': decision})
        for option in options:
            card = deepcopy(option['canonical_card'])
            card['agent_decision'] = {'agent_id': owner, 'session_id': session,
                'snapshot_id': snapshot, 'option_id': option['option_id'],
                'decision_sha256': digest(decision),
                'canonical_source_card_sha256': option['canonical_card_sha256']}
            report['generated_tickets'].append(card)
    for channel in COMPANION_ROLES:
        ticket = run + '-' + channel
        row = {'channel_id': channel, 'run_id': run, 'target_date': DAY,
            'shared_snapshot_id': snapshot, 'calendar_ids_sha256': digest(fixtures),
            'input_sha256': 'b' * 64, 'settings_sha256': 'f' * 64, 'producer': {'module': 'TESTONLY', 'function': channel, 'version': 1},
            'producer_report_sha256': 'c' * 64, 'claim_sha256': 'd' * 64,
            'canonical_source_card_sha256': 'e' * 64, 'status': 'COMPLETE', 'reason': None,
            'ticket_ids': [ticket]}
        report['companion_ticket_channels']['channels'].append(row)
        report['generated_tickets'].append({'ticket_id': ticket, 'channel_id': channel,
            'profile_id': 'daily223', 'producer_profile_id': 'daily223', 'date': DAY,
            'status': 'DRAFT', 'publication_scope': 'RESEARCH', 'playable': False,
            'legs': [{'fixture_id': fixtures[1], 'decimal_price': 2.0}], 'combined_price': 16.0,
            'companion_decision': {**{key: deepcopy(row[key]) for key in COMPANION_BINDING_FIELDS},
                                  'authority': 'PROTECTED_CONSTRUCTOR_NOT_AGENT_REASONING'}})
    report['report_sha256'] = digest(report)
    return report


def _service(tmp_path, report, monkeypatch):
    repository = SavedWorkshop(tmp_path)
    monkeypatch.setattr(repository, 'report', lambda **kwargs: report)
    monkeypatch.setattr(repository, 'states', lambda saved: [])
    monkeypatch.setattr('goal_analysis.workshop_service.load_settings', lambda path: {})
    store = SimpleNamespace(latest=lambda day: None, schedule=lambda now: {'enabled': False})
    service = WorkshopService(tmp_path, tmp_path / 'settings.json', repository=repository,
                              store=store, clock=lambda: NOW)
    path = tmp_path / 'data/arthur/agent-decisions' / report['run_id'] / 'shared-data.json'
    path.parent.mkdir(parents=True)
    return service, repository, path


def test_oversized_and_missing_optional_snapshot_keep_twelve_canonical_cards_without_read(tmp_path, monkeypatch):
    report = _report()
    before = deepcopy(report)
    service, repository, path = _service(tmp_path, report, monkeypatch)
    path.write_bytes(b'TESTONLY oversized stat; this is never parsed')
    real_stat, real_open = Path.stat, Path.open
    reads = []

    def oversized_stat(item, *args, **kwargs):
        value = real_stat(item, *args, **kwargs)
        if item == path:
            fields = list(value)
            fields[6] = 512 * 1024 * 1024 + 1
            return os.stat_result(fields)
        return value

    def deny_snapshot_open(item, *args, **kwargs):
        if item == path:
            reads.append(str(item))
            raise AssertionError('No oversized snapshot read is allowed')
        return real_open(item, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', oversized_stat)
    monkeypatch.setattr(Path, 'open', deny_snapshot_open)
    view = service.view(DAY)
    assert len(view['cards']) == 12 and view['capacity']['filled_main_slots'] == 12
    assert view['capacity']['target_met'] is True
    assert view['snapshot_readback'] == {
        'status': 'UNKNOWN', 'reason': 'SAVED_SNAPSHOT_EXCEEDS_INTERACTIVE_READ_BOUND',
        'expected_snapshot_sha256': report['ten_agent_decisions']['snapshot_id'],
        'canonical_report_sha256': report['report_sha256'], 'raw_snapshot_read': False,
        'physical_verification': 'NOT_PERFORMED_BY_VIEW',
    }
    assert view['calendar']['fixture_count'] is None and view['calendar']['snapshot_id'] is None
    assert all(row['status'] == 'UNKNOWN' and row['reason'] == view['snapshot_readback']['reason']
               for row in view['families'])
    assert len(view['families']) == 12
    cards = service.cards(report['run_id'], 0, 32)
    assert [item['ticket'] for item in cards['items']] == report['generated_tickets']
    with pytest.raises(SnapshotOverviewReadBound, match='SAVED_SNAPSHOT_READ_BOUND'):
        repository.snapshot(report)
    assert reads == [] and report == before
    path.unlink()
    missing = service.view(DAY)
    assert missing['snapshot_readback']['reason'] == 'SAVED_SNAPSHOT_NOT_FOUND'
    assert missing['capacity']['filled_main_slots'] == 12 and missing['cards'] == view['cards']
    assert reads == [] and report == before


def test_small_snapshot_is_exact_and_corruption_or_foreign_identity_is_not_hidden(tmp_path, monkeypatch):
    snapshot = {'run_id': '2026-11-14-testonly2', 'target_date': DAY, 'fixture_ids': ['fixture-1'],
                'families': {key: {'status': 'UNKNOWN', 'value': None, 'reason': 'TESTONLY_UNKNOWN'}
                             for key in FAMILIES}}
    raw = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    report = {'run_id': snapshot['run_id'], 'date': DAY, 'generated_tickets': [],
              'ten_agent_decisions': {'snapshot_id': hashlib.sha256(raw).hexdigest(), 'owners': []}}
    report['report_sha256'] = digest(report)
    service, repository, path = _service(tmp_path, report, monkeypatch)
    path.write_bytes(raw)
    view = service.view(DAY)
    assert 'snapshot_readback' not in view
    assert view['calendar']['fixture_count'] == 1
    assert view['calendar']['snapshot_id'] == report['ten_agent_decisions']['snapshot_id']
    assert all(row['reason'] == 'TESTONLY_UNKNOWN' for row in view['families'])
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='SAVED_SNAPSHOT_PHYSICAL_HASH_MISMATCH'):
        service.view(DAY)
    foreign = {**snapshot, 'run_id': '2026-11-14-foreign1'}
    foreign_raw = json.dumps(foreign, sort_keys=True, separators=(',', ':')).encode()
    report['ten_agent_decisions']['snapshot_id'] = hashlib.sha256(foreign_raw).hexdigest()
    path.write_bytes(foreign_raw)
    with pytest.raises(ValueError, match='SAVED_SNAPSHOT_IDENTITY_MISMATCH'):
        service.view(DAY)

    def unrelated_error(saved):
        raise ValueError('TESTONLY_OTHER_INTEGRITY_ERROR')

    monkeypatch.setattr(repository, 'snapshot', unrelated_error)
    with pytest.raises(ValueError, match='TESTONLY_OTHER_INTEGRITY_ERROR'):
        service.view(DAY)
