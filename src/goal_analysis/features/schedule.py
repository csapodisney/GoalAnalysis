"""Witnessed schedule features; kickoff gaps and venue distance are proxies.

No actual recovery duration or team itinerary is fabricated. Empty/incomplete
calendar coverage cannot mean a team had no matches. No numeric ticket effect.
"""
import math
from datetime import timedelta

from goal_analysis.normalization.canonical import utc
from goal_analysis.normalization.identity import native_id
from goal_analysis.normalization.leakage import model_inputs
from goal_analysis.normalization.training_datasets import snapshot

VERSION = 'team-schedule-shadow-v1'
WINDOW_DAYS = 14
MAX_AGE_HOURS = 6
ACTIVE = {'finished', 'live', 'scheduled'}
INACTIVE = {'cancelled', 'postponed'}


def _match(row):
    r = dict(row)
    for key, prefix in (('fixture_id','mt'), ('competition_id','comp'), ('season_id','sn'),
                        ('home_team_id','tm'), ('away_team_id','tm')):
        native_id(r[key], prefix)
    if r['home_team_id'] == r['away_team_id'] or r['status'] not in ACTIVE | INACTIVE:
        raise ValueError('INVALID_SCHEDULE_PARTICIPANTS_OR_STATUS')
    r['kickoff'] = utc(r['kickoff']).isoformat()
    return r


def _calendar(payload, target, team, cutoff):
    if (payload.get('team_id') != team or payload.get('scope') != 'ALL_COMPETITIONS_ALL_STATUSES'
            or payload.get('pagination_complete') is not True):
        raise ValueError('INCOMPLETE_OR_FILTERED_CALENDAR')
    kickoff = utc(target['kickoff'])
    if (utc(payload['window_start']) > kickoff-timedelta(days=WINDOW_DAYS)
            or utc(payload['window_end_exclusive']) < kickoff+timedelta(days=WINDOW_DAYS)):
        raise ValueError('INSUFFICIENT_CALENDAR_WINDOW')
    if cutoff-utc(payload['observed_at']) > timedelta(hours=MAX_AGE_HOURS):
        raise ValueError('STALE_CALENDAR')
    seen, rows = set(), []
    for item in payload['matches']:
        row = _match(item)
        if row['fixture_id'] in seen:
            raise ValueError('DUPLICATE_CALENDAR_MATCH')
        seen.add(row['fixture_id'])
        when = utc(row['kickoff'])
        if team not in {row['home_team_id'], row['away_team_id']}:
            raise ValueError('CALENDAR_TEAM_MISMATCH')
        if not utc(payload['window_start']) <= when < utc(payload['window_end_exclusive']):
            raise ValueError('MATCH_OUTSIDE_CALENDAR_WINDOW')
        if row['status'] in {'finished', 'live'} and when > cutoff:
            raise ValueError('FUTURE_MATCH_ALREADY_PLAYED')
        rows.append(row)
    targets = [r for r in rows if r['fixture_id'] == target['fixture_id']]
    if len(targets) != 1 or any(targets[0][k] != target[k] for k in
            ('competition_id', 'season_id', 'home_team_id', 'away_team_id', 'kickoff')):
        raise ValueError('TARGET_NOT_EXACTLY_LINKED_TO_CALENDAR')
    if targets[0]['status'] != 'scheduled':
        raise ValueError('TARGET_NOT_SCHEDULED')
    return rows


def _distance(first, second):
    a, b = math.radians(first['latitude']), math.radians(second['latitude'])
    dlat = b-a
    dlon = math.radians(second['longitude']-first['longitude'])
    h = math.sin(dlat/2)**2 + math.cos(a)*math.cos(b)*math.sin(dlon/2)**2
    return 6371.0088 * 2*math.asin(math.sqrt(min(1., max(0., h))))


def build_schedule_features(target, calendars, *, as_of, venues=None):
    cutoff, target = utc(as_of), _match(target)
    if cutoff > utc(target['kickoff'])-timedelta(minutes=15):
        raise ValueError('PREMATCH_LOCKOUT')
    payloads = model_inputs(calendars, as_of=cutoff)
    venue_envelopes = [] if venues is None else venues
    venue_rows = model_inputs(venue_envelopes, as_of=cutoff)
    positions = {};match_positions = {}
    for row in venue_rows:
        if row.get('provider') == 'wikidata' and row.get('fixture_id'):
            if row.get('coordinate_scope') != 'VERIFIED_STADIUM' or row['fixture_id'] in match_positions:
                raise ValueError('UNVERIFIED_OR_DUPLICATE_STADIUM_COORDINATES')
            _match(row)
            for name,bound in (('latitude',90),('longitude',180)):
                value=row.get(name)
                if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>bound:
                    raise ValueError('INVALID_STADIUM_COORDINATES')
            match_positions[row['fixture_id']]=row
            continue
        key = row.get('venue_id')
        if (not isinstance(key, str) or not key or key in positions
                or row.get('provider') != 'the_stats_api'
                or row.get('coordinate_scope') != 'VERIFIED_STADIUM'):
            raise ValueError('UNVERIFIED_OR_DUPLICATE_STADIUM_COORDINATES')
        for name, bound in (('latitude',90),('longitude',180)):
            value = row.get(name)
            if type(value) not in (int,float) or not math.isfinite(value) or not -bound <= value <= bound:
                raise ValueError('INVALID_STADIUM_COORDINATES')
        positions[key] = row
    result = []
    for side in ('home','away'):
        team = target[side+'_team_id']
        row = {'side':side, 'team_id':team, 'status':'MISSING_CALENDAR',
               'previous_fixture':None, 'next_fixture':None, 'previous_kickoff_gap_hours':None,
               'next_kickoff_gap_hours':None, 'confirmed_played_prev7d_lower_bound':None,
               'previous_kickoff_gap_days':None, 'next_kickoff_gap_days':None,
               'unconfirmed_past_scheduled_count':None, 'future_planned_before_target_count':None,
               'planned_or_confirmed_prev7d':None, 'planned_or_confirmed_prev14d':None,
               'planned_or_confirmed_next7d':None, 'actual_rest_hours':None,
               'actual_travel_km':None, 'previous_to_target_venue_great_circle_km':None,
               'travel_status':'MISSING_VERIFIED_STADIUM_COORDINATES',
               'previous_window_left_censored':None, 'next_window_right_censored':None}
        candidates = [p for p in payloads if p.get('team_id') == team]
        if len(candidates) > 1:
            raise ValueError('AMBIGUOUS_TEAM_CALENDAR')
        if not candidates:
            result.append(row)
            continue
        try:
            matches = _calendar(candidates[0], target, team, cutoff)
        except (ValueError, KeyError, TypeError) as error:
            row['status'] = str(error) if isinstance(error, ValueError) else 'INVALID_CALENDAR_SCHEMA'
            result.append(row)
            continue
        active = sorted((r for r in matches if r['status'] in ACTIVE), key=lambda r:r['kickoff'])
        times = [r['kickoff'] for r in active]
        if len(times) != len(set(times)):
            row['status'] = 'CONFLICTING_SIMULTANEOUS_FIXTURES'
            result.append(row)
            continue
        kickoff = utc(target['kickoff'])
        before = [r for r in active if utc(r['kickoff']) < kickoff]
        after = [r for r in active if utc(r['kickoff']) > kickoff]
        previous, following = (before[-1] if before else None), (after[0] if after else None)
        recent = lambda r,n,k=kickoff: k-timedelta(days=n) <= utc(r['kickoff']) < k
        row.update(status='AVAILABLE_SHADOW', previous_fixture=previous, next_fixture=following,
            previous_kickoff_gap_hours=(kickoff-utc(previous['kickoff'])).total_seconds()/3600 if previous else None,
            next_kickoff_gap_hours=(utc(following['kickoff'])-kickoff).total_seconds()/3600 if following else None,
            previous_window_left_censored=previous is None, next_window_right_censored=following is None,
            confirmed_played_prev7d_lower_bound=sum(recent(r,7) and r['status'] in {'finished','live'} for r in before),
            planned_or_confirmed_prev7d=sum(recent(r,7) for r in before),
            planned_or_confirmed_prev14d=sum(recent(r,14) for r in before),
            planned_or_confirmed_next7d=sum(utc(r['kickoff']) < kickoff+timedelta(days=7) for r in after))
        row.update(previous_kickoff_gap_days=row['previous_kickoff_gap_hours']/24 if previous else None,
                   next_kickoff_gap_days=row['next_kickoff_gap_hours']/24 if following else None,
                   unconfirmed_past_scheduled_count=sum(r['status']=='scheduled' and utc(r['kickoff']) <= cutoff for r in before),
                   future_planned_before_target_count=sum(r['status']=='scheduled' and utc(r['kickoff']) > cutoff for r in before))
        if previous:
            linked_target = next(r for r in matches if r['fixture_id']==target['fixture_id'])
            target_venue = linked_target.get('venue_id')
            if target.get('venue_id') is not None and target.get('venue_id') != target_venue:
                target_venue = None  # Caller metadata cannot override a verified calendar's venue.
            first, second = positions.get(previous.get('venue_id')), positions.get(target_venue)
            def exact_position(match):
                location=match_positions.get(match['fixture_id'])
                if location is None:return None
                return location if all(location[k]==match[k] for k in (
                    'competition_id','season_id','home_team_id','away_team_id','kickoff','status')) else None
            first=exact_position(previous) or first
            second=exact_position(linked_target) or second
            if first is not None and second is not None:
                row.update(previous_to_target_venue_great_circle_km=_distance(first,second),
                           travel_status='VERIFIED_VENUE_DISTANCE_PROXY_NOT_TEAM_ITINERARY')
        result.append(row)
    return {'version':VERSION, 'mode':'SHADOW', 'as_of':cutoff.isoformat(),
            'fixture_id':target['fixture_id'], 'live_decision_effect':False, 'teams':result,
            'units':'UTC_KICKOFF_GAPS_HOURS_AND_VENUE_GREAT_CIRCLE_KM_NOT_ACTUAL_REST_OR_TRAVEL',
            'dataset':snapshot([target, *payloads, *venue_rows], role='team_schedule_features', as_of=cutoff,
                recipe={'version':VERSION, 'window_days':WINDOW_DAYS, 'max_age_hours':MAX_AGE_HOURS},
                lineage=[*calendars, *venue_envelopes])}
