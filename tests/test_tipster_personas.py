"""Persona differentiation and price, provenance, temporal and disclosure boundaries."""

import json
import math
import socket
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

import goal_analysis.tipsters.recommendations as recommendation_module
from goal_analysis.agents import canonical_sha256
from goal_analysis.tipsters import build_tipster_recommendations
from goal_analysis.tipsters.catalog import TIPSTER_CATALOG, TIPSTER_PROFILE_IDS, get_catalogue
from goal_analysis.tipsters.recommendations import PRODUCTS

NOW = datetime(2026, 10, 11, 10, tzinfo=UTC)
HISTORY = "a" * 64


def fixture(n=1):
    return {"fixture_id": f"api_football:{n}", "api_football_fixture_id": str(n),
            "api_football_home_team_id": str(n * 10), "api_football_away_team_id": str(n * 10 + 1),
            "home_team": f"Hazai {n}", "away_team": f"Vendeg {n}",
            "competition": f"league_{n % 3}", "sport_key": "soccer_test", "provider_status": "NS",
            "kickoff": (NOW + timedelta(hours=6 + n / 10)).isoformat(), "target_date": "2026-10-11"}


def quote(n=1, key="home", price=2.0, book="test_book", provider="the_odds_api", **changes):
    market, selection = PRODUCTS[key]
    item = {**fixture(n), "market_key": market, "selection_key": selection, "period": "FULL_TIME",
            "event_status": "SCHEDULED", "decimal_price": price, "bookmaker": book,
            "region": "eu", "currency": "EUR", "quote_available": True,
            "quote_provider": provider, "quoted_at": NOW.isoformat(),
            "quote_source_id": f"https://api.the-odds-api.com/v4/sports/soccer_test/events/event{n}/odds"}
    if provider == "api_football":
        item["bookmaker"] = "test_api_football_8"
        bet = {"h2h": 1, "totals_2_5": 5, "btts": 8}[market]
        item.update(provider_market_id=bet,
                    quote_source_id=f"https://v3.football.api-sports.io/odds?fixture={n}&bookmaker=8&bet={bet}")
    item["candidate_id"] = "d223-" + canonical_sha256({
        "fixture_id": item["fixture_id"], "event_id": f"event{n}", "market": market,
        "selection": selection, "period": "FULL_TIME", "bookmaker": item["bookmaker"]})[:24]
    item["evidence"] = [{"evidence_id": f"history-v1:{item['candidate_id']}:{category}",
                         "category": category, "source_id": f"history-sha256:{HISTORY}",
                         "observed_at": (NOW - timedelta(days=1)).isoformat(),
                         "strength": 0.65, "reliability": 0.8} for category in ("historical", "venue_form")]
    item.update(changes)
    return item


def model_results(count=8):
    result = {}
    for source in ("gauss", "kepler", "laplace"):
        predictions = []
        for n in range(1, count + 1):
            shift = (n % 3) * 0.005 + (0.01 if source == "kepler" else 0)
            values = {"home": 0.5 + shift, "draw": 0.25, "away": 0.25 - shift,
                      "over25": 0.6, "under25": 0.4, "btts_yes": 0.55}
            if source == "kepler":
                values = {key: values[key] for key in ("home", "draw", "away")}
            predictions.append({"fixture_id": fixture(n)["fixture_id"], "probabilities": values,
                                "stability": dict.fromkeys(values, 0.03) if source == "gauss" else {},
                                "model_details": {"cutoff": NOW.isoformat(), "history_hash": HISTORY,
                                                  "training_count": 100, "calibration": "unvalidated"}})
        result[source] = {"profile_id": source, "status": "READY", "predictions": predictions}
    return result


def enriched(count=8, *, keys=None, provider="the_odds_api"):
    prices = {"home": 2.0, "draw": 3.3, "away": 3.0, "over25": 2.05,
              "under25": 1.95, "btts_yes": 1.8, "btts_no": 1.95}
    return {"date": "2026-10-11", "fixtures": [fixture(n) for n in range(1, count + 1)],
            "history_analysis": {"snapshot_sha256": HISTORY},
            "candidates": [quote(n, key, prices[key], provider=provider)
                           for n in range(1, count + 1) for key in (keys or prices)]}


def build(data=None, models=None, enabled=None, now=NOW):
    return build_tipster_recommendations(
        data if data is not None else enriched(), model_results() if models is None else models,
        {"enabled_profiles": list(TIPSTER_PROFILE_IDS) if enabled is None else enabled}, now)


@pytest.fixture
def isolated_single_persona(monkeypatch):
    """Isolate candidate gates from the production multi-leg construction rule.

    Each caller explicitly identifies the persona(s) under test. Only this
    module's private deep-copy is patched, and pytest restores it after the test.
    The production catalogue and all other personas retain their actual policy.
    """
    def configure(*profile_ids, maximum=None):
        catalogue = deepcopy(recommendation_module.TIPSTER_CATALOG)
        for persona in catalogue:
            if persona["profile_id"] not in profile_ids:
                continue
            persona.update(min_legs=1, max_legs=1, preferred_legs=1,
                           target_odds_minimum_enforced=False)
            persona["target_odds_band"]["min"] = None
            if maximum is not None:
                persona["target_odds_band"]["max"] = maximum
        monkeypatch.setattr(recommendation_module, "TIPSTER_CATALOG", catalogue)
    return configure


def test_catalogue_exactly_twenty_distinct_fictional_meaningful_policies():
    assert len(TIPSTER_PROFILE_IDS) == len(set(TIPSTER_PROFILE_IDS)) == 20
    assert all(p.startswith("tipster_") and p.isascii() for p in TIPSTER_PROFILE_IDS)
    assert len({p["name"] for p in TIPSTER_CATALOG}) == 20
    assert len({canonical_sha256(p["selection_policy"]) for p in TIPSTER_CATALOG}) == 20
    for persona in TIPSTER_CATALOG:
        assert persona["fictional"] is True
        assert 10 <= persona["target_odds_band"]["min"] <= 25
        assert persona["reference_odds_band"] == persona["target_odds_band"]
        assert persona["target_odds_band"]["min"] < persona["target_odds_band"]["max"] <= 50
        assert persona["target_odds_minimum_enforced"] is True
        assert 2 <= persona["min_legs"] <= persona["preferred_legs"] <= persona["max_legs"] <= 6
        assert persona["source_style"]["implementation"] == "LOCAL_ADAPTATION_NO_DOWNLOADED_RUNTIME_CODE"
        assert persona["source_style"]["sources"]
        assert all(source["license"] == "MIT" for source in persona["source_style"]["sources"])
    detached = get_catalogue()
    detached[0]["name"] = "changed"
    assert detached != TIPSTER_CATALOG
    json.dumps(TIPSTER_CATALOG, allow_nan=False)


def test_all_twenty_real_priced_combinations_distinct_fixtures_and_honest_schema():
    data, models = enriched(), model_results()
    original = deepcopy([data, models])
    cards = build(data, models)
    assert len(cards) == 20
    ready = [card for card in cards if card["status"] == "READY"]
    assert ready
    assert len({card["recommendation_id"] for card in ready}) == len(ready)
    for card in cards:
        assert card["probability"] is card["stake_eur"] is card["portfolio_ticket_id"] is None
        assert card["confidence"] == "LOW" and not card["playable"] and not card["selected_for_portfolio"]
        assert card["method_kind"] == "SHARED_EVIDENCE_PERSONA"
        assert card["source_style"] == card["persona"]["source_style"]
        assert card["model_summary"]["additional_ai_calls"] == card["model_summary"]["additional_model_fits"] == 0
        if not card["legs"]:
            assert card["status"] == "ABSTAINED"
            assert card["reason_code"] in {"INSUFFICIENT_EVIDENCE", "NO_COMPLIANT_COMBINATION"}
            continue
        band = card["persona"]["target_odds_band"]
        assert band["min"] <= card["combined_price"] <= band["max"]
        assert card["persona"]["min_legs"] <= len(card["legs"]) <= card["persona"]["max_legs"]
        assert card["combined_price"] == math.prod(leg["decimal_price"] for leg in card["legs"])
        assert len({leg["fixture_id"] for leg in card["legs"]}) == len(card["legs"])
        assert len({(leg["bookmaker"], leg["region"], leg["currency"]) for leg in card["legs"]}) == 1
        for leg in card["legs"]:
            assert leg["source_candidate_id"] == leg["quote_candidate_id"]
            source = next(c for c in data["candidates"] if c["candidate_id"] == leg["source_candidate_id"])
            assert leg["decimal_price"] == source["decimal_price"]
            assert leg["quote_source_id"] == source["quote_source_id"]
    assert [data, models] == original
    json.dumps(cards, allow_nan=False)


def test_personalities_keep_market_methods_within_their_fixed_ticket_bands():
    cards = {card["profile_id"]: card for card in build()}
    for profile, market, selection in (("tipster_golvadasz_lili", "totals_2_5", "over"),
                                       ("tipster_csendes_mira", "totals_2_5", "under"),
                                       ("tipster_dupla_aron", "btts", "yes"),
                                       ("tipster_remi_doma", "h2h", "draw"),
                                       ("tipster_hazai_mark", "h2h", "home"),
                                       ("tipster_vandor_zora", "h2h", "away")):
        assert all((leg["market_key"], leg["selection_key"]) == (market, selection)
                   for leg in cards[profile]["legs"])
    assert any(len(card["legs"]) > 1 for card in cards.values())
    assert cards["tipster_meresz_hunor"]["status"] == "ABSTAINED"
    if cards["tipster_fek_tade"]["legs"]:
        assert cards["tipster_fek_tade"]["combined_price"] <= cards["tipster_fek_tade"]["persona"]["target_odds_band"]["max"]


def test_disabled_and_non_persona_profiles_do_not_generate_cards():
    assert build(enabled=[]) == []
    assert build(enabled=["gauss", "daily223", "not_real"]) == []
    assert len(build(enabled=[TIPSTER_PROFILE_IDS[0]] * 2)) == 1


@pytest.mark.parametrize("field,value", [
    ("decimal_price", None), ("decimal_price", 1), ("decimal_price", True), ("decimal_price", float("nan")),
    ("home_team", "Wrong team"), ("event_status", "LIVE"), ("quote_available", False),
    ("period", "FIRST_HALF"), ("api_football_fixture_id", "999"), ("region", ""),
    ("quote_provider", "untrusted"), ("quote_source_id", "https://evil.test/odds"),
    ("candidate_id", "d223-not-the-real-source-id"),
])
def test_invalid_quotes_cannot_become_a_ticket(field, value):
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate[field] = value
    assert all(card["reason_code"] == "NO_VALID_QUOTES" and not card["legs"] for card in build(data))


@pytest.mark.parametrize("stamp", [None, "broken", "2000-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"])
def test_quote_timestamp_never_vetoes_or_changes_selection(stamp):
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
    first = build(data)
    assert any(card["legs"] for card in first)
    for quote_item in data["candidates"]:
        quote_item["quoted_at"] = stamp
    second = build(data)
    assert [(c.get("recommendation_id"), c["combined_price"], c["status"], c["confidence"]) for c in first] == [
        (c.get("recommendation_id"), c["combined_price"], c["status"], c["confidence"]) for c in second]


def test_missing_quote_available_flag_is_not_a_veto():
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
        candidate.pop("quote_available")
    assert any(c["legs"] for c in build(data))


@pytest.mark.parametrize("provider", ["the_odds_api", "api_football"])
def test_exact_fixture_market_source_provenance_is_enforced(provider):
    data = enriched(keys=["home"], provider=provider)
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
    assert any(c["legs"] for c in build(data))
    for candidate in data["candidates"]:
        if provider == "the_odds_api":
            candidate["quote_source_id"] = candidate["quote_source_id"].replace("/event", "/unmatched")
        else:
            candidate["quote_source_id"] = candidate["quote_source_id"].replace("bet=1", "bet=8")
            candidate["provider_market_id"] = 8
    assert all(c["reason_code"] == "NO_VALID_QUOTES" for c in build(data))


def test_separate_region_currency_quotes_abstain_without_a_complete_same_cohort_ticket():
    data = enriched(count=3, keys=["home"])
    for n, candidate in enumerate(data["candidates"]):
        candidate["region"] = f"region{n}"
    cards = build(data)
    assert all(not c["legs"] and c["status"] == "ABSTAINED" for c in cards)


def test_cutoff_before_kickoff_and_target_day_duplicate_fixture_fail_closed():
    data = enriched()
    assert all(c["reason_code"] == "NO_FIXTURES" for c in build(data, now=NOW + timedelta(days=1)))
    for item in data["fixtures"]:
        item["kickoff"] = NOW.isoformat()
    assert all(c["reason_code"] == "NO_FIXTURES" for c in build(data))
    data = enriched()
    data["fixtures"] *= 2
    assert all(c["reason_code"] == "NO_FIXTURES" for c in build(data))
    with pytest.raises(ValueError):
        build(now=NOW.replace(tzinfo=None))


def test_missing_evidence_and_models_abstain_instead_of_inventing_probability():
    data = enriched()
    for candidate in data["candidates"]:
        candidate["evidence"] = []
        candidate["support_score"] = 999
        candidate["model_probability"] = 0.999
    assert all(c["reason_code"] == "INSUFFICIENT_EVIDENCE" for c in build(data, {}))
    assert all(c["reason_code"] == "NO_FIXTURES" for c in build({}, {}))


def test_verified_history_only_abstains_without_a_model_value_estimate():
    cards = build(models={})
    assert all(not c["legs"] and c["status"] == "ABSTAINED" for c in cards)


def test_future_or_unbound_history_cannot_support_selection():
    data = enriched()
    for candidate in data["candidates"]:
        for evidence in candidate["evidence"]:
            evidence["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    assert all(not c["legs"] for c in build(data, {}))
    data = enriched()
    data["history_analysis"]["snapshot_sha256"] = "b" * 64
    assert all(not c["legs"] for c in build(data, {}))


def test_future_model_cutoff_invalid_probabilities_and_duplicate_prediction_not_used():
    data, models = enriched(), model_results()
    for candidate in data["candidates"]:
        candidate["evidence"] = []
    for result in models.values():
        for prediction in result["predictions"]:
            prediction["model_details"]["cutoff"] = (NOW + timedelta(seconds=1)).isoformat()
    assert all(not c["legs"] for c in build(data, models))
    models = model_results()
    for result in models.values():
        result["predictions"] *= 2
    assert all(not c["legs"] for c in build(data, models))
    models = model_results()
    for result in models.values():
        for prediction in result["predictions"]:
            prediction["probabilities"] = {"home": 0.99, "draw": 0.99, "away": 0.99}
    assert all(not c["legs"] for c in build(data, models))


def test_ensemble_weights_are_preserved_and_not_calibrated_or_multiplied():
    models = model_results()
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
    card = build(data, models, ["tipster_hazai_mark"])[0]
    assert card["probability"] is None and card["legs"]
    for leg in card["legs"]:
        details = leg["model_details"]
        assert details["calibration"] == "unvalidated"
        expected = sum(p * details["shared_model_weights"][source]
                       for source, p in details["shared_model_probabilities"].items())
        assert leg["model_probability"] == pytest.approx(expected)
        assert leg["estimated_edge"] == pytest.approx(expected * leg["decimal_price"] - 1)


def test_consensus_rejects_large_model_disagreement_and_single_source():
    data, models = enriched(keys=["home"]), model_results()
    for prediction in models["kepler"]["predictions"]:
        prediction["probabilities"] = {"home": 0.8, "draw": 0.1, "away": 0.1}
    enabled = ["tipster_konszenzus_vera", "tipster_stabil_irma"]
    assert all(not c["legs"] for c in build(data, models, enabled))
    assert all(not c["legs"] for c in build(data, {"kepler": models["kepler"]}, enabled))


def test_deterministic_order_no_network_or_extra_model_fits(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Personas must not make network calls")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    data, models = enriched(), model_results()
    first = build(data, models)
    data["candidates"].reverse()
    data["fixtures"].reverse()
    for result in models.values():
        result["predictions"].reverse()
    assert build(data, models) == first


def test_long_history_and_recent_venue_personalities_choose_different_evidence():
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
        earlier = int(candidate["api_football_fixture_id"]) <= 4
        for evidence in candidate["evidence"]:
            favoured = (evidence["category"] == "historical") == earlier
            evidence.update(strength=0.9 if favoured else 0.1, reliability=1.0)
    cards = {c["profile_id"]: c for c in build(data, model_results(),
                                            ["tipster_archiv_nora", "tipster_forma_soma"])}
    history_ids = {int(leg["api_football_fixture_id"]) for leg in cards["tipster_archiv_nora"]["legs"]}
    venue_ids = {int(leg["api_football_fixture_id"]) for leg in cards["tipster_forma_soma"]["legs"]}
    assert history_ids and history_ids <= {1, 2, 3, 4}
    assert venue_ids and venue_ids <= {5, 6, 7, 8}


def test_low_prices_without_positive_value_are_not_used_as_padding():
    data = enriched(count=1, keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 1.01
    cards = build(data)
    assert all(not c["legs"] for c in cards)
    single_fixture = build(enriched(count=1))
    assert all(not c["legs"] and c["status"] == "ABSTAINED" for c in single_fixture)


def test_malformed_probabilities_force_abstention_instead_of_history_only_fallback():
    models = model_results()
    for result in models.values():
        for prediction in result["predictions"]:
            prediction["probabilities"] = {"home": float("nan"), "draw": True, "away": 4.0}
    cards = build(models=models)
    assert all(not c["legs"] for c in cards)


def test_original_probability_and_persona_score_are_distinct_fields():
    data, models = enriched(keys=["home"]), model_results()
    original = deepcopy(models)
    for candidate in data["candidates"]:
        candidate.update(decimal_price=2.2, support_score=1000000, model_probability=1.0,
                         astra_review={"assessment": "support", "model_probability": 1.0})
    card = build(data, models, ["tipster_hazai_mark"])[0]
    assert card["legs"] and models == original
    assert all(0.5 <= leg["model_probability"] < 0.53 for leg in card["legs"])
    assert all(0 <= leg["model_details"]["selection_score"] <= 1 for leg in card["legs"])
    assert all(leg["model_details"]["selection_score_kind"] == "HEURISTIC_NOT_PROBABILITY" for leg in card["legs"])


def test_model_btts_complement_is_labeled_and_not_a_new_fit():
    data = enriched(keys=["btts_no"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.4
    card = build(data, enabled=["tipster_csendes_mira"])[0]
    assert card["legs"]
    assert all(leg["model_probability"] == pytest.approx(0.45) for leg in card["legs"])
    assert all(leg["model_details"]["derived_complement"] is True for leg in card["legs"])


def test_stated_market_specialties_never_fill_odds_target_with_other_markets():
    # Every price and model is valid, but the only priced product is an opposite
    # market. A soft bonus used to permit these filler tickets.
    for profile, wrong in (("tipster_golvadasz_lili", "under25"),
                           ("tipster_hazai_mark", "away"),
                           ("tipster_vandor_zora", "home"),
                           ("tipster_remi_doma", "home"),
                           ("tipster_csendes_mira", "btts_yes"),
                           ("tipster_dupla_aron", "btts_no"),
                           ("tipster_golterkep_emese", "home"),
                           ("tipster_pajzs_dorka", "btts_yes"),
                           ("tipster_roham_levi", "under25")):
        card = build(enriched(keys=[wrong]), enabled=[profile])[0]
        assert card["status"] == "ABSTAINED" and not card["legs"]


def test_specialist_abstains_when_its_own_market_cannot_form_the_declared_ticket():
    data = enriched(keys=["home"])
    data["candidates"].extend(quote(n, "under25", 2.7) for n in (1, 2))
    card = build(data, enabled=["tipster_csendes_mira"])[0]
    assert card["status"] == "ABSTAINED" and not card["legs"]
    assert card["reason_code"] == "NO_COMPLIANT_COMBINATION"


def test_selection_diagnostics_disclose_shared_coverage_and_actual_overlap():
    data, models = enriched(keys=["home"]), model_results()
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
    for model in models.values():
        model["predictions"] = model["predictions"][:4]
    cards = build(data, models, ["tipster_hazai_mark", "tipster_archiv_nora"])
    published = [card for card in cards if card["legs"]]
    counts = {}
    for card in published:
        for leg in card["legs"]:
            counts[leg["fixture_id"]] = counts.get(leg["fixture_id"], 0) + 1
    for card in cards:
        details = card["selection_diagnostics"]
        assert details["input_fixture_count"] == details["verified_quote_fixture_count"] == 8
        assert details["shared_model_fixture_count"] == 4
        assert details["club_selected_fixture_count"] == len(counts)
        assert details["club_max_fixture_profile_count"] == max(counts.values())
        assert details["club_max_fixture_profile_share"] == max(counts.values()) / len(published)
        assert details["selected_fixture_ids"] == sorted(leg["fixture_id"] for leg in card["legs"])
        assert details["selected_fixture_profile_counts"] == {
            leg["fixture_id"]: counts[leg["fixture_id"]] for leg in card["legs"]}


def test_duplicate_exposure_is_disclosed_without_forced_selection_changes(isolated_single_persona):
    # Single-leg construction isolates duplicate reporting from band compliance.
    isolated_single_persona("tipster_bastya_bence", "tipster_stabil_irma")
    data = enriched(count=4, keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 2.2
    cards = build(data, model_results(4), ["tipster_bastya_bence", "tipster_stabil_irma"])
    first_ids = {leg["fixture_id"] for leg in cards[0]["legs"]}
    second_ids = {leg["fixture_id"] for leg in cards[1]["legs"]}
    assert first_ids == second_ids and len(first_ids) == 1
    for card in cards:
        assert card["selection_diagnostics"]["duplicate_exposure_profile_ids"] == [
            c["profile_id"] for c in cards]


@pytest.mark.parametrize("profile", ["tipster_bastya_bence", "tipster_dupla_aron",
                                    "tipster_fek_tade", "tipster_remi_doma",
                                    "tipster_vandor_zora", "tipster_forma_soma",
                                    "tipster_golterkep_emese"])
def test_personas_promising_model_estimates_abstain_when_models_are_missing(profile):
    card = build(models={}, enabled=[profile])[0]
    assert not card["legs"] and card["reason_code"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("profile,missing", [("tipster_vandor_zora", "kepler"),
                                           ("tipster_forma_soma", "kepler"),
                                           ("tipster_golterkep_emese", "gauss"),
                                           ("tipster_golterkep_emese", "laplace")])
def test_named_model_method_cannot_be_replaced_by_another_source(profile, missing):
    models = model_results()
    models.pop(missing)
    card = build(models=models, enabled=[profile])[0]
    assert not card["legs"] and card["reason_code"] == "INSUFFICIENT_EVIDENCE"


def test_negative_estimated_edge_is_rejected_before_ticket_construction():
    data = enriched(keys=["home"])
    for candidate in data["candidates"]:
        candidate["decimal_price"] = 1.8
    card = build(data, enabled=["tipster_bastya_bence"])[0]
    assert not card["legs"] and card["status"] == "ABSTAINED"
