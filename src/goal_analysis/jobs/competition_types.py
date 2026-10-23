"""Witnessed, read-only competition metadata view and post-save SHADOW archive."""
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from goal_analysis.jobs.match_stage_contract import SCOPE as NULL_STAGE_SCOPE
from goal_analysis.jobs.match_stage_contract import read_contract
from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.competition_types import (
    RULE_SHA256,
    VERSION,
    classify_competition,
    unknown,
)
from goal_analysis.normalization.identity import field_id, native_id
from goal_analysis.normalization.training_datasets import snapshot
from goal_analysis.storage.raw_evidence import read_witnessed_raw
from goal_analysis.storage.training_datasets import DatasetStore


def _read(db, row, root, cutoff):
    body, proof = read_witnessed_raw(db, row, root, cutoff)
    if not isinstance(body.get('data'), list):
        raise TypeError('LIST_SOURCE_REQUIRED')
    return body['data'], proof


def _newest(values, key):
    """Same-time contradictory metadata is unknown, never a hash-order winner."""
    selected, ambiguous = {}, set()
    for value, proof in values:
        identifier = value[key]
        old = selected.get(identifier)
        when = utc(proof['times']['observed_at'])
        if old is None or when > utc(old[1]['times']['observed_at']):
            selected[identifier] = (value, proof)
            ambiguous.discard(identifier)
        elif when == utc(old[1]['times']['observed_at']) and value != old[0]:
            ambiguous.add(identifier)
    return {k: v for k, v in selected.items() if k not in ambiguous}, ambiguous


def _match(row):
    if row.get('stage_name') is not None and not isinstance(row['stage_name'], str):
        raise ValueError('INVALID_STAGE_NAME')
    group = row.get('group_label')
    if group is not None and (not isinstance(group, str) or not 1 <= len(group) <= 100):
        raise ValueError('INVALID_FIXTURE_GROUP')
    return {'id': native_id(row['id'], 'mt'),
            'competition_id': native_id(field_id(row, 'competition'), 'comp'),
            'season_id': native_id(field_id(row, 'season'), 'sn'),
            'kickoff': utc(row['utc_date']).isoformat(),
            'home_team_id': native_id(row['home_team']['id'], 'tm'),
            'away_team_id': native_id(row['away_team']['id'], 'tm'),
            'stage_name': row.get('stage_name'), 'group_label': group,
            'stage_field_present': 'stage_name' in row, 'group_field_present': 'group_label' in row}


def inspect_competition_types(root, fixtures, *, as_of):
    """Exact requested native fixtures only; no calendar or event additions."""
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_FIXTURE_LIST_REQUIRED')
    root, cutoff = Path(root).resolve(), utc(as_of)
    contract=read_contract(root,as_of=cutoff)
    identifiers, requested = set(), []
    for fixture in fixtures:
        fid = fixture['fixture_id']
        if fid in identifiers:
            raise ValueError('DUPLICATE_REQUESTED_FIXTURE')
        identifiers.add(fid)
        requested.append(fixture)
    values, catalogue, issues, proofs = [], [], Counter(), {}
    path = root/'data/goal_analysis.sqlite3'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'raw_snapshots', 'temporal_receipts'} <= tables:
                # Follow the actual fixture's exact raw-source pointer. Do not
                # scan an ever-growing five-year match archive on each daily run.
                raw_rows = {r['content_hash']: dict(r) for r in db.execute("""SELECT rowid AS _sequence,*
                    FROM raw_snapshots WHERE provider='the_stats_api' AND resource_type='competition_catalogue'""")}
                for fixture in requested:
                    content = (fixture.get('fixture_source') or {}).get('content_hash')
                    if content is None:
                        continue
                    row = db.execute("""SELECT rowid AS _sequence,* FROM raw_snapshots
                        WHERE content_hash=? AND provider='the_stats_api'""", (content,)).fetchone()
                    if row is not None:
                        raw_rows[content] = dict(row)
                raw_rows = [row for row in raw_rows.values() if utc(row['fetched_at']) <= cutoff]
                # Budget failure is explicit, not a half-read catalogue used as
                # if no newer conflicting evidence existed.
                if len(raw_rows) > 1000 or sum(r['size_bytes'] for r in raw_rows) > 64*1024*1024:
                    raise ValueError('COMPETITION_SOURCE_BUDGET_EXCEEDED')
                for row in raw_rows:
                    try:
                        if utc(row['fetched_at']) > cutoff:
                            continue
                        data, proof = _read(db, row, root, cutoff)
                        route = urlsplit(row['source_url']).path
                        if route == '/api/football/competitions':
                            parsed = [(dict(d), proof) for d in data
                                      if native_id(d['id'], 'comp')]
                            catalogue.extend(parsed)
                        elif route == '/api/football/matches':
                            parsed = [(_match(d), proof) for d in data]
                            values.extend(parsed)
                    except (ValueError, TypeError, KeyError, OSError):
                        issues['INVALID_OR_UNAVAILABLE_SOURCE'] += 1
    matches, conflicts = {}, set()
    for value, proof in values:
        key = (proof['source_key'], value['id'])
        if key in matches and matches[key][0] != value:
            conflicts.add(key)
        matches[key] = (value, proof)
    competitions, catalogue_conflicts = _newest(catalogue, 'id')
    rows = []
    for fixture in sorted(requested, key=lambda f: f['fixture_id']):
        result = {'fixture_id': fixture['fixture_id'], **unknown('MISSING_LINK')}
        try:
            if fixture.get('source_provider') != 'the_stats_api':
                raise ValueError('MISSING_NATIVE_FIXTURE_LINK')
            match_id = native_id(fixture['provider_fixture_id'], 'mt')
            key = ((fixture.get('fixture_source') or {}).get('content_hash'), match_id)
            if fixture['fixture_id'] != 'the_stats_api:'+match_id:
                raise ValueError('MISSING_NATIVE_FIXTURE_LINK')
            if key in conflicts:
                raise ValueError('AMBIGUOUS_MATCH_METADATA')
            if key not in matches:
                raise ValueError('NO_WITNESSED_MATCH_METADATA')
            match, match_proof = matches[key]
            if (match['competition_id'] != fixture['provider_league_id']
                    or match['season_id'] != fixture['provider_season_id']
                    or utc(match['kickoff']) != utc(fixture['kickoff'])
                    or match['home_team_id'] != fixture['provider_home_team_id']
                    or match['away_team_id'] != fixture['provider_away_team_id']
                    or match['home_team_id'] == match['away_team_id']):
                raise ValueError('EXACT_FIXTURE_IDENTITY_CONFLICT')
            if match['competition_id'] in catalogue_conflicts:
                raise ValueError('AMBIGUOUS_CATALOGUE_METADATA')
            if match['competition_id'] not in competitions:
                raise ValueError('NO_WITNESSED_CATALOGUE_ENTRY')
            comp, comp_proof = competitions[match['competition_id']]
            explicit_null = bool(contract is not None
                and (match['competition_id'],match['season_id'])==NULL_STAGE_SCOPE
                and match['stage_field_present'] and match['stage_name'] is None
                and match['group_field_present'] and match['group_label'] is None)
            classification = classify_competition(comp, match['stage_name'],reviewed_explicit_null=explicit_null)
            result.update(classification, native_fixture_id=match_id,
                          native_competition_id=match['competition_id'], season_id=match['season_id'],
                          kickoff=match['kickoff'], competition_name=comp['name'],
                          source_group_label=match['group_label'],
                          source_receipt_ids=[match_proof['receipt_id'], comp_proof['receipt_id']])
            for proof in (match_proof, comp_proof):
                proofs[proof['receipt_id']] = proof
            if explicit_null and classification['phase']=='LEAGUE_PHASE':
                result['stage_interpretation_basis']=contract['basis']
                result['source_receipt_ids'].append(contract['receipt_id'])
                proofs[contract['receipt_id']]=contract
        except (ValueError, TypeError, KeyError):
            issues['MISSING_OR_CONFLICTING_FIXTURE_LINK'] += 1
        rows.append(result)
    counts = Counter(row['family'] for row in rows)
    dataset = snapshot(rows, role='competition_types_shadow', as_of=cutoff,
                       recipe={'version': VERSION, 'rule_sha256': RULE_SHA256},
                       lineage=[proofs[k] for k in sorted(proofs)])
    return {'version': VERSION, 'mode': 'SHADOW', 'live_decision_effect': False,
            'status': 'PARTIAL' if any(r['status'] != 'CLASSIFIED' for r in rows) else 'COMPLETE',
            'as_of': cutoff.isoformat(), 'requested_fixtures': len(rows), 'counts': dict(counts),
            'phase_unknown': sum(r['phase'] == 'UNKNOWN' for r in rows), 'rows': rows,
            'dataset': dataset, 'issues': dict(issues), 'external_requests': 0,
            'production_writes': 0, 'catalogue_scope': 'PER_ENTRY_NOT_GLOBAL_CATALOGUE_CERTIFICATION'}


def archive_competition_types(root, report, *, as_of):
    """Called after tickets are saved; isolated failure cannot alter publication."""
    try:
        view = inspect_competition_types(root, report.get('fixtures', []), as_of=as_of)
        store = DatasetStore(Path(root)/'data/arthur/competition-types/datasets')
        created = store.put(view['dataset'])
        return {k: view[k] for k in ('version', 'mode', 'status', 'counts', 'phase_unknown',
                                    'requested_fixtures', 'live_decision_effect', 'external_requests')} | {
            'dataset_id': view['dataset']['manifest']['dataset_id'], 'dataset_created': created}
    except Exception:  # noqa: BLE001 - optional post-save diagnostics, never a daily veto
        return {'version': VERSION, 'mode': 'SHADOW', 'status': 'UNAVAILABLE',
                'reason': 'COMPETITION_TYPE_ARCHIVE_FAILED', 'external_requests': 0,
                'live_decision_effect': False}
