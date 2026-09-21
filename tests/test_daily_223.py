import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, date, datetime
from itertools import permutations
from pathlib import Path

import pytest

from goal_analysis.agents import load_master_prompt
from goal_analysis.engine.daily_223 import WEIGHTS, Daily223Policy, build_daily_223

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
DAY = date(2026, 9, 20)


def candidate(identifier="a", price=2.0, strength=0.8, **changes):
    item = {
        "candidate_id": identifier,
        "fixture_id": identifier,
        "home_team": f"Home {identifier}",
        "away_team": f"Away {identifier}",
        "competition": "DE1",
        "competition_type": "LEAGUE",
        "kickoff": "2026-09-20T18:00:00+02:00",
        "event_status": "SCHEDULED",
        "market_key": "totals_2_5",
        "selection_key": "over",
        "period": "FULL_TIME",
        "settlement": "90 minutes plus stoppage; extra time excluded",
        "bookmaker": "test-book",
        "region": "DE",
        "currency": "EUR",
        "decimal_price": price,
        "quoted_at": NOW.isoformat(),
        "quote_source_id": "synthetic-test-quote",
        "quote_available": True,
        "sensitivity_note": "Synthetic test only; unexpected rotation can affect the thesis.",
        "evidence": [
            {
                "evidence_id": f"{identifier}-{category}",
                "category": category,
                "statement": f"Synthetic {category} test statement, not live evidence.",
                "claim_type": "FACT",
                "source_id": f"synthetic-{category}",
                "observed_at": NOW.isoformat(),
                "strength": strength,
                "reliability": 1.0,
            }
            for category in WEIGHTS
        ],
    }
    item.update(changes)
    return item


def pool():
    return [candidate("a", 2), candidate("b", 2), candidate("c", 3)]


def test_exact_three_distinct_legs_with_strict_twelve_price():
    report = build_daily_223(pool(), DAY, NOW)
    assert report["construction_status"] == "COMPLETE"
    assert [leg["target_price"] for leg in report["legs"]] == [2, 2, 3]
    assert len({leg["fixture_id"] for leg in report["legs"]}) == 3
    assert report["combined_price"] == 12
    assert report["odds_band"] == "STRICT"
    assert report["betting_approved"] is report["real_wager_placed"] is False
    assert report["selection_status"] == "RESEARCH_ONLY"
    assert len(report["report_sha256"]) == 64


def test_busy_bookmaker_keeps_evidence_diversified_candidates_instead_of_disappearing():
    rows = [candidate(f"fixture-{i:03d}", 2, strength=0.4) for i in range(205)]
    rows.extend(candidate(f"strong-{i}", price, strength=0.9) for i, price in enumerate((2, 2, 3)))
    report = build_daily_223(rows, DAY, NOW)
    assert report["construction_status"] == "COMPLETE"
    assert report["eligible_candidate_count"] == 208
    assert report["search_candidate_count"] == 120
    assert {leg["candidate_id"] for leg in report["legs"]} == {"strong-0", "strong-1", "strong-2"}
    assert report["search_diagnostics"][0]["deferred_candidates"] == 88


def test_deterministic_with_input_reordering():
    for sequence in permutations(pool()):
        assert build_daily_223(sequence, DAY, NOW) == build_daily_223(pool(), DAY, NOW)


@pytest.mark.parametrize(
    ("prices", "complete"),
    [
        ((1.96, 2, 3), True),
        ((2, 2, 2.94), True),
        ((1.96, 1.96, 2.94), False),
        ((1.959, 2.5, 3.5), False),
        ((2, 2, 2.939), False),
    ],
)
def test_minimal_tolerance_includes_total_constraint(prices, complete):
    result = build_daily_223([candidate(str(i), p) for i, p in enumerate(prices)], DAY, NOW)
    assert (result["construction_status"] == "COMPLETE") is complete
    if complete:
        assert result["odds_band"] == "NEAR_TARGET"
        assert result["combined_price"] >= 11.76


def test_strict_candidate_preferred_to_relaxed_candidate():
    report = build_daily_223([*pool(), candidate("d", 2.94, strength=1)], DAY, NOW)
    assert report["odds_band"] == "STRICT"
    assert "c" in [leg["candidate_id"] for leg in report["legs"]]


def test_disabled_tolerance_and_bad_tolerances():
    data = [candidate("a", 1.96), candidate("b", 2), candidate("c", 3)]
    assert not build_daily_223(data, DAY, NOW, Daily223Policy(tolerance=0))["legs"]
    for tolerance in (-0.1, 0.021, float("nan"), True):
        with pytest.raises(ValueError):
            Daily223Policy(tolerance=tolerance)


def test_price_is_not_a_positive_evidence_weight():
    report = build_daily_223([*pool(), candidate("d", 8, strength=0.2)], DAY, NOW)
    assert "d" not in [leg["candidate_id"] for leg in report["legs"]]
    assert all(leg["support_score"] == 80 for leg in report["legs"])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("bookmaker", "other-book"),
        ("region", "other-region"),
        ("currency", "USD"),
        ("fixture_id", "a"),
        ("quote_available", False),
        ("quote_available", "true"),
        ("kickoff", NOW.isoformat()),
        ("kickoff", "2026-09-21T18:00:00+02:00"),
        ("event_status", "CANCELLED"),
        ("decimal_price", float("nan")),
        ("decimal_price", float("inf")),
        ("decimal_price", True),
        ("period", ""),
        ("quote_source_id", ""),
    ],
)
def test_invalid_or_non_combinable_leg_never_fills_third_slot(name, value):
    data = pool()
    data[2][name] = value
    assert not build_daily_223(data, DAY, NOW)["legs"]


def test_quote_boundary_and_berlin_midnight():
    data = pool()
    data[2]["quoted_at"] = "2026-09-20T09:55:00+00:00"
    assert len(build_daily_223(data, DAY, NOW)["legs"]) == 3
    data[2]["kickoff"] = "2026-09-20T22:00:00+00:00"  # Already next day in Berlin.
    assert not build_daily_223(data, DAY, NOW)["legs"]


def test_mixed_markets_are_allowed_without_relabeling_full_time_as_half_time():
    data = pool()
    data[1].update(market_key="h2h", selection_key="home")
    data[2].update(
        market_key="totals_1_5",
        period="FIRST_HALF",
        settlement="First half plus stoppage only",
        competition_type="UEFA",
    )
    report = build_daily_223(data, DAY, NOW)
    assert report["legs"][2]["period"] == "FIRST_HALF"


def test_repeated_arguments_cannot_inflate_score():
    data = pool()
    extra = dict(data[0]["evidence"][0], evidence_id="same-claim-different-wording")
    data[0]["evidence"].append(extra)
    assert build_daily_223(data, DAY, NOW)["legs"][0]["support_score"] == 80
    data[0]["evidence"].append(dict(extra))
    assert not build_daily_223(data, DAY, NOW)["legs"]


def test_missing_motivation_not_invented_and_stale_context_not_rewarded():
    data = pool()
    data[0]["evidence"] = data[0]["evidence"][:2]
    for observation in data[1]["evidence"][2:]:
        observation["observed_at"] = "2026-09-18T09:59:59+00:00"
    report = build_daily_223(data, DAY, NOW)
    assert report["legs"][0]["support_score"] == 44
    assert report["legs"][1]["support_score"] == 44
    assert "documented_motivation" in report["legs"][0]["missing_support"]


def test_future_evidence_or_missing_history_not_accepted():
    data = pool()
    data[0]["evidence"][0]["observed_at"] = "2026-09-20T10:01:00+00:00"
    assert not build_daily_223(data, DAY, NOW)["legs"]
    data[0]["evidence"] = data[0]["evidence"][2:]
    assert not build_daily_223(data, DAY, NOW)["legs"]


def test_other_ticket_decisions_not_used_and_input_not_mutated():
    data = pool()
    for item in data:
        item["other_ticket_status"] = "VETO"
        item["already_used_in_other_ticket"] = True
    before = deepcopy(data)
    report = build_daily_223(data, DAY, NOW)
    assert len(report["legs"]) == 3
    assert data == before


def test_ambiguous_quote_history_requires_a_current_snapshot():
    data = pool()
    data.append(dict(data[0], candidate_id="a-new", quote_available=False))
    report = build_daily_223(data, DAY, NOW)
    assert not report["legs"]
    assert any("DUPLICATE_PRODUCT" in item["reason"] for item in report["rejected_candidates"])


def test_report_still_emitted_without_three_real_candidates():
    report = build_daily_223([], DAY, NOW)
    assert report["daily_report_required"] is True
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert report["follow_up"]
    assert report["legs"] == []


def test_incomplete_report_retains_real_candidates_and_evidence():
    report = build_daily_223(pool()[:2], DAY, NOW)
    assert report["construction_status"] == "CONSTRUCTION_INCOMPLETE"
    assert [item["candidate_id"] for item in report["candidate_shortlist"]] == ["a", "b"]
    assert report["candidate_shortlist"][0]["evidence"]
    assert report["legs"] == []


def test_inferences_remain_labeled_and_unknown_claim_type_is_rejected():
    data = pool()
    data[0]["evidence"][0]["claim_type"] = "INFERENCE"
    report = build_daily_223(data, DAY, NOW)
    historical = next(
        item for item in report["legs"][0]["evidence"] if item["category"] == "historical"
    )
    assert historical["claim_type"] == "INFERENCE"
    data[0]["evidence"][0]["claim_type"] = "LIKELY_FACT"
    assert not build_daily_223(data, DAY, NOW)["legs"]


def test_versioned_prompt_keeps_v2_intact():
    old = load_master_prompt(Path("config/prompts"), "arthur-pentagram-v2")
    new = load_master_prompt(Path("config/prompts"), "arthur-pentagram-v2.1")
    assert old.sha256 == "f29de06a751fc1283343083771d2877ef512e0b73be28c3de6891d641ae72fe8"
    assert new.sha256 != old.sha256
    assert old.text.split("## 1.")[1] in new.text
    assert "## 16. DAILY_223" in new.text
    assert "11,76" in new.text


def test_cli_writes_report_and_refuses_to_overwrite(tmp_path):
    source, output = tmp_path / "input.json", tmp_path / "report.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "date": DAY.isoformat(),
                "observed_at": NOW.isoformat(),
                "candidates": pool(),
            }
        ),
        "utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(Path("src").resolve()))
    args = [
        sys.executable,
        "scripts/build-daily-223.py",
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    run = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(output.read_text("utf-8"))["combined_price"] == 12
    original = output.read_bytes()
    rerun = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    assert rerun.returncode == 1
    assert output.read_bytes() == original
