"""Bounded research contract; web-extracted prices are indicative, never live feed quotes."""

import math
from datetime import datetime, timedelta

from goal_analysis.agents.astra_review import _public_url

TOPICS = ("weather", "coach", "lineup", "absences", "workload", "motivation")


def research_schema():
    string = {"type": "string"}
    strings = {"type": "array", "items": string}

    def obj(fields):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": fields,
            "required": list(fields),
        }

    quote = obj(
        {
            **{
                k: string
                for k in (
                    "candidate_id",
                    "home_team",
                    "away_team",
                    "kickoff",
                    "market_key",
                    "selection_key",
                    "period",
                    "bookmaker",
                    "source_url",
                    "observed_at",
                )
            },
            "decimal_price": {"type": "number"},
        }
    )
    context = obj(
        {
            "fixture_id": string,
            "topic": {"type": "string", "enum": list(TOPICS)},
            "status": {"type": "string", "enum": ["SOURCED", "PROVISIONAL", "UNKNOWN"]},
            "summary": string,
            "source_urls": strings,
        }
    )
    return obj(
        {
            "notes": string,
            "sources": {"type": "array", "items": obj({"url": string, "title": string})},
            "odds_quotes": {"type": "array", "items": quote},
            "fixture_context": {"type": "array", "items": context},
        }
    )


def validate_research_details(payload, candidates, sources, observed_at, searched):
    """Discard invalid individual findings without throwing away other fixture research."""
    allowed = {s["url"] for s in sources} if searched else set()
    index = {c["candidate_id"]: c for c in candidates}
    fixtures = {c["fixture_id"] for c in candidates}
    cutoff = datetime.fromisoformat(observed_at)
    quotes, contexts, rejected = [], {}, 0
    for row in payload.get("odds_quotes", [])[:40]:
        try:
            c = index[row["candidate_id"]]
            if not allowed or row["source_url"] not in allowed:
                raise ValueError
            _public_url(row["source_url"])
            if any(
                row[k] != c[k]
                for k in ("home_team", "away_team", "market_key", "selection_key", "period")
            ):
                raise ValueError
            kickoff = datetime.fromisoformat(row["kickoff"])
            observed = datetime.fromisoformat(row["observed_at"])
            price = row["decimal_price"]
            if (
                kickoff != datetime.fromisoformat(c["kickoff"])
                or observed.tzinfo is None
                or not cutoff - timedelta(hours=24) <= observed <= cutoff
                or type(price) not in (int, float)
                or not math.isfinite(price)
                or not 1 < price <= 1000
                or not isinstance(row["bookmaker"], str)
                or not 1 <= len(row["bookmaker"]) <= 100
            ):
                raise ValueError
            quotes.append(
                {
                    **row,
                    "verification": "CODEX_WEB_EXTRACTION",
                    "indicative": True,
                    "requires_bookmaker_check": True,
                }
            )
        except (ValueError, TypeError, KeyError):
            rejected += 1
    for row in payload.get("fixture_context", [])[:240]:
        if (
            not isinstance(row, dict)
            or row.get("fixture_id") not in fixtures
            or row.get("topic") not in TOPICS
        ):
            continue
        urls = row.get("source_urls", [])
        if (
            row.get("status") not in {"SOURCED", "PROVISIONAL", "UNKNOWN"}
            or not isinstance(urls, list)
            or any(not isinstance(u, str) or u not in allowed for u in urls)
            or not isinstance(row.get("summary"), str)
            or len(row["summary"]) > 1500
        ):
            continue
        if row["status"] != "UNKNOWN" and not urls:
            continue
        contexts[(row["fixture_id"], row["topic"])] = row
    # Every selected fixture has explicit coverage; absence must never read as verified.
    for fixture in sorted(fixtures):
        for topic in TOPICS:
            contexts.setdefault(
                (fixture, topic),
                {
                    "fixture_id": fixture,
                    "topic": topic,
                    "status": "UNKNOWN",
                    "summary": "Nem sikerült forrással ellenőrizni ebben a futásban.",
                    "source_urls": [],
                },
            )
    return {
        "web_odds": quotes,
        "fixture_context": list(contexts.values()),
        "rejected_web_quotes": rejected,
    }
