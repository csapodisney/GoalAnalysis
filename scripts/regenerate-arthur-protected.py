"""Explicit fresh generation around the effective immutable DAILY; stage before publishing."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import load_settings
from goal_analysis.engine.publication_odds import apply_publication_odds
from goal_analysis.jobs.expert_advisory import refresh_news, review_experts
from goal_analysis.jobs.grok_gate import review_and_repair
from goal_analysis.jobs.nemotron_tickets import curate
from goal_analysis.jobs.portfolio import RunLock, run_portfolio, write_portfolio_bundle
from goal_analysis.jobs.protected_regeneration import (
    compose_protected_daily,
    validate_protected_daily,
)
from goal_analysis.jobs.sports_context_archive import make_weather_provider
from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage import CacheStore, Database
from goal_analysis.storage.portfolio_ledger import PortfolioLedger
from goal_analysis.team.manager import TeamManager
from goal_analysis.team.routing import generation_plan

REASON = ('Friss napi adatcsomag; közös időjárási és motivációs szakértők, '
          'egyszeri Grok-vélemény; változatlan tipsterstílusok és védett DAILY_223.')


def load(path):
    return json.loads(path.read_text('utf-8-sig'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_bundle_once(directory, bundle, attempt):
    """Publish complete artifact directories, or verify an identical earlier attempt."""
    prepared = attempt / 'complete-bundle'
    write_portfolio_bundle(prepared, bundle)
    if directory.exists():
        expected = {path.name: path.read_bytes() for path in prepared.iterdir()}
        if any(not (directory / name).is_file() or (directory / name).read_bytes() != content
               for name, content in expected.items()):
            raise ValueError('Existing publication artifacts differ; evidence was preserved')
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        prepared.rename(directory)


def completed_resume(ledger, stage, *, day, source_run, expected_hash):
    """Recover a committed publication whose audit write/response was interrupted."""
    reviewed = stage / 'reviewed-report.json'
    if not reviewed.exists():
        return None
    report = load(reviewed)
    content = deepcopy(report)
    digest = content.pop('report_sha256', None)
    if digest != canonical_sha256(content) or report.get('date') != day:
        raise ValueError('Staged review integrity/date mismatch')
    history = ledger.list_freeze_history(day)
    if not history or history[-1]['run_id'] != report['run_id']:
        return None
    previous = next((item for item in history if item['run_id'] == source_run), None)
    if (previous is None or previous['snapshot_hash'] != expected_hash
            or history[-1]['snapshot']['report'] != report):
        raise ValueError('Committed revision does not match the protected resume boundary')
    source = previous['snapshot']['report']
    for group in ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets', 'profiles'):
        if ([row for row in report.get(group, []) if row.get('profile_id') == 'daily223']
                != [row for row in source.get(group, []) if row.get('profile_id') == 'daily223']):
            raise ValueError('Committed DAILY differs from the protected source')
    def no_new_publication(*unused):
        raise ValueError('Resume expected an already committed revision')
    revision = ledger.publish_revision(report, expected_hash, REASON, validator=no_new_publication)
    audit = {'date': day, 'run_id': report['run_id'], 'published': True,
             'already_published': True, 'recovered_after_interruption': True,
             'daily223_exactly_preserved': True, 'revision': revision,
             'additional_grok_requests': 0}
    destination = stage / 'publication-attempts' / uuid4().hex / 'publication-verification.json'
    write(destination, audit)
    return {**audit, 'audit_file': str(destination)}


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--source-run', required=True)
    parser.add_argument('--expected-snapshot-hash', required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.stage_only and (args.publish or args.resume):
        parser.error('--stage-only cannot publish or resume')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.source_run):
        parser.error('Invalid source run')
    if not re.fullmatch(r'[a-f0-9]{64}', args.expected_snapshot_hash):
        parser.error('Invalid snapshot hash')
    helper = module('club_source_reader', 'rebuild-arthur-club.py')
    cli = module('arthur_cli', 'run-arthur.py')
    now = lambda: datetime.now(UTC)
    with RunLock(ROOT / 'data/arthur/run.lock'):
        ledger_path = ROOT / 'data/arthur/ledger.sqlite3'
        ledger = PortfolioLedger(ledger_path)
        if args.resume:
            stage = args.resume.resolve()
            if not stage.is_relative_to(ROOT / 'analysis'):
                raise ValueError('Resume directory must be inside project analysis')
            recovered = completed_resume(ledger, stage, day=args.date.isoformat(),
                                         source_run=args.source_run,
                                         expected_hash=args.expected_snapshot_hash)
            if recovered:
                print(json.dumps(recovered, ensure_ascii=True), flush=True)
                return
        source, _ = helper.read_source(ROOT, args.date.isoformat(), args.source_run,
                                      args.expected_snapshot_hash)
        source_hash = canonical_sha256(source)
        if args.resume:
            bundle = load(stage / 'fresh-bundle.json')
            options = load(stage / 'options.json')
        else:
            settings = load_settings(ROOT / 'config/arthur-settings.json')
            config = validate_live_config(load(ROOT / 'config/daily223-live.json'))
            db = Database(ROOT / 'data/goal_analysis.sqlite3')
            db.initialize()
            cache = CacheStore(db)
            football = TheStatsPrimaryClient(ROOT, max_requests=config['max_football_calls'])
            odds = TheStatsOddsFeed(ROOT, config['odds_region'], config['max_odds_credits'])
            weather = make_weather_provider(ROOT, f'protected-{args.date}-{now().isoformat()}')
            options = {}

            def capture(report, available):
                options.update(deepcopy(available))
                return report  # Grok sees the final protected composition, exactly once below.

            print('FRESH_GENERATION_STARTED', flush=True)
            bundle = run_portfolio(config, settings, football, odds, cache, args.date,
                cli.make_reviewer(settings), ledger, weather_provider=weather,
                model_artifact_dir=ROOT / 'data/arthur/models',
                team_plan=generation_plan(settings['enabled_profiles'],
                    TeamManager(ROOT).enroll_missing()['snapshot']),
                nemotron_stylist=lambda available, cards, day, cutoff: curate(ROOT, available, cards, day, cutoff),
                expert_reviewer=lambda report, available: review_experts(ROOT, report, available,
                    weather_provider=weather, standings_client=football, standings_cache=cache,
                    max_standings_requests=min(8, max(0, config['max_football_calls']
                        - report.get('usage', {}).get('football_calls', 0)))),
                package_reviewer=capture)
            run_id = bundle['artifacts']['report']['run_id']
            stage = ROOT / 'analysis' / f'{args.date}-experts' / run_id
            write(stage / 'fresh-bundle.json', bundle)
            write(stage / 'options.json', options)
            print('FRESH_GENERATION_STAGED ' + str(stage), flush=True)
            if args.stage_only:
                return
        boundary = {'expected_source_run_id': source['run_id'],
                    'expected_source_report_sha256': source_hash}
        attempt_id = uuid4().hex
        attempt = stage / 'publication-attempts' / attempt_id
        report = compose_protected_daily(bundle['artifacts']['report'], source, options,
                                         now=now(), **boundary)
        report, options = apply_publication_odds(report, options, report['settings'])
        report.pop('report_sha256', None)
        report['report_sha256'] = canonical_sha256(report)
        # Save the complete pre-review proposal for crash-safe diagnosis and repeatable checks.
        write(attempt / 'protected-proposal.json', report)
        reviewed = stage / 'reviewed-report.json'
        if reviewed.exists():
            report = load(reviewed)
            digest_input = deepcopy(report)
            digest = digest_input.pop('report_sha256', None)
            fresh = bundle['artifacts']['report']
            if (digest != canonical_sha256(digest_input) or report.get('run_id') != fresh['run_id']
                    or report.get('date') != fresh['date']
                    or report.get('protected_regeneration', {}).get('source_report_sha256') != source_hash
                    or report.get('protected_regeneration', {}).get('fresh_report_sha256')
                        != canonical_sha256(fresh)):
                raise ValueError('Staged review integrity/source mismatch')
            validate_protected_daily(report, source, now=now(), **boundary)
        else:
            review_started = stage / 'grok-review-started.json'
            if review_started.exists():
                raise ValueError('Interrupted Grok review requires recovery of the saved result; '
                                 'an automatic second request is forbidden')
            write(review_started, {'run_id': report['run_id'], 'started_at': now().isoformat(),
                                  'proposal_sha256': report['report_sha256']})
            print('ONE_PACKAGE_GROK_REVIEW_STARTED', flush=True)
            report = review_and_repair(ROOT, report, options, manual_review=True)
            report = refresh_news(ROOT, report)
            report['finished_at'] = now().isoformat()
            report.pop('report_sha256', None)
            report['report_sha256'] = canonical_sha256(report)
            write(reviewed, report)
        proof = validate_protected_daily(report, source, now=now(), **boundary)
        cards = [card for card in report.get('recommendations', []) if card.get('legs')]
        audit = {'date': report['date'], 'run_id': report['run_id'], 'published': False,
                 'daily_validation': proof, 'grok_review': report.get('grok_review'),
                 'package_count': len(cards), 'usage': report.get('usage'),
                 'tickets': [{'profile_id': c['profile_id'], 'name': c['profile_name'],
                              'legs': len(c['legs']), 'combined_price': c['combined_price']}
                             for c in cards]}
        write(attempt / 'preflight-verification.json', audit)
        if args.publish:
            helper.read_source(ROOT, args.date.isoformat(), args.source_run, args.expected_snapshot_hash)
            before = helper.immutable_rows(ledger_path)
            backup = ROOT / 'data/arthur/backups' / f"{report['run_id']}-{attempt_id}-before.sqlite3"
            rehearsal = attempt / 'publication-rehearsal.sqlite3'
            if backup.exists() or rehearsal.exists():
                raise ValueError('Refuse to overwrite publication audit evidence')
            backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(ledger_path.as_uri() + '?mode=ro', uri=True) as db, sqlite3.connect(backup) as copied:
                db.backup(copied)
            with sqlite3.connect(backup) as db, sqlite3.connect(rehearsal) as copied:
                db.backup(copied)
            def validator(candidate, stamp):
                return validate_protected_daily(candidate, source, now=stamp, **boundary)
            trial = PortfolioLedger(rehearsal)
            trial.publish_revision(report, args.expected_snapshot_hash, REASON, validator=validator)
            validate_protected_daily(report, source, now=now(), **boundary)
            helper.read_source(ROOT, args.date.isoformat(), args.source_run, args.expected_snapshot_hash)
            bundle['artifacts']['report'] = report
            directory = ROOT / 'reports/arthur' / report['run_id']
            write_bundle_once(directory, bundle, attempt)
            revision = ledger.publish_revision(report, args.expected_snapshot_hash, REASON,
                                               validator=validator)
            after = helper.immutable_rows(ledger_path)
            if not all(after[table].get(key) == value
                       for table, rows in before.items() for key, value in rows.items()):
                raise RuntimeError('Immutable historical row changed')
            if before['portfolio_wagers'] != after['portfolio_wagers']:
                raise RuntimeError('Wager records changed')
            audit.update(published=True, revision=revision, immutable_history_preserved=True,
                         wagers_unchanged=True, backup=str(backup))
            write(directory / f'publication-verification-{attempt_id}.json', audit)
        audit['audit_file'] = str(attempt / 'publication-verification.json')
        write(attempt / 'publication-verification.json', audit)
        print(json.dumps(audit, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
