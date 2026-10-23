"""Point-in-time descriptive xG, venue separation and opponent adjustment."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from goal_analysis.models.xg_rates import fit_xg_rates
from goal_analysis.normalization.leakage import LookAheadError
from goal_analysis.normalization.training_datasets import sha

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def envelope(number, home, away, hx=1.6, ax=1.1, days=2, **extra):
    payload = {"fixture_id": f"mt_{number}", "provider": "the_stats_api",
        "competition_id": "comp_1", "season_id": "sn_1", "home_team_id": f"tm_{home}",
        "away_team_id": f"tm_{away}", "home_xg": hx, "away_xg": ax, "status": "FT",
        "neutral_venue": False, "scope": "REGULATION_FIRST_PLUS_SECOND_HALF",
        "kickoff": (NOW-timedelta(days=days)).isoformat(),
        "observed_at": (NOW-timedelta(hours=2)).isoformat(),
        "available_at": (NOW-timedelta(hours=1)).isoformat(), **extra}
    times = {"observed_at": payload["observed_at"], "available_at": payload["available_at"],
        "published_at": None, "ingested_at": None, "availability_basis": "SOURCE_PRESENCE_UPPER_BOUND",
        "event_time": payload["kickoff"], "event_time_kind": "KICKOFF"}
    return {"record_id": sha(payload), "payload": payload, "payload_sha256": sha(payload),
            "source_sha256": "a"*64, "receipt_id": "b"*64, "times": times}


def complete():
    return [envelope(i+1, h, a, days=i+1) for i, (h, a) in enumerate(
        (h, a) for h in range(1, 5) for a in range(1, 5) if h != a)]


def fit(values=None, **kwargs):
    return fit_xg_rates(complete() if values is None else values, as_of=NOW, **kwargs)


def test_constant_schedule_preserves_known_distinct_home_away_rates():
    r = fit(); league = r['leagues'][0]
    assert r['mode'] == 'SHADOW' and not r['is_probability_model'] and not r['live_decision_effect']
    assert league['league_home_xg'] == pytest.approx(1.6)
    assert league['league_away_xg'] == pytest.approx(1.1)
    for team in league['teams']:
        assert team['home']['status'] == team['away']['status'] == 'AVAILABLE_SHADOW'
        assert team['home']['attack_xg_per_regulation_match'] == pytest.approx(1.6)
        assert team['away']['attack_xg_per_regulation_match'] == pytest.approx(1.1)
        assert team['home']['conceded_xg_per_regulation_match'] == pytest.approx(1.1)


def test_same_team_xg_is_discounted_against_weak_opponent_defense():
    def schedule(y):
        return ([envelope(i+1, 1, 2, 2, 1) for i in range(3)]
            + [envelope(i+4, 2, 3, 2, y) for i in range(3)]
            + [envelope(i+7, 4, 5, 2, 2-y) for i in range(3)])
    strong, weak = fit(schedule(.5))['leagues'][0], fit(schedule(1.5))['leagues'][0]
    assert strong['league_home_xg'] == weak['league_home_xg'] == 2
    assert strong['league_away_xg'] == weak['league_away_xg'] == 1
    first = lambda league: next(t for t in league['teams'] if t['team_id'] == 'tm_1')['home']
    assert first(strong)['attack_xg_per_regulation_match'] > 2
    assert first(weak)['attack_xg_per_regulation_match'] < 2


def test_time_decay_downweights_old_high_xg_without_changing_the_observation():
    rows = [envelope(1, 1, 2, 10, 1, days=90), envelope(2, 2, 3, 0, 1, days=1),
            envelope(3, 3, 1, 0, 1, days=2)]
    r = fit(rows)
    assert r['leagues'][0]['league_home_xg'] == pytest.approx(5/(.5 + 2**(-1/90)+2**(-2/90)))
    assert r['dataset']['rows'][0]['home_xg'] == 10


def test_missing_role_sample_is_none_and_does_not_remove_the_other_role():
    rows = complete(); rows = [r for r in rows if r['payload']['fixture_id'] != 'mt_1']
    team = next(t for t in fit(rows)['leagues'][0]['teams'] if t['team_id']=='tm_1')
    assert team['home']['status']=='INSUFFICIENT_XG_HISTORY'
    assert team['home']['attack_xg_per_regulation_match'] is None
    assert team['away']['status']=='AVAILABLE_SHADOW'


def test_opponent_with_no_other_history_is_explicit_gap_not_measured_average():
    rows = [envelope(i, 1, i+1) for i in (1, 2, 3)]
    team = next(t for t in fit(rows)['leagues'][0]['teams'] if t['team_id']=='tm_1')
    assert team['home']['opponents_with_no_other_history']==3
    assert team['home']['attack_strength'] is None


def test_order_and_input_objects_are_stable():
    rows = complete(); before = deepcopy(rows)
    assert fit(rows) == fit(list(reversed(rows)))
    assert rows == before


def test_competition_and_season_pools_do_not_mix():
    rows = complete()
    other = [envelope(i+100, r['payload']['home_team_id'][3:], r['payload']['away_team_id'][3:],
                      5, 4, competition_id='comp_2') for i, r in enumerate(rows)]
    assert fit(rows+other)['leagues'][0] == fit(rows)['leagues'][0]


@pytest.mark.parametrize('change', [{'home_xg':None}, {'home_xg':-.1}, {'home_xg':True},
    {'scope':'PROVIDER_ALL_EXTRA_TIME_SCOPE_UNKNOWN'}, {'neutral_venue':None}, {'status':'AET'}])
def test_invalid_or_unscoped_xg_is_not_imputed(change):
    r = fit([envelope(1, 1, 2, **change)])
    assert r['accepted_matches']==0 and r['issues'] and r['leagues']==[]


@pytest.mark.parametrize('field', ['observed_at', 'available_at'])
def test_future_knowledge_fails_before_any_estimation(field):
    e = envelope(1, 1, 2, **{field:(NOW+timedelta(seconds=1)).isoformat()})
    with pytest.raises(LookAheadError, match='FUTURE'):
        fit([e])


def test_missing_witness_or_tampered_payload_hard_fails():
    e = envelope(1, 1, 2); e['times']['available_at']=None
    with pytest.raises(LookAheadError, match='UNPROVEN_AVAILABLE'):
        fit([e])
    e = envelope(1, 1, 2); e['payload']['home_xg']=2
    with pytest.raises(LookAheadError, match='PAYLOAD_HASH'):
        fit([e])


def test_duplicate_match_versions_are_not_double_weighted_or_arbitrarily_chosen():
    with pytest.raises(ValueError, match='DUPLICATE_XG_MATCH'):
        fit([envelope(1,1,2),envelope(1,1,2,hx=2)])


def test_zero_xg_stays_zero_but_normalization_with_zero_baseline_is_unavailable():
    r = fit([envelope(i,i,i+1,hx=0,ax=0) for i in (1,2,3)])
    assert r['accepted_matches']==3 and r['leagues'][0]['league_home_xg']==0
    assert r['leagues'][0]['status']=='INSUFFICIENT_LEAGUE_XG_OR_ZERO_BASELINE'


def test_ancient_or_numerically_negligible_exposure_does_not_become_ready():
    assert fit([envelope(1,1,2,days=366)])['accepted_matches']==0
    r=fit([envelope(i,i,i+1,days=3000) for i in (1,2,3)],recipe={'half_life_days':1,'lookback_days':3650})
    assert r['accepted_matches']==0 and r['issues']['NUMERICAL_TIME_WEIGHT_UNDERFLOW']==3


def test_extreme_time_decay_cannot_publish_infinite_normalized_strengths():
    r=fit([envelope(1,1,2,hx=20,days=1060),envelope(2,2,3,hx=0,days=1),
           envelope(3,3,1,hx=0,days=2)],recipe={'half_life_days':1,'lookback_days':1200})
    assert r['leagues'][0]['status']=='NUMERICALLY_UNSTABLE_BASELINE'
    assert r['leagues'][0]['teams']==[]


@pytest.mark.parametrize('recipe',[{'prior_weight':0},{'half_life_days':True},{'min_matches':2},
    {'unknown':1},{'lookback_days':float('nan')}])
def test_invalid_recipe_is_not_silently_defaulted(recipe):
    with pytest.raises(ValueError):
        fit(recipe=recipe)
