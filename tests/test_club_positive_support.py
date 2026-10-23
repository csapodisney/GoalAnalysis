"""Primary/alternate parity; synthetic prices/models are never API calls."""

from copy import deepcopy

import pytest
from test_tipster_personas import NOW, enriched, model_results

from goal_analysis.tipsters.recommendations import _positive_support, build_tipster_recommendations

PROFILE = "tipster_archiv_nora"


def inputs(count=3, weak=3):
    data, models = enriched(count=count, keys=["home"]), model_results(count)
    weak_ids = {f"api_football:{n}" for n in range(count - weak + 1, count + 1)}
    for candidate in data["candidates"]:
        candidate.update(decimal_price=2.6, evidence=[])
    for source, model in models.items():
        for prediction in model["predictions"]:
            if prediction["fixture_id"] in weak_ids:
                p = 0.99 if source == "gauss" else 0.01
                prediction["probabilities"] = {"home": p, "draw": (1 - p) / 2, "away": (1 - p) / 2}
    return data, models


def build(data, models, mode):
    options = {}
    cards = build_tipster_recommendations(data, models, {"enabled_profiles": [PROFILE]}, NOW,
                                         support_guard_mode=mode, diversity_options=options)
    return cards[0], options[PROFILE]


@pytest.mark.parametrize("weak", [1, 2, 3])
def test_cannot_pad_fixed_three_leg_band_with_zero_support(weak):
    data, models = inputs(weak=weak)
    original = deepcopy([data, models])
    legacy, old_options = build(data, models, "SHADOW")
    fixed, options = build(data, models, "LIVE")
    assert legacy["status"] == "READY" and legacy["combined_price"] == pytest.approx(17.576)
    assert sum(leg["model_details"]["selection_score"] == 0 for leg in legacy["legs"]) == weak
    assert fixed["status"] == "ABSTAINED" and not fixed["legs"] and not options
    assert fixed["reason_code"] == ("INSUFFICIENT_EVIDENCE" if weak == 3 else "NO_COMPLIANT_COMBINATION")
    assert fixed["model_summary"]["eligible_selections"] == 3 - weak
    assert old_options == []  # The alternative path already rejected these rows.
    assert legacy["selection_diagnostics"]["positive_support_guard"]["would_abstain"]
    assert not legacy["selection_diagnostics"]["positive_support_guard"]["primary_guard_enforced"]
    assert [data, models] == original


def test_valid_alternatives_survive_and_all_paths_retain_only_positive_support():
    data, models = inputs(count=5, weak=2)
    shadow, old = build(data, models, "SHADOW")
    fixed, options = build(data, models, "LIVE")
    assert fixed["status"] == "READY" and len(fixed["legs"]) == 3
    assert fixed["combined_price"] == pytest.approx(17.576)
    assert all(leg["model_details"]["selection_score"] > 0 for leg in fixed["legs"])
    assert options and all(leg["model_details"]["selection_score"] > 0
                           for option in options for leg in option["recommendation"]["legs"])
    key = lambda rows: [(o["quality"], o["recommendation"]["legs"]) for o in rows]
    assert key(old) == key(options)
    assert shadow["selection_diagnostics"]["positive_support_guard"]["would_select_candidate_ids"] == [
        leg["source_candidate_id"] for leg in fixed["legs"]]


def test_zero_support_fixture_does_not_displace_three_supported_fixtures():
    data, models = inputs(count=4, weak=1)
    fixed, options = build(data, models, "LIVE")
    assert fixed["status"] == "READY" and options
    assert all(leg["fixture_id"] != "api_football:4" for leg in fixed["legs"])
    assert len({leg["fixture_id"] for leg in fixed["legs"]}) == len(fixed["legs"])


def test_no_new_threshold_or_odds_reward_in_already_supported_pool():
    data, models = inputs(count=5, weak=0)
    assert build(data, models, "SHADOW") == build(data, models, "LIVE")


def test_validated_parity_repair_is_the_default_and_does_not_require_option_output():
    data, models = inputs()
    card = build_tipster_recommendations(data, models, {"enabled_profiles": [PROFILE]}, NOW)[0]
    assert card["status"] == "ABSTAINED" and not card["legs"]
    assert card["model_summary"]["eligible_selections"] == 0
    assert card["model_status"] == "SHARED_POOL_INSUFFICIENT"


@pytest.mark.parametrize("score", [0, -0.01, float("nan"), float("inf"), True, None, "0.9"])
def test_existing_finite_positive_rule_rejects_invalid_or_zero_scores(score):
    assert not _positive_support({"score": score})


@pytest.mark.parametrize("score", [1e-12, 0.01, 0.5, 1])
def test_positive_score_not_subject_to_an_invented_new_floor(score):
    assert _positive_support({"score": score})


@pytest.mark.parametrize("mode", ["OFF", "shadow", "", None])
def test_unknown_mode_never_silently_relaxes_guard(mode):
    data, models = inputs()
    with pytest.raises(ValueError, match="support_guard_mode"):
        build(data, models, mode)
