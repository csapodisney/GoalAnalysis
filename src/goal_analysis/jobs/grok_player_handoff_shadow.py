"""Detached next-review input using witnessed player facts, never a new review.

The frozen report and its prices remain inputs. This sidecar is not an advisor,
request cache, publication gate, numerical adjustment or permission to dispatch.
"""
import json
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.grok_packet_tables import wire_packet
from goal_analysis.agents.grok_research import _packet as validate_packet
from goal_analysis.agents.grok_stats_data import validate_stats_context
from goal_analysis.jobs.grok_advisory import _packet as build_packet
from goal_analysis.jobs.player_collection_shadow import _record
from goal_analysis.jobs.player_specialist_shadow import TTL, _contexts
from goal_analysis.jobs.xg_specialist_shadow import _targets
from goal_analysis.normalization.canonical import utc

VERSION = 'canonical-player-next-grok-input-shadow-v1'


def prepare(root, report, fixtures, *, as_of):
    """Read only exact selected fixtures; strict TTL/raw/receipt readers apply.

    A stale target or missing fact is an omission, never a healthy-team claim.
    No supplied sidecar can substitute for physically witnessed canonical facts.
    """
    cutoff = utc(as_of)
    if not isinstance(fixtures, list) or len(fixtures) > 1000:
        raise ValueError('BOUNDED_GROK_PLAYER_TARGETS_REQUIRED')
    original = canonical_sha256([report, fixtures])
    packet, coverage, run_id, report_hash = build_packet(report, cutoff)
    by_id = {}
    for fixture in fixtures:
        fid = fixture['fixture_id']
        if fid in by_id:
            raise ValueError('DUPLICATE_GROK_PLAYER_TARGET')
        by_id[fid] = fixture
    selected = {}
    for fixture in packet['fixtures']:
        fid = fixture['fixture_id']
        target = by_id.get(fid)
        if target is None:
            continue
        if (any(target.get(key) != fixture.get(key) for key in ('home_team', 'away_team'))
                or utc(target['kickoff']) != utc(fixture['kickoff'])
                or any(target.get(key) != fixture.get(key) for key in (
                    'source_provider', 'provider_fixture_id', 'provider_league_id',
                    'provider_season_id', 'provider_home_team_id', 'provider_away_team_id'))):
            raise ValueError('GROK_PLAYER_TARGET_IDENTITY_MISMATCH')
        if utc(target['kickoff']) > cutoff+timedelta(minutes=15):
            selected[fid] = target
    targets, _, target_issues = _targets(Path(root).resolve(), selected, cutoff) if selected else ({}, {}, {})
    contexts, issues, total = _contexts(Path(root).resolve(),
        {fid: selected[fid] for fid in targets}, cutoff) if targets else ({}, {}, 0)
    counts, provenance, omissions = Counter(), [], []
    for fixture in packet['fixtures']:
        fid = fixture['fixture_id']
        previous = fixture.get('the_stats_api')
        # Do not carry an un-revalidated player assertion from the old report.
        if previous:
            previous['facts'] = [fact for fact in previous['facts'] if fact['category'] not in TTL]
        if fid not in targets:
            reason = 'PLAYER_CONTEXT_TARGET_UNPROVEN'
            omissions.append({'fixture_id': fid, 'reason': reason})
            if previous:
                previous['missing'] = sorted(set(previous.get('missing', [])) | {reason})
            continue
        record = _record(selected[fid])
        record.update(version='thestatsapi-context-v1', provider='the_stats_api', observed_at=cutoff.isoformat())
        if previous:
            record['facts'].extend(previous['facts'])
            record['missing'].extend(code for code in previous.get('missing', [])
                                     if code.split(':', 1)[0] not in TTL)
        rows = contexts.get(fid, [])
        present = set()
        for row in rows:
            category, payload = row['category'], row['payload']
            subject = payload.get('player_id' if category == 'player_season_stats' else
                                  'match_id' if category == 'lineup' else 'team_id')
            record['facts'].append({'category': category, 'subject_id': subject, 'status': 'AVAILABLE',
                'data': deepcopy(payload), 'source': {'provider': 'the_stats_api',
                    'source_url': row['source_url'], 'content_hash': row['source_sha256'],
                    'observed_at': row['observed_at'], 'resource_type': category, 'published_at': None}})
            present.add((category, subject)); counts[category] += 1
            provenance.append({'fixture_id': fid, 'category': category, 'subject_id': subject,
                **{key: row[key] for key in ('event_id', 'source_sha256', 'source_url',
                                             'observed_at', 'available_at', 'availability_basis')}})
        for team in (selected[fid]['provider_home_team_id'], selected[fid]['provider_away_team_id']):
            for category in ('squad', 'injuries_suspensions'):
                if (category, team) not in present:
                    record['facts'].append({'category': category, 'subject_id': team, 'status': 'UNAVAILABLE'})
        if not any(category == 'lineup' for category, _ in present):
            record['facts'].append({'category': 'lineup', 'subject_id': selected[fid]['provider_fixture_id'],
                                    'status': 'UNAVAILABLE'})
            record['missing'].append('PLAYER_LINEUP_UNAVAILABLE')
        # Targeted absence stats cannot certify the full squad's production.
        record['missing'].append('PLAYER_SEASON_STATS_PARTIAL_OR_UNPROVEN')
        fixture['the_stats_api'] = validate_stats_context(record, fixture, cutoff)
    for quote in packet.get('quote_sources', []):
        if quote.get('quote_provider') == 'the_stats_api':
            quote.setdefault('quoted_at', None)
    clean, _, _, _ = validate_packet(packet)
    wire = wire_packet(clean)
    characters = len(json.dumps(wire, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    if characters > 100000:
        raise OverflowError('GROK_PLAYER_PACKET_TOO_LARGE')
    fresh_prices = bool(clean.get('quote_sources')) and all(
        (stamp := row.get('quote_observed_at') or row.get('quoted_at')) is not None
        and 0 <= (cutoff-utc(stamp)).total_seconds() <= 1800 for row in clean['quote_sources'])
    assert canonical_sha256([report, fixtures]) == original
    return {'version': VERSION, 'mode': 'SHADOW', 'status': 'NEXT_REVIEW_INPUT_PREPARED',
        'as_of': cutoff.isoformat(), 'run_id': run_id, 'report_sha256': report_hash,
        'packet': clean, 'wire_packet': wire, 'packet_sha256': canonical_sha256(clean),
        'wire_characters': characters, 'schema_valid': True, 'fresh_price_receipts': fresh_prices,
        'dispatch_allowed': False, 'original_grok_request_rewritten': False,
        'coverage': coverage, 'accepted_player_facts': dict(counts), 'source_events': provenance,
        'target_issues': target_issues, 'context_issues': issues, 'context_events_read': total,
        'omissions': omissions, 'full_player_coverage_complete': False,
        'external_requests': 0, 'llm_requests': 0, 'production_writes': 0,
        'live_decision_effect': False, 'probability_adjustment': 0}
