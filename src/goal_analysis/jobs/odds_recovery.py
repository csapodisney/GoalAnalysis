"""Fill portfolio price gaps without buying another feed or using model tokens."""

from collections import defaultdict
from zoneinfo import ZoneInfo

from goal_analysis.jobs.daily_223_candidates import assemble_daily223_candidates
from goal_analysis.providers.api_football_history import aware_time
from goal_analysis.providers.api_football_odds import ApiFootballOddsCollector, normalize_odds


def required_products(config):
    required = {("h2h", choice, "FULL_TIME") for choice in ("home", "draw", "away")}
    required.update(("totals_2_5", choice, "FULL_TIME") for choice in ("over", "under"))
    for market in config["extra_markets"]:
        if market in {"btts", "btts_h1"}:
            period = "FIRST_HALF" if market.endswith("_h1") else "FULL_TIME"
            required.update(("btts", choice, period) for choice in ("yes", "no"))
        elif market == "totals_h1":
            required.update(("totals_0_5", choice, "FIRST_HALF") for choice in ("over", "under"))
        elif market == "h2h_3_way_h1":
            required.update(("h2h", choice, "FIRST_HALF") for choice in ("home", "draw", "away"))
    return required


def _products(candidates):
    result = defaultdict(set)
    for c in candidates:
        result[c["fixture_id"]].add((c["market_key"], c["selection_key"], c["period"]))
    return result


def recover_odds(
    candidate_input, fixtures, config, budget, cache, target_date, clock, fixtures_observed_at
):
    """Retain separate quote providers/books and expose per-fixture coverage."""
    now = clock()
    pending = [
        f
        for f in fixtures
        if f["provider_status"] == "NS"
        and aware_time(f["kickoff"]) > now
        and aware_time(f["kickoff"]).astimezone(ZoneInfo("Europe/Berlin")).date() == target_date
    ]
    required = required_products(config)
    before = _products(candidate_input["candidates"])
    missing = [f for f in pending if required - before[f["fixture_id"]]]
    queries, issues, normalized = [], [], []
    collector = ApiFootballOddsCollector(
        budget, cache, config["api_football_odds_max_calls"], clock
    )
    if missing and config["api_football_odds_max_calls"]:
        rows, queries, issues = collector.collect(missing, target_date)
        normalized, parse_issues = normalize_odds(rows, missing, clock())
        issues.extend(parse_issues)
        enriched = assemble_daily223_candidates(
            missing,
            normalized,
            config,
            target_date,
            clock(),
            fixtures_observed_at,
            allow_stale_quotes=True,
        )
        # The join to the calendar was validated by provider IDs before using
        # the common market parser. It is not a fuzzy cross-provider name match.
        for match in enriched["matching"]:
            match.update(
                provider="api_football", method="EXACT_PROVIDER_FIXTURE_LEAGUE_SEASON_KICKOFF"
            )
        for issue in enriched["data_issues"]:
            issue["provider"] = "api_football"
        candidate_input["candidates"].extend(enriched["candidates"])
        candidate_input["matching"].extend(enriched["matching"])
        issues.extend(enriched["data_issues"])
        for field in ("market_coverage", "available_bookmakers"):
            candidate_input[field] = sorted(set(candidate_input[field]) | set(enriched[field]))
    by_fixture = defaultdict(list)
    for c in candidate_input["candidates"]:
        by_fixture[c["fixture_id"]].append(c)
    after = _products(candidate_input["candidates"])
    coverage = []
    for f in pending:
        quotes = by_fixture[f["fixture_id"]]
        gaps = required - after[f["fixture_id"]]
        sources = []
        for provider in sorted({c["quote_provider"] for c in quotes}):
            scoped = [c for c in quotes if c["quote_provider"] == provider]
            sources.append(
                {
                    "provider": provider,
                    "candidate_count": len(scoped),
                    "bookmakers": sorted({c["bookmaker_name"] for c in scoped}),
                    "oldest_quote_at": min(c["quoted_at"] for c in scoped),
                    "newest_quote_at": max(c["quoted_at"] for c in scoped),
                }
            )
        coverage.append(
            {
                "fixture_id": f["fixture_id"],
                "status": "COMPLETE" if not gaps else "PARTIAL" if quotes else "ODDS_PENDING",
                "missing_products": [
                    dict(zip(("market", "selection", "period"), p)) for p in sorted(gaps)
                ],
                "providers": sources,
                "recovered_candidates": sum(c["quote_provider"] == "api_football" for c in quotes),
            }
        )
    candidate_input["data_issues"].extend(issues)
    recovered_ids = {
        c["fixture_id"]
        for c in candidate_input["candidates"]
        if c["quote_provider"] == "api_football"
    }
    for issue in candidate_input["data_issues"]:
        if (
            issue.get("provider") == "the_odds_api"
            and issue.get("status") == "UNMATCHED"
            and issue.get("fixture_id") in recovered_ids
        ):
            issue.update(
                status="PRIMARY_ODDS_MISSING_RECOVERED",
                message="Az Odds API-nál hiányzó mérkőzéshez az API-Football adott szorzókat.",
            )
    return {
        "queries": queries,
        "coverage": coverage,
        "api_football_calls": collector.calls,
        "recovered_candidates": sum(
            c["quote_provider"] == "api_football" for c in candidate_input["candidates"]
        ),
        "events": normalized,
    }
