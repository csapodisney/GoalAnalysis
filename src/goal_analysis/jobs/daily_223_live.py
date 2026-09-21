"""One independent run: history, current fixtures, matched prices, evidence and report."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.engine.daily_223 import build_daily_223
from goal_analysis.providers.api_football_history import ApiFootballHistoryCollector, aware_time
from goal_analysis.providers.base import ProviderError

from .daily_223_candidates import (
    assemble_daily223_candidates,
    match_daily_events,
    merge_extra_event,
    normalize_daily_fixtures,
)
from .daily_223_history import build_daily223_from_history


class BudgetFootballClient:
    def __init__(self, client, maximum: int):
        self.client, self.maximum, self.calls = client, maximum, 0

    @property
    def last_usage(self):
        return self.client.last_usage

    def request(self, endpoint, params):
        if self.calls >= self.maximum:
            raise ProviderError("football API call budget exhausted")
        if self.last_usage.remaining_day == 0:
            raise ProviderError("football API daily quota exhausted")
        self.calls += 1
        return self.client.request(endpoint, params)


def blocked_report(target_date: date, now: datetime, stage: str, reason: str) -> dict:
    report = build_daily_223([], target_date, now)
    report.update(construction_status="DATA_BLOCKED", blocked_stage=stage, reason=reason)
    del report["report_sha256"]
    report["report_sha256"] = canonical_sha256(report)
    return report


def _preference_rank(bookmaker: str, preferred: list[str]) -> int | None:
    for index, name in enumerate(preferred):
        if bookmaker == name or bookmaker.startswith(f"{name}_"):
            return index
    return None


def _report_strength(item: tuple[str, dict, int | None]) -> tuple:
    bookmaker, report, _ = item
    scores = [leg["support_score"] for leg in report["legs"]]
    if not scores:
        shortlist = report.get("candidate_shortlist", [])
        scores = [candidate["support_score"] for candidate in shortlist]
    return (
        -min(scores, default=0),
        -sum(scores),
        -report["eligible_candidate_count"],
        bookmaker,
    )


def _build_for_one_bookmaker(candidate_input: dict, history: dict) -> tuple[dict, dict]:
    preferred = candidate_input["preferred_bookmakers"]
    bookmakers = sorted({item["bookmaker"] for item in candidate_input["candidates"]})
    evaluated = []
    for bookmaker in bookmakers:
        scoped = dict(candidate_input)
        scoped["candidates"] = [
            item for item in candidate_input["candidates"] if item["bookmaker"] == bookmaker
        ]
        report = build_daily223_from_history(scoped, history)
        evaluated.append((bookmaker, report, _preference_rank(bookmaker, preferred)))
    complete = [item for item in evaluated if item[1]["construction_status"] == "COMPLETE"]
    preferred_complete = [item for item in complete if item[2] is not None]
    if preferred_complete:
        best_rank = min(item[2] for item in preferred_complete)
        selected = min(
            (item for item in preferred_complete if item[2] == best_rank),
            key=_report_strength,
        )
        method = "PREFERRED_COMPLETE"
    elif complete:
        selected = min(complete, key=_report_strength)
        method = "EVIDENCE_RANKED_FALLBACK"
    elif evaluated:
        selected = min(evaluated, key=_report_strength)
        method = "NO_COMPLETE_BOOKMAKER"
    else:
        selected = (None, build_daily223_from_history(candidate_input, history), None)
        method = "NO_BOOKMAKER_CANDIDATES"
    bookmaker, report, rank = selected
    selection = {
        "selected": bookmaker,
        "preferred": rank is not None,
        "method": method,
        "preferred_bookmakers": preferred,
        "evaluated": [
            {
                "bookmaker": key,
                "preferred": preference is not None,
                "construction_status": candidate_report["construction_status"],
                "eligible_candidate_count": candidate_report["eligible_candidate_count"],
            }
            for key, candidate_report, preference in sorted(evaluated)
        ],
    }
    return report, selection


def run_daily223_live(
    config: dict,
    football_client,
    odds_feed,
    cache,
    target_date: date | None = None,
    clock=lambda: datetime.now(UTC),
    *,
    construct_legacy: bool = True,
    allow_incomplete: bool = False,
) -> dict:
    started = clock()
    aware_time(started.isoformat())
    today = started.astimezone(ZoneInfo("Europe/Berlin")).date()
    target_date = target_date or today
    artifacts, stage = {}, "configuration"
    budget = None
    try:
        config = validate_live_config(config)
        if not today <= target_date <= today + timedelta(days=7):
            raise ValueError(
                "live runs require today through the next 7 Berlin dates; "
                "past dates are recorded results or historical replay only"
            )
        if (
            odds_feed.region != config["odds_region"]
            or odds_feed.max_credits != config["max_odds_credits"]
        ):
            raise ValueError("odds feed configuration mismatch")
        if construct_legacy and config["max_football_calls"] < len(config["leagues"]):
            raise ProviderError("football call budget cannot cover the configured fixture queries")
        # Check only the minimum bulk request. A quiet day must not require
        # enough credits for every configured league before fixtures are known.
        if construct_legacy:
            odds_feed.reserve_check(2)
        artifacts["config"] = config
        budget = BudgetFootballClient(football_client, config["max_football_calls"])
        if not construct_legacy:
            from .portfolio_calendar import collect_calendar, empty_history

            stage = "fixtures"
            fixture_time = clock()
            fixtures, fixture_issues, calendar = collect_calendar(
                budget, config["leagues"], target_date
            )
            artifacts["calendar"] = calendar
            artifacts["fixtures"] = {"observed_at": fixture_time.isoformat(), "records": fixtures}
            stage = "history"
            active = {
                int(f["api_football_league_id"]) for f in fixtures if f["provider_status"] == "NS"
            }
            pairs = {
                (league["api_football_id"], season)
                for league in config["leagues"]
                if league["api_football_id"] in active
                for season in league["history_seasons"]
            }
            pairs.update(
                (int(f["api_football_league_id"]), f["season"])
                for f in fixtures
                if f["provider_status"] == "NS"
            )
            artifacts["history"] = (
                ApiFootballHistoryCollector(budget, cache, clock=clock).collect(
                    sorted(pairs),
                    max_calls=config["max_football_calls"] - budget.calls,
                    allow_partial=True,
                )
                if pairs
                else empty_history(clock())
            )
        else:
            stage = "history"
            pairs = [
                (league["api_football_id"], season)
                for league in config["leagues"]
                for season in league["history_seasons"]
            ]
            artifacts["history"] = ApiFootballHistoryCollector(budget, cache, clock=clock).collect(
                pairs,
                max_calls=config["max_football_calls"] - len(config["leagues"]),
                allow_partial=not construct_legacy,
            )
            stage = "fixtures"
            fixtures, fixture_time, fixture_issues = [], clock(), []
            for league in config["leagues"]:
                try:
                    payload = budget.request(
                        "fixtures",
                        {
                            "date": target_date.isoformat(),
                            "league": league["api_football_id"],
                            "season": league["season"],
                            "timezone": "Europe/Berlin",
                        },
                    )
                    fixtures.extend(normalize_daily_fixtures(payload, league, target_date))
                except (ProviderError, ValueError, TypeError, KeyError):
                    if construct_legacy:
                        raise
                    fixture_issues.append(
                        {
                            "league_id": league["api_football_id"],
                            "status": "LEAGUE_FIXTURES_UNAVAILABLE",
                            "message": "Egy liga mérkőzésadatai hiányoznak; a többi liga feldolgozása folytatódott.",
                        }
                    )
        artifacts["fixtures"] = {"observed_at": fixture_time.isoformat(), "records": fixtures}
        stage = "odds"
        events, base_issues = [], []
        artifacts["odds_bulk"] = {}
        for league in config["leagues"]:
            # Skip a league with no pending fixture in the current daily universe.
            if not any(
                item["sport_key"] == league["odds_sport_key"]
                and item["provider_status"] == "NS"
                and aware_time(item["kickoff"]) > clock()
                for item in fixtures
            ):
                continue
            try:
                odds_feed.reserve_check(2)
            except ProviderError as error:
                base_issues.append(
                    {
                        "sport_key": league["odds_sport_key"],
                        "status": "BASE_MARKETS_BUDGET_UNAVAILABLE",
                        "reason": str(error),
                    }
                )
                continue
            try:
                payload = odds_feed.bulk(league["odds_sport_key"], target_date)
            except ProviderError as error:
                base_issues.append(
                    {
                        "sport_key": league["odds_sport_key"],
                        "status": "BASE_MARKETS_UNAVAILABLE",
                        "reason": str(error),
                    }
                )
                continue
            artifacts["odds_bulk"][league["odds_sport_key"]] = payload
            events.extend(payload)
            if not payload:
                base_issues.append(
                    {
                        "sport_key": league["odds_sport_key"],
                        "status": "EMPTY_ODDS_RESPONSE",
                        "returned_events": 0,
                        "message": "Az Odds API üres kínálatot adott erre a napra és ligára. A mérkőzések szorzó nélkül is megmaradnak.",
                    }
                )
        stage = "extra_markets"
        # Cover BTTS before experimental half-time markets when credits are tight.
        extras = sorted(
            config["extra_markets"],
            key=lambda market: (market != "btts", market != "totals_h1", market),
        )
        extra_issues = []
        if extras:
            pairs, _ = match_daily_events(
                fixtures, events, config["team_aliases"], target_date, clock()
            )
            replacements = {}
            conflicted = set()
            artifacts["odds_extra"] = {}
            pairs.sort(key=lambda pair: (aware_time(pair[0]["kickoff"]), pair[0]["fixture_id"]))
            completed_extra = 0
            for market in extras:
                for _, event in pairs:
                    if event["id"] in conflicted:
                        continue
                    try:
                        odds_feed.reserve_check(1)
                    except ProviderError:
                        extra_issues.append(
                            {
                                "event_id": event["id"],
                                "market": market,
                                "status": "OPTIONAL_MARKETS_BUDGET_UNAVAILABLE",
                            }
                        )
                        continue
                    try:
                        extra = odds_feed.extra(event["sport_key"], event["id"], [market])
                    except ProviderError:
                        extra_issues.append(
                            {
                                "event_id": event["id"],
                                "market": market,
                                "status": "OPTIONAL_MARKETS_UNAVAILABLE",
                            }
                        )
                        continue
                    artifacts["odds_extra"].setdefault(event["id"], {})[market] = extra
                    try:
                        replacements[event["id"]] = merge_extra_event(
                            replacements.get(event["id"], event), extra
                        )
                        completed_extra += 1
                    except (ProviderError, KeyError, ValueError, TypeError):
                        conflicted.add(event["id"])
                        extra_issues.append(
                            {"event_id": event["id"], "status": "CONFLICTING_EVENT_IDENTITY"}
                        )
            if completed_extra < len(pairs) * len(extras):
                extra_issues.append(
                    {
                        "status": "OPTIONAL_MARKETS_PARTIAL_COVERAGE",
                        "requested_event_markets": len(pairs) * len(extras),
                        "retrieved_event_markets": completed_extra,
                    }
                )
            events = [
                replacements.get(event.get("id"), event) if isinstance(event, dict) else event
                for event in events
                if not isinstance(event, dict) or event.get("id") not in conflicted
            ]
        stage = "construction"
        observed = clock()
        if observed < started:
            raise ValueError("clock moved backwards during live run")
        candidate_input = assemble_daily223_candidates(
            fixtures,
            events,
            config,
            target_date,
            observed,
            fixture_time,
            allow_stale_quotes=allow_incomplete,
        )
        candidate_input["data_issues"].extend(
            [
                *artifacts["history"].get("data_issues", []),
                *fixture_issues,
                *base_issues,
                *extra_issues,
            ]
        )
        artifacts["candidate_input"] = candidate_input
        if construct_legacy:
            report, bookmaker_selection = _build_for_one_bookmaker(
                candidate_input, artifacts["history"]
            )
        else:
            # The portfolio coordinator enriches this shared universe once and
            # constructs all profiles. Do not repeat that work for every book.
            report = build_daily_223([], target_date, observed)
            report.update(
                construction_status="COLLECTION_COMPLETE",
                selection_status="NOT_RUN",
                input_candidate_count=len(candidate_input["candidates"]),
                follow_up=[],
                collection_status="COLLECTION_COMPLETE",
            )
            bookmaker_selection = {
                "selected": None,
                "preferred": False,
                "method": "DEFERRED_TO_PORTFOLIO",
                "evaluated": [],
                "preferred_bookmakers": candidate_input["preferred_bookmakers"],
            }
        report["bookmaker_selection"] = bookmaker_selection
        report["matching"] = candidate_input["matching"]
        report["data_issues"] = candidate_input["data_issues"]
        report["market_coverage"] = candidate_input["market_coverage"]
        report["requested_market_coverage"] = candidate_input["requested_market_coverage"]
        report["account_context"] = candidate_input["account_context"]
        report["context_review_pending"] = [
            "competition_context",
            "load_and_squad",
            "documented_motivation",
        ]
        report["bookmaker_account_and_combination_verified"] = False
        report["risk_notes"].append(
            "Feed quotes and market conventions do not verify account-specific availability or bookmaker settlement rules."
        )
    except (
        ProviderError,
        ValueError,
        TypeError,
        KeyError,
        OSError,
        AttributeError,
        OverflowError,
    ) as error:
        reason = str(error)
        for key in (
            getattr(football_client, "_api_key", None),
            getattr(odds_feed, "_api_key", None),
        ):
            if key:
                reason = reason.replace(key, "[REDACTED]").replace(
                    urllib.parse.quote_plus(key), "[REDACTED]"
                )
        report = blocked_report(target_date, clock(), stage, reason)
    report["usage"] = {
        "football_calls": budget.calls if budget else 0,
        "odds_calls": odds_feed.calls,
        "odds_credits_reserved": odds_feed.reserved_credits,
        "odds_provider_usage": odds_feed.usage,
        "llm_calls": 0,
    }
    del report["report_sha256"]
    report["report_sha256"] = canonical_sha256(report)
    artifacts["report"] = report
    return {"schema_version": 1, "artifacts": artifacts}


def write_daily223_bundle(directory: Path, bundle: dict) -> None:
    """Write into a pre-reserved new run directory; never overwrite run artifacts."""
    for name, value in bundle["artifacts"].items():
        with (directory / f"{name}.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    with (directory / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(render_daily223_report(bundle["artifacts"]["report"]))


def render_daily223_report(report: dict) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    lines = [
        f"# Önálló napi 2×2×3 — {report['date']}",
        "",
        f"Állapot: **{report['construction_status']}** · RESEARCH_ONLY",
        "",
    ]
    if report.get("reason"):
        lines.extend([f"Adatgyűjtési akadály: {cell(report['reason'])}", ""])
    selection = report.get("bookmaker_selection", {})
    if selection.get("selected"):
        lines.extend(
            [
                f"Kiválasztott iroda: **{cell(selection['selected'])}** · {cell(selection['method'])}",
                "",
            ]
        )
    if report["legs"]:
        lines.extend(
            ["| Meccs | Berlini kezdés | Piac / periódus | Szorzó |", "| --- | --- | --- | ---: |"]
        )
        for leg in report["legs"]:
            start = aware_time(leg["kickoff"]).astimezone(ZoneInfo("Europe/Berlin"))
            lines.append(
                f"| {cell(leg['home_team'])} – {cell(leg['away_team'])} | {start:%H:%M} | {cell(leg['market_key'])} / {cell(leg['selection_key'])} / {leg['period']} | {leg['decimal_price']:.2f} |"
            )
        lines.extend(
            [
                "",
                f"Elméleti összszorzó: **{report['combined_price']:.4f}** · {report['odds_band']}",
                "",
            ]
        )
        for leg in report["legs"]:
            lines.extend(
                [
                    f"## {cell(leg['home_team'])} – {cell(leg['away_team'])}",
                    "",
                    f"Bizonyítékpont: {leg['support_score']:.2f}/100; ez nem találati esély.",
                    f"Iroda: {cell(leg['bookmaker'])}; ár időbélyege: {leg['quoted_at']}",
                    "",
                ]
            )
            lines.extend(f"- {cell(item['statement'])}" for item in leg["evidence"])
            lines.extend(["", f"Feltétel: {cell(leg['sensitivity_note'])}", ""])
    else:
        lines.append(
            "A futás nem állított elő három igazolt, megfelelően árazott lábat. A JSON-riport megőrzi az adatokat és a pontos hiányokat."
        )
    lines.extend(
        [
            "",
            "Ez történeti adatokra és formaadatokra épülő kutatási riport. A friss kerethírek, a meccs versenyhelyzete és a motiváció értékelése még hiányzik; nincs automatikus fogadás.",
            "",
        ]
    )
    return "\n".join(lines)
