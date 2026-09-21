"""One coherent run of the Arthur desk, with immutable reports and bounded AI review."""

from __future__ import annotations

import json
import os
import urllib.parse
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import validate_settings
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.quote_metadata import quote_metadata

BERLIN = ZoneInfo("Europe/Berlin")


def safe_error(error: object) -> str:
    message = str(error)
    for name in ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY", "OPENAI_API_KEY"):
        key = os.environ.get(name, "")
        if key:
            message = message.replace(key, "[REDACTED]").replace(
                urllib.parse.quote_plus(key), "[REDACTED]"
            )
    return message[:1200]


def validate_target_day(target: date, now: datetime) -> None:
    today = now.astimezone(BERLIN).date()
    if not today <= target <= today + timedelta(days=7):
        raise ValueError(
            "Új elemzés mára vagy a következő hét napra kérhető. Korábbi naphoz az archívum használható."
        )


class RunLock(AbstractContextManager):
    """OS lock shared by dashboard and Windows Task Scheduler; process exit releases it."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Már fut egy Arthur-feladat. Várd meg a befejezését.") from None
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
        return False


def effective_live_config(config: dict, settings: dict) -> dict:
    live = validate_live_config(config)
    extras = set(live["extra_markets"])
    if "ritmusor" in settings["enabled_profiles"]:
        extras.add("btts")
    if "h1_over05" in settings["enabled_profiles"]:
        extras.add("totals_h1")
    live["extra_markets"] = sorted(extras)
    return live


def _review_packets(enriched: dict, preliminary: dict, maximum: int) -> list[dict]:
    """Selected legs first, then different fixture/market alternatives; never all books."""
    from goal_analysis.engine.daily_223 import Daily223Policy
    from goal_analysis.engine.portfolio import prepare_portfolio_candidate
    from goal_analysis.providers.api_football_history import aware_time

    candidates = {}
    for candidate in enriched.get("candidates", []):
        try:
            prepare_portfolio_candidate(
                candidate,
                date.fromisoformat(enriched["date"]),
                aware_time(enriched["observed_at"]),
                Daily223Policy(**enriched.get("policy", {})),
                preliminary["policy"],
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        candidates[candidate["candidate_id"]] = candidate
    analysis = {
        a["candidate_id"]: a for a in enriched.get("history_analysis", {}).get("candidates", [])
    }
    chosen = []
    seen = set()
    products = set()
    for ticket in preliminary.get("tickets", []):
        for leg in ticket["legs"]:
            identifier = leg["candidate_id"]
            if identifier not in seen and identifier in candidates:
                chosen.append(identifier)
                seen.add(identifier)
                products.add(
                    (leg["fixture_id"], leg["market_key"], leg["selection_key"], leg["period"])
                )
    scored = []
    for identifier, candidate in candidates.items():
        ev = candidate.get("evidence", [])
        score = sum(e.get("strength", 0) * e.get("reliability", 0) for e in ev)
        scored.append((-score, identifier))
    review_limit = min(maximum, len(chosen) + 6)
    for _, identifier in sorted(scored):
        if len(chosen) >= review_limit:
            break
        item = candidates[identifier]
        product = (item["fixture_id"], item["market_key"], item["selection_key"], item["period"])
        if identifier not in seen and product not in products:
            chosen.append(identifier)
            seen.add(identifier)
            products.add(product)
    packets = []
    for identifier in chosen[:maximum]:
        packet = deepcopy(candidates[identifier])
        packet["historical_context"] = analysis.get(identifier, {})
        packets.append(packet)
    return packets


def _compact_feedback(ledger) -> dict | None:
    if ledger is None:
        return None
    source = ledger.feedback_summary()
    result = {
        "interpretation": source.get(
            "interpretation", "Descriptive observations, not automatic learning."
        )
    }
    for mode in ("actual", "research", "recommendations"):
        section = source.get(mode, {})
        result[mode] = {
            "summary": section.get("summary", {}),
            "by_profile": section.get("by_profile", [])[:8],
        }
    result["recent_settled"] = [
        {
            key: ticket.get(key)
            for key in ("profile_id", "date", "played", "combined_price", "outcome")
        }
        for ticket in source.get("recent_settled", [])[:12]
    ]
    return result


def run_portfolio(
    config: dict,
    settings: dict,
    football_client,
    odds_feed,
    cache,
    target_date: date,
    reviewer=None,
    ledger=None,
    clock=lambda: datetime.now(UTC),
) -> dict:
    """Injectable coordinator. No wager is ever placed or marked automatically."""
    from goal_analysis.engine.portfolio import build_portfolio, permissive
    from goal_analysis.engine.portfolio_preview import build_previews, preview_candidates
    from goal_analysis.engine.recommendations import build_recommendations, recommendation_packets

    settings = validate_settings(settings)
    started = clock()
    validate_target_day(target_date, started)
    run_id = f"{target_date.isoformat()}-{uuid4().hex[:12]}"
    bundle = run_daily223_live(
        effective_live_config(config, settings),
        football_client,
        odds_feed,
        cache,
        target_date,
        clock,
        construct_legacy=False,
        allow_incomplete=permissive(settings),
    )
    artifacts = bundle["artifacts"]
    legacy = artifacts["report"]
    review = {
        "status": "UNAVAILABLE",
        "model": settings["openai"]["model"],
        "reviews": [],
        "usage": {},
        "message": "Astra-elemzés még nem történt.",
    }
    if "candidate_input" in artifacts and "history" in artifacts:
        enriched = enrich_daily223_input(artifacts["candidate_input"], artifacts["history"])
        preliminary = build_portfolio(enriched, settings)
        packets = _review_packets(enriched, preliminary, settings["openai"]["max_candidates"])
        unpriced = preview_candidates(
            artifacts.get("fixtures", {}).get("records", []),
            artifacts["candidate_input"],
            artifacts["history"],
            settings,
        )
        recommendations = build_recommendations(enriched, unpriced, settings, clock(), preliminary)
        packets = recommendation_packets(
            recommendations, packets, settings["openai"]["max_candidates"]
        )
        if packets and reviewer is not None:
            review = reviewer.review(
                packets,
                target_date.isoformat(),
                feedback=_compact_feedback(ledger),
            )
        elif not packets:
            review["message"] = "Nincs elemzésre alkalmas jelölt; nem történt OpenAI-hívás."
        report = build_portfolio(enriched, settings, astra_review=review)
        # Review latency can change event eligibility, never price eligibility.
        finished = clock()
        from goal_analysis.providers.api_football_history import aware_time

        delivered = []
        for ticket in report.get("tickets", []):
            if any(aware_time(leg["kickoff"]) <= finished for leg in ticket["legs"]):
                report.setdefault("diagnostics", []).append(
                    {
                        "code": "EVENT_STARTED_DURING_REVIEW",
                        "profile_id": ticket["profile_id"],
                        "message": "A kiválasztott mérkőzés az elemzés alatt elkezdődött; új szelvényként nem adható ki.",
                    }
                )
                continue
            for leg in ticket["legs"]:
                leg.update(
                    quote_metadata(leg.get("quote_timestamp_raw", leg.get("quoted_at")), finished)
                )
            delivered.append(ticket)
        report["tickets"] = delivered
        report["preview_tickets"] = build_previews(unpriced, settings, delivered, finished, review)
        report["recommendations"] = build_recommendations(
            enriched, unpriced, settings, finished, report, review
        )
        report["review_requested_candidates"] = len(packets)
        artifacts["unpriced_candidates"] = unpriced
        published = {t["profile_id"] for t in delivered}
        for profile in report.get("profiles", []):
            profile["published"] = profile["profile_id"] in published
        if len(delivered) < settings["target_ticket_count"] or "daily223" not in published:
            report["construction_status"] = "PARTIAL" if delivered else "CONSTRUCTION_INCOMPLETE"
        artifacts["candidate_analysis"] = enriched
    else:
        report = {
            "schema_version": 3,
            "module": "ARTHUR_PORTFOLIO",
            "date": target_date.isoformat(),
            "observed_at": clock().isoformat(),
            "construction_status": "DATA_BLOCKED",
            "tickets": [],
            "profiles": [],
            "diagnostics": [],
            "policy": settings,
        }
    report.update(run_id=run_id, timezone="Europe/Berlin", astra=review, settings=settings)
    report.setdefault("preview_tickets", [])
    report.setdefault("recommendations", [])
    if report["preview_tickets"] and not report["tickets"]:
        report["construction_status"] = "ODDS_PENDING"
    priced_fixtures = {
        c["fixture_id"] for c in artifacts.get("candidate_input", {}).get("candidates", [])
    }
    report["fixtures"] = [
        {
            **fixture,
            "odds_status": "PRICED" if fixture["fixture_id"] in priced_fixtures else "ODDS_PENDING",
        }
        for fixture in artifacts.get("fixtures", {}).get("records", [])
    ]
    report["calendar"] = artifacts.get("calendar", {})
    report["odds_queries"] = [
        {
            "provider": "the_odds_api",
            "sport_key": sport,
            "date": target_date.isoformat(),
            "returned_events": len(events),
        }
        for sport, events in artifacts.get("odds_bulk", {}).items()
    ]
    recovery = artifacts.get("odds_recovery", {})
    report["odds_queries"].extend(recovery.get("queries", []))
    report["odds_coverage"] = recovery.get("coverage", [])
    report["odds_recovered_candidates"] = recovery.get("recovered_candidates", 0)
    report["preview"] = target_date > started.astimezone(BERLIN).date()
    report["finished_at"] = clock().isoformat()
    report["data_issues"] = legacy.get("data_issues", [])
    report["market_coverage"] = legacy.get("market_coverage", [])
    report["requested_market_coverage"] = legacy.get("requested_market_coverage", [])
    report["usage"] = {
        **legacy.get("usage", {}),
        "openai": review.get("usage", {}),
        "llm_calls": review.get("usage", {}).get("requests", 0),
    }
    report["funnel"] = {
        "fixtures": len(artifacts.get("fixtures", {}).get("records", [])),
        "priced_candidates": len(artifacts.get("candidate_input", {}).get("candidates", [])),
        "astra_reviewed": len(review.get("reviews", []))
        if review.get("status") == "COMPLETE"
        else 0,
        "tickets": len(report["tickets"]),
        "preview_tickets": len(report["preview_tickets"]),
        "recommendations": sum(bool(r["legs"]) for r in report["recommendations"]),
    }
    report.setdefault("diagnostics", [])
    if legacy.get("reason"):
        report["reason"] = safe_error(legacy["reason"])
        report["diagnostics"].append(
            {"code": legacy.get("blocked_stage", "DATA_BLOCKED"), "message": report["reason"]}
        )
    report["real_wager_placed"] = False
    report.pop("report_sha256", None)
    report["report_sha256"] = canonical_sha256(report)
    artifacts["daily223_report"] = legacy
    artifacts["report"] = report
    return {"schema_version": 3, "artifacts": artifacts}


def write_portfolio_bundle(directory: Path, bundle: dict) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in bundle["artifacts"].items():
        with (directory / f"{name}.json").open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    report = bundle["artifacts"]["report"]
    lines = [
        f"# Arthur — {report['date']}",
        "",
        f"Állapot: {report['construction_status']}",
        f"Astra: {report['astra']['status']}",
        "",
    ]
    for ticket in report.get("tickets", []):
        lines.extend(
            [
                f"## {ticket['profile_name']} · {ticket['combined_price']:.2f}×",
                "",
                f"Iroda: {ticket['bookmaker']} · {ticket['status']}",
                "",
            ]
        )
        for leg in ticket["legs"]:
            lines.append(
                f"- {leg['home_team']} – {leg['away_team']}: {leg['market_key']} / {leg['selection_key']} / {leg['period']} · {leg['decimal_price']:.2f}"
            )
        lines.extend(["", *ticket.get("reasoning", []), ""])
        lines.extend("Figyelmeztetés: " + warning for warning in ticket.get("quality_warnings", []))
        lines.append("")
    for ticket in report.get("preview_tickets", []):
        lines.extend([f"## {ticket['profile_name']} · Szorzóra váró előzetes", ""])
        for leg in ticket["legs"]:
            lines.append(
                f"- {leg['home_team']} – {leg['away_team']}: {leg['market_key']} / {leg['selection_key']} / {leg['period']} · szorzó hiányzik"
            )
        lines.extend("Figyelmeztetés: " + warning for warning in ticket["quality_warnings"])
        lines.append("")
    if not report.get("tickets") and not report.get("preview_tickets"):
        lines.append(
            report.get(
                "reason",
                "A részletes adat- és lefedettségi diagnosztika a report.json fájlban található.",
            )
        )
    lines.extend(
        [
            "",
            "A szelvények generálása nem jelent tényleges fogadást. Az eredménynapló a megjátszott tételeket külön kezeli.",
            "",
        ]
    )
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
