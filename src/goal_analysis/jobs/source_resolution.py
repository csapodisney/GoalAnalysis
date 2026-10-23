"""Audit already collected facts through the new SHADOW field resolver.

Full field decisions belong in a separate artifact. The saved/dashboard report
gets compact coverage, never another copy of every quote or another API request.
"""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from urllib.parse import urlsplit

from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.identity import exact_name
from goal_analysis.normalization.source_priority import (
    PRIORITIES,
    VERSION,
    Observation,
    resolve_field,
)

REQUIRED = ("home.season.expected_goals_for", "away.season.expected_goals_for",
            "home.availability.injuries", "away.availability.injuries",
            "home.availability.suspensions", "away.availability.suspensions", "lineup.confirmed")
ODDS_HOSTS = {"the_stats_api": "api.thestatsapi.com",
              "the_odds_api": "api.the-odds-api.com",
              "api_football": "v3.football.api-sports.io"}


def _same_scope(value, fixture):
    """Check every supplied identity assertion; missing fields are not evidence."""
    try:
        for name in ("fixture_id", "api_football_fixture_id", "api_football_home_team_id",
                     "api_football_away_team_id", "api_football_league_id", "source_provider",
                     "provider_fixture_id", "provider_home_team_id", "provider_away_team_id",
                     "provider_league_id", "provider_season_id"):
            if name not in value:
                continue
            expected = fixture.get(name)
            if name == "api_football_fixture_id" and expected is None:
                prefix, _, native = fixture["fixture_id"].partition(":")
                expected = native if prefix == "api_football" else None
            if expected is None or type(value[name]) is bool or str(value[name]) != str(expected):
                return False
        for name in ("home_team", "away_team"):
            if name in value and exact_name(value[name]) != exact_name(fixture[name]):
                return False
        if "kickoff" in value and utc(value["kickoff"]) != utc(fixture["kickoff"]):
            return False
    except (ValueError, TypeError, KeyError, OverflowError):
        return False
    return True


def _provider_url(value, hostname):
    try:
        parsed = urlsplit(value)
        return bool(hostname and parsed.scheme == "https" and parsed.hostname == hostname)
    except (ValueError, TypeError, AttributeError):
        return False


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def build_source_resolution(report, artifacts, *, as_of):
    """No mutation, model change, HTTP call or database write."""
    from goal_analysis.agents.grok_stats_data import validate_stats_context
    from goal_analysis.storage.sports_news import _assertion

    cutoff = utc(as_of)
    fixtures = {f["fixture_id"]: f for f in report.get("fixtures", [])}
    groups, domains, rejected = defaultdict(list), {}, Counter()
    deferred = Counter()

    def key_for(fid, field, domain):
        key = (fid, field)
        domains[key] = domain
        groups[key]  # Keep explicit required-but-missing fields.
        return key

    def add(fid, field, domain, value, source, *, quote_time=None):
        key = key_for(fid, field, domain)
        try:
            groups[key].append(Observation.from_value(
                canonical_id("fixture", fid), field, source["provider"], value,
                source["observed_at"], source.get("available_at", source["observed_at"]),
                source["source_url"], source["evidence_sha256"], True, quote_time))
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected["INVALID_SOURCE_OBSERVATION"] += 1

    for fid in fixtures:
        for field in REQUIRED:
            key_for(fid, field, "sports")
        key_for(fid, "weather.forecast", "weather")
        key_for(fid, "news.context", "news")

    collection = report.get("sports_supplement", {}).get("the_stats_api") or {}
    for fid, fixture in fixtures.items():
        raw_context = collection.get("context_by_fixture", {}).get(fid)
        if not raw_context:
            continue
        try:
            context = validate_stats_context({**raw_context, "version": collection["version"],
                "provider": collection["provider"], "observed_at": collection["observed_at"]},
                fixture, cutoff)
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected["INVALID_STATS_CONTEXT"] += 1
            continue
        if context["status"] != "MATCHED":
            rejected["MISSING_LINK"] += 1
            continue
        mapping = context["mapping"]
        for fact in context["facts"]:
            if fact["status"] != "AVAILABLE":
                continue
            category, data = fact["category"], fact["data"]
            if category in {"match_odds", "player_goal_market"}:
                deferred[category] += 1  # Products/timestamps require roadmap 3.1/3.2.
                continue
            subject = fact.get("subject_id")
            side = "home" if subject == mapping["home_team_id"] else "away" if subject == mapping["away_team_id"] else None
            prefix = (side + ".season" if category == "team_season_stats" and side else
                      side + ".availability" if category == "injuries_suspensions" and side else
                      "lineup" if category == "lineup" else category + ":" + subject)
            source = {**fact["source"], "evidence_sha256": fact["source"]["content_hash"]}
            for name, value in data.items():
                if name.endswith("_id"):
                    continue
                add(fid, prefix + "." + name, "sports", value, source)

    receipt = artifacts.get("candidate_input", {}).get("observed_at")
    for candidate in artifacts.get("candidate_input", {}).get("candidates", []):
        if not isinstance(candidate, dict):
            rejected["INVALID_QUOTE_OBSERVATION"] += 1
            continue
        fid = candidate.get("fixture_id")
        if fid not in fixtures:
            rejected["MISSING_LINK"] += 1
            continue
        if not _same_scope(candidate, fixtures[fid]):
            rejected["MISSING_LINK"] += 1
            continue
        provider = candidate.get("quote_provider")
        if not _provider_url(candidate.get("quote_source_id"), ODDS_HOSTS.get(provider)):
            rejected["INVALID_QUOTE_SOURCE"] += 1
            continue
        product_fields = ("bookmaker", "market_key", "selection_key", "period", "settlement", "region", "currency")
        if (candidate.get("quote_available") is not True or
                any(not isinstance(candidate.get(name), str) or not candidate[name].strip()
                    for name in product_fields)):
            rejected["INVALID_QUOTE_PRODUCT"] += 1
            continue
        # Provider-specific bookmaker identity remains separate until a verified
        # cross-provider product mapping exists. Do not compare unlike markets.
        product = [fid, provider, candidate.get("bookmaker"), candidate.get("market_key"),
                   candidate.get("selection_key"), candidate.get("period"),
                   candidate.get("settlement"), candidate.get("region"), candidate.get("currency")]
        try:
            add(fid, "odds:" + _hash(product), "odds", candidate.get("decimal_price"),
                {"provider": provider, "observed_at": receipt,
                 "source_url": candidate.get("quote_source_id"), "evidence_sha256": _hash(candidate)},
                quote_time=candidate.get("quoted_at"))
        except (ValueError, TypeError, OverflowError):
            rejected["INVALID_QUOTE_OBSERVATION"] += 1

    for fid, fixture in fixtures.items():
        weather = fixture.get("weather") or {}
        if not isinstance(weather, dict) or not _same_scope(weather, fixture):
            rejected["MISSING_LINK"] += 1
            continue
        if weather.get("status") == "SOURCED" and weather.get("raw_payload_hash"):
            if not _provider_url(weather.get("source_url"), "api.open-meteo.com"):
                rejected["INVALID_WEATHER_SOURCE"] += 1
                continue
            add(fid, "weather.forecast", "weather", weather,
                {"provider": "open_meteo", "observed_at": weather.get("observed_at"),
                 "source_url": weather.get("source_url"), "evidence_sha256": weather["raw_payload_hash"]})
    for claim in report.get("grok_review", {}).get("claims", []):
        try:
            if not isinstance(claim, dict):
                raise TypeError("structured news assertion required")
            assertion, _, fetched = _assertion(claim, cutoff.isoformat(timespec="microseconds"))
            fid = assertion["fixture_id"]
            if fid not in fixtures:
                raise ValueError("unknown news fixture")
            # This remains a sourced UNVERIFIED assertion, never an established fact.
            add(fid, "news.context", "news", assertion, {"provider": "grok",
                "observed_at": assertion["published_at"], "available_at": fetched,
                "source_url": assertion["source_url"], "evidence_sha256": _hash(assertion)})
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected["INVALID_NEWS_PROVENANCE"] += 1

    results, counts, providers, domain_counts, reasons = [], Counter(), Counter(), {}, Counter()
    matrix = {(domain, provider): Counter() for domain, sources in PRIORITIES.items()
              for provider in sources}
    fixture_counts = {fid: Counter() for fid in fixtures}
    for key, observations in sorted(groups.items()):
        fid, field = key
        domain = domains[key]
        age = timedelta(minutes=30) if domain == "odds" else timedelta(hours=2) if ".availability." in field or field.startswith("lineup.") else timedelta(hours=24)
        decision = resolve_field(observations, domain=domain, as_of=cutoff, max_age=age)
        for provider in PRIORITIES[domain]:
            cell = matrix[domain, provider]
            cell["expected_fields"] += 1
            audited = [row for row in decision["freshness_order"] if row["provider"] == provider]
            current_ids = set(decision["conflict"]["observation_ids"])
            values = {item.value_json for item in observations if item.provider == provider
                      and item.to_record()["observation_id"] in current_ids}
            usable = any(row["eligible"] for row in audited) and len(values) <= 1
            cell["available_fields"] += usable
            cell["missing_fields"] += not usable
            cell["conflicting_fields"] += len(values) > 1
            cell["rejected_observations"] += sum(not row["eligible"] for row in audited)
            if decision["selected"] and decision["selected"]["provider"] == provider:
                cell["selected_fields"] += 1
                cell["fallback_fields"] += PRIORITIES[domain].index(provider) > 0
        results.append({"fixture_id": fid, "field": field, "domain": domain,
                        "resolution": decision, "observations": [o.to_record() for o in observations]})
        counts[decision["status"]] += 1
        if decision["conflict"]["status"] == "CONFLICT":
            counts["CONFLICT"] += 1
            if decision["status"] == "MISSING":
                counts["UNRESOLVED_CONFLICT"] += 1
        fixture_counts[fid][decision["status"]] += 1
        domain_counts.setdefault(domain, Counter())[decision["status"]] += 1
        if decision["selected"]:
            providers[decision["selected"]["provider"]] += 1
        if decision["resolution_reason"].startswith("FALLBACK"):
            counts["FALLBACK"] += 1
        for omission in decision["rejected"]:
            reasons[omission["reason"]] += 1
    summary = {"version": VERSION, "mode": "SHADOW", "effective_data_path": "LEGACY_UNCHANGED",
        "status": "PARTIAL" if counts["RESOLVED"] and counts["MISSING"] else
                  "AVAILABLE" if counts["RESOLVED"] else "MISSING",
        "counts": {name: counts[name] for name in ("RESOLVED", "MISSING", "FALLBACK")},
        "conflict_fields": counts["CONFLICT"],
        "unresolved_conflict_fields": counts["UNRESOLVED_CONFLICT"],
        "provider_matrix": [{"domain": domain, "provider": provider,
            "role": "PRIMARY" if PRIORITIES[domain][0] == provider else "FALLBACK",
            **{name: values[name] for name in ("expected_fields", "available_fields", "selected_fields",
                "missing_fields", "fallback_fields", "rejected_observations", "conflicting_fields")}}
            for (domain, provider), values in matrix.items()],
        "matrix_scope": "COLLECTED_NORMALIZED_FIELDS_ONLY",
        "by_provider": dict(providers),
        "by_domain": {k: dict(v) for k, v in domain_counts.items()},
        "rejected_inputs": dict(rejected), "rejected_observations": dict(reasons),
        "deferred_categories": dict(deferred),
        "fixtures": [{"fixture_id": fid, **dict(values)} for fid, values in fixture_counts.items()],
        "as_of": cutoff.isoformat(), "external_requests": 0,
        "odds_comparison_scope": "PROVIDER_SPECIFIC_UNTIL_VERIFIED_PRODUCT_LINK",
        "evidence_hash_basis": {"sports": "RAW_RESPONSE", "weather": "CANONICAL_RAW_RESPONSE",
                                "odds": "NORMALIZED_CANDIDATE", "news": "NORMALIZED_SOURCED_ASSERTION"}}
    return {"summary": summary, "fields": results}
