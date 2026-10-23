"""League measurement denominators and missing evidence must stay explicit."""
from copy import deepcopy

import pytest

from goal_analysis.jobs.odds_coverage import measure_odds_coverage
from goal_analysis.normalization.odds_snapshots import (
    normalize_odds_snapshot,
    normalize_saved_quotes,
)

RECEIPT = "2026-10-21T20:00:00+00:00"
CUTOFF = "2026-10-21T20:30:00+00:00"
FUTURE = "2026-10-21T20:31:00+00:00"
URL = "https://api.thestatsapi.com/api/football/matches/mt_1/odds"
SHA = "a" * 64


def normalized(markets=None):
    return normalize_odds_snapshot("the_stats_api", {"data": {"match_id": "mt_1", "bookmakers": [
        {"bookmaker": "Book A", "markets": markets or {"match_odds": {
            "home": {"last_seen": "2.1", "opening": "2.2"},
            "draw": {"last_seen": "3.2"}, "away": {"last_seen": "3.4"}}}}]}},
        observed_at=RECEIPT, source_url=URL, source_sha256=SHA)["records"]


def frame(records=None, **extra):
    return {"provider": "the_stats_api", "fixture_id": "mt_1", "observed_at": RECEIPT,
            "source_sha256": SHA, "records": normalized() if records is None else records, **extra}


def inputs():
    return {"leagues": [{"league_id": "L1", "name": "League One"},
                         {"league_id": "L2", "name": "No scheduled events"}],
            "fixtures": [{"provider": "the_stats_api", "fixture_id": f"mt_{i}",
                          "league_id": "L1", "observed_at": RECEIPT} for i in (1, 2)],
            "frames": [frame()], "as_of": CUTOFF, "universe_scope": "FROZEN_CONFIGURED_DATE_UNIVERSE"}


def measure(**changes):
    return measure_odds_coverage(**(inputs() | changes))


def first(**changes):
    return measure(**changes)["leagues"][0]


def test_denominator_includes_unpriced_fixture_and_does_not_count_opening_as_last_seen():
    r = first()
    assert r["expected_events"] == 2 and r["responses"] == 1
    assert r["events_with_any_last_seen_price"]["ratio"] == .5
    assert r["market_coverage"]["h2h:FULL_TIME"]["ratio"] == .5
    assert r["requested_market_slots"] == {"numerator": 1, "denominator": 12, "ratio": 1/12}
    assert r["counts"]["last_seen_prices"] == 3
    assert r["counts"]["non_last_seen_records"] == 1


def test_no_fixture_is_not_100_percent_coverage():
    r = measure()["leagues"][1]
    assert r["expected_events"] == 0
    assert r["events_with_supported_price"]["ratio"] is None
    assert r["market_coverage"]["totals:FULL_TIME"]["ratio"] is None


def test_receipt_age_is_not_quote_delay_and_no_missing_error_log_is_zero():
    r = first()
    assert r["receipt_age_seconds"]["mean"] == 1800
    assert r["provider_quote_to_receipt_seconds"]["mean"] is None
    assert r["quote_delay_status"] == "NO_VALID_PROVIDER_QUOTE_CLOCK"
    assert r["http"]["error_rate"] == {"numerator": None, "denominator": None, "ratio": None}
    assert r["cross_provider_price_difference"]["value"] is None
    assert measure()["receipt_freshness_is_quote_freshness"] is False


def test_recent_receipt_boundary_and_stale_response_keeps_archival_coverage_only():
    assert first()["events_with_recent_receipt"]["numerator"] == 1
    r = first(as_of="2026-10-21T20:30:01Z")
    assert r["events_with_recent_receipt"]["numerator"] == 0
    assert r["events_with_supported_price"]["numerator"] == 1


def test_latest_empty_response_supersedes_older_prices_without_backfill():
    r = first(frames=[frame(), frame([], observed_at=CUTOFF, source_sha256="b"*64)])
    assert r["responses"] == 1
    assert r["events_with_any_last_seen_price"]["numerator"] == 0


def test_future_response_excluded_and_mixed_offset_order_is_utc():
    r = measure(frames=[frame(), frame([], observed_at=FUTURE),
                        frame([], observed_at="2026-10-21T22:10:00+02:00")])
    assert r["issues"]["FUTURE_RESPONSE"] == 1
    assert r["leagues"][0]["events_with_any_last_seen_price"]["numerator"] == 0


def test_same_time_conflicting_response_excluded_until_new_unambiguous_frame():
    ambiguous = frame([], source_sha256="b"*64)
    assert first(frames=[frame(), ambiguous])["responses"] == 0
    r = first(frames=[frame(), ambiguous, frame([], observed_at=CUTOFF)])
    assert r["responses"] == 1


def test_exact_duplicate_frame_does_not_inflate_price_or_event_count():
    assert first(frames=[frame(), deepcopy(frame())]) == first()


@pytest.mark.parametrize("change", ["fixture_id", "provider", "observed_at", "source_sha256"])
def test_record_parent_mismatch_rejected(change):
    rows = normalized()
    rows[0][change] = CUTOFF if change == "observed_at" else "wrong"
    with pytest.raises(ValueError, match="QUOTE_PARENT_MISMATCH"):
        first(frames=[frame(rows)])


def test_compact_totals_alias_stays_unmapped_and_cannot_inflate_market_coverage():
    rows = normalize_saved_quotes("the_stats_api", [{"fixture_id": "the_stats_api:mt_1",
        "quote_source_id": URL, "bookmaker": "book-a", "market_key": "totals_2_5",
        "selection_key": "over", "decimal_price": 2.1, "period": "FULL_TIME",
        "settlement": "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES"}],
        observed_at=RECEIPT, source_sha256=SHA)["records"]
    assert rows and rows[0]["mapping_status"] == "UNSUPPORTED"
    r = first(frames=[frame(rows)])
    assert r["events_with_any_last_seen_price"]["numerator"] == 1
    assert r["events_with_supported_price"]["numerator"] == 0
    assert r["market_coverage"]["totals:FULL_TIME"]["numerator"] == 0


@pytest.mark.parametrize("period,market,expected", [("FULL_TIME", "h2h", 1),
    ("FIRST_HALF", "h2h_h1", 1), ("FULL_TIME", "h2h_h1", 0)])
def test_explicit_existing_regulation_and_half_aliases_are_measured_without_rewriting_rows(period, market, expected):
    rows = [r for r in normalized() if r["price_kind"] == "LAST_SEEN"]
    for row in rows:
        row.update(settlement="REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
                   market_key=market, period=period)
    original = deepcopy(rows)
    assert first(frames=[frame(rows)])["market_coverage"]["h2h:" + period]["numerator"] == expected
    assert rows == original


@pytest.mark.parametrize("mode", ["split_books", "split_lines", "split_periods"])
def test_never_combine_incomplete_selection_sets(mode):
    rows = normalized({"total_goals": {"2.5": {"over": 2.0, "under": 1.8}}})
    assert len(rows) == 2
    if mode == "split_books":
        rows[0]["bookmaker"] = "Other"
    elif mode == "split_lines":
        rows[0]["line"] = "3.5"
    else:
        rows[0]["period"] = "FIRST_HALF"
    r = first(frames=[frame(rows)])
    assert r["market_coverage"]["totals:FULL_TIME"]["numerator"] == 0
    assert r["market_coverage"]["totals:FIRST_HALF"]["numerator"] == 0


def test_new_shadow_market_is_not_a_live_requested_market_slot():
    rows = normalized({"draw_no_bet": {"home": 2.0, "away": 1.8}})
    assert rows and rows[0]["eligibility"] == "SHADOW_ONLY"
    r = first(frames=[frame(rows)])
    assert r["events_with_supported_price"]["numerator"] == 1
    assert r["requested_market_slots"]["numerator"] == 0
    assert measure()["live_promotion"] is False


def test_cross_book_spread_is_labelled_separately_and_never_cross_provider_proof():
    rows = [r for r in normalized() if r["price_kind"] == "LAST_SEEN"]
    extra = deepcopy(rows[0]); extra.update(bookmaker="Book B", decimal_price="2.52")
    r = first(frames=[frame(rows + [extra])])
    assert r["within_provider_cross_book_relative_spread"]["mean"] == pytest.approx(.2)
    assert r["cross_provider_price_difference"]["pair_count"] == 0


def test_conflicting_price_for_one_product_in_same_response_rejected():
    rows = [r for r in normalized() if r["price_kind"] == "LAST_SEEN"]
    extra = deepcopy(rows[0]); extra["decimal_price"] = "2.5"
    with pytest.raises(ValueError, match="CONFLICTING_QUOTE"):
        first(frames=[frame(rows + [extra])])


def test_known_quote_delay_requires_quote_timestamp_not_receipt_substitution():
    rows = [r for r in normalized() if r["price_kind"] == "LAST_SEEN"]
    rows[0].update(quoted_at="2026-10-21T19:58:00Z", quote_time_status="KNOWN")
    assert first(frames=[frame(rows)])["provider_quote_to_receipt_seconds"]["mean"] == 120
    rows[0]["quoted_at"] = CUTOFF
    with pytest.raises(ValueError, match="QUOTE_CLOCK_STATUS_MISMATCH"):
        first(frames=[frame(rows)])


def test_attempt_latency_error_rate_pending_cutoff_and_no_endpoint_guessing():
    def attempt(key, **more):
        return {"attempt_id": key, "provider": "the_stats_api", "fixture_id": "mt_1",
                "started_at": RECEIPT, "finished_at": "2026-10-21T20:00:02Z",
                "http_status": 200, **more}
    r = first(attempts=[attempt("ok"), attempt("bad", http_status=429),
        attempt("timeout", http_status=None, error="TIMEOUT"), attempt("pending", finished_at=FUTURE),
        attempt("unmapped", fixture_id="unknown")])["http"]
    assert r["attempts"] == 4 and r["completed"] == 3 and r["pending_at_cutoff"] == 1
    assert r["error_rate"]["ratio"] == pytest.approx(2/3)
    assert r["completed_latency_seconds"]["mean"] == 2
    assert first(attempts=[])["http"]["error_rate"]["ratio"] is None


def test_duplicate_fixture_is_excluded_not_chosen_by_name_or_order():
    fixtures = inputs()["fixtures"]
    r = measure(fixtures=fixtures + [deepcopy(fixtures[0])])
    assert r["issues"]["DUPLICATE_FIXTURE_ID"] == 1
    assert r["leagues"][0]["expected_events"] == 1
    assert r["leagues"][0]["responses"] == 0


def test_unknown_league_naive_time_and_future_fixture_cannot_be_admitted():
    fixtures = inputs()["fixtures"]
    fixtures[0]["league_id"] = "unknown"
    with pytest.raises(ValueError, match="UNDECLARED_LEAGUE"):
        first(fixtures=fixtures)
    with pytest.raises(ValueError, match="explicit timezone"):
        first(as_of="2026-10-21T20:30:00")
    fixtures = inputs()["fixtures"]; fixtures[0]["observed_at"] = FUTURE
    assert measure(fixtures=fixtures)["issues"]["FUTURE_FIXTURE"] == 1


def test_inputs_are_not_mutated_by_measurement():
    data = inputs(); original = deepcopy(data)
    measure_odds_coverage(**data)
    assert data == original


@pytest.mark.parametrize("mode", ["duplicate", "negative_duration"])
def test_bad_attempt_telemetry_is_not_silently_used(mode):
    a = {"attempt_id": "x", "provider": "the_stats_api", "fixture_id": "mt_1",
         "started_at": RECEIPT, "finished_at": CUTOFF, "http_status": 200}
    if mode == "duplicate":
        attempts = [a, deepcopy(a)]
    else:
        a["finished_at"] = "2026-10-21T19:59:00Z"
        attempts = [a]
    with pytest.raises(ValueError, match="DUPLICATE_HTTP_ATTEMPT|NEGATIVE_HTTP_DURATION"):
        first(attempts=attempts)


def test_parser_failures_are_separate_from_http_error_rate():
    r = first(frames=[frame([], rejected=[{"reason": "BAD_PRICE"}],
                           unsupported=[{"reason": "UNSUPPORTED_MARKET"}])])
    assert r["counts"]["parser_rejected_fragments"] == 1
    assert r["counts"]["parser_unsupported_fragments"] == 1
    assert r["http"]["error_rate"]["ratio"] is None


@pytest.mark.parametrize("changes", [{"universe_scope": ""}, {"max_receipt_age_seconds": True},
                                    {"max_receipt_age_seconds": -1}])
def test_scope_and_receipt_policy_must_be_explicit(changes):
    with pytest.raises(ValueError, match="EXPLICIT_SCOPE"):
        measure(**changes)
