"""Budgeted, append-only Grok shadow research after Arthur publication.

This module neither changes recommendations nor writes the portfolio ledger.
Local reservations limit subsequent requests; they cannot cap provider billing
while an individual request is in flight.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import closing
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents.grok_packet_accounting import (
    incomplete_refusal_inventory,
    packet_accounting,
)
from goal_analysis.agents.grok_packet_tables import wire_packet
from goal_analysis.agents.grok_sports_data import form_context, history_counts, model_context
from goal_analysis.agents.grok_stats_data import stats_contexts
from goal_analysis.agents.motivation_expert import compact_motivation_context
from goal_analysis.config.portfolio import PROFILE_IDS
from goal_analysis.jobs.sports_context_archive import archive_grok_result

MODEL = "grok-4.7"
POLICY_VERSION = "grok-all-ticket-shadow-v7-lossless-readable-packet-accounting"
BERLIN = ZoneInfo("Europe/Berlin")
CACHE_MINUTES = 60
LEASE_SECONDS = 210
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_SECRET = re.compile(r"(?:xai-|nvapi-|sk-)[A-Za-z0-9_-]{12,}|Bearer\s+\S+", re.IGNORECASE)
_REVIEWABLE = {"READY", "REVIEWED", "DRAFT", "MODEL_READY"}
_RESULT_KEYS = {"status", "model", "claims", "critiques", "citations", "usage",
                "diagnostics", "error_code"}


class PacketTooLarge(OverflowError):
    """A refused, fully assembled packet keeps its honest category inventory."""

    def __init__(self, coverage):
        super().__init__("PACKET_TOO_LARGE")
        self.coverage = coverage


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _provider_version():
    """A prompt/validator change invalidates previously cached findings."""
    agents = Path(__file__).parents[1] / "agents"
    return hashlib.sha256(b"\0".join((agents / name).read_bytes() for name in (
        "grok_research.py", "grok_sports_data.py", "grok_packet_tables.py", "grok_packet_dense.py",
        "grok_stats_data.py", "motivation_expert.py", "weather_expert.py", "grok_packet_readable.py", "grok_packet_accounting.py"))).hexdigest()


def _time(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            return None
        return stamp.astimezone(UTC)
    except ValueError:
        return None


def _number(value, lower=0, upper=1_000_000):
    return type(value) in (int, float) and math.isfinite(value) and lower <= value <= upper


def _text(value, limit=160):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(
        ord(char) < 32 for char in value) and not _SECRET.search(value)


def _url(value):
    if not _text(value, 2048):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _sports_source(value):
    """Retain sports request identity, never credentials or arbitrary query data."""
    base = _url(value)
    if base is None:
        return None
    if urlsplit(value).hostname == 'api.thestatsapi.com':
        from goal_analysis.normalization.live_source import stats_source_url

        try:
            return stats_source_url(value)
        except (ValueError, TypeError):
            return None
    allowed = {"id", "fixture", "league", "season", "team", "last", "date", "bookmaker", "bet"}
    query = [(key, item) for key, item in parse_qsl(urlsplit(value).query)
             if key in allowed and re.fullmatch(r"[0-9-]{1,16}", item)]
    return base + ("?" + urlencode(query) if query else "")


def _history_evidence(leg, now):
    evidence = leg.get("evidence", [])
    if isinstance(evidence, list) and len(evidence) > 12:
        raise OverflowError("PACKET_TOO_LARGE")
    result = {}
    for item in evidence[:12] if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        observed = _time(item.get("observed_at"))
        if observed is None or observed > now or not all(_text(item.get(key), limit)
                for key, limit in (("category", 80), ("claim_type", 80),
                                   ("statement", 4000), ("source_id", 256))):
            continue
        counts = history_counts(item["statement"], item["category"])
        statement = item["statement"]
        prefix = f"{leg.get('period')} {leg.get('market_key')} / {leg.get('selection_key')}; "
        statement = statement.removeprefix(prefix)
        for original, compact in (
                ("hazai oldal ", "home "), ("vendég oldal ", "away "),
                ("Utolsó 5: ", "last5: "), ("helyszíni/aktuális arány: ", "venue/current: "),
                ("Megfigyelt piaci mintázat, nem becsült nyerési esély.", "")):
            statement = statement.replace(original, compact)
        statement = statement.strip()
        clean = {key: item[key] for key in ("category", "claim_type", "source_id")}
        # Generated historical frequencies are numeric data, not disposable
        # prose. Send all counts losslessly before any excerpt shortening.
        if counts:
            clean["history_counts"] = counts
            statement = "Observed match counts; not win probabilities."
        if len(statement) > 320:
            # Do not silently chop an unknown numeric observation in half.
            raise ValueError("SPORTS_EVIDENCE_EXCEEDS_LIMIT")
        clean.update(statement=statement,
                     statement_truncated=False,
                     observed_at=observed.isoformat())
        for key in ("strength", "reliability"):
            if _number(item.get(key), 0, 1):
                clean[key] = round(item[key], 6)
        identifier = _digest(clean)[:16]
        result[identifier] = {"evidence_id": identifier, **clean}
    return result


def _short_references(packet):
    """Local symbols are deterministic; full provenance remains in shared tables."""
    def relabel(rows, field, prefix):
        ordered = sorted(rows, key=lambda row: _json({key: value for key, value in row.items()
                                                     if key != field}))
        mapping = {row[field]: f"{prefix}{index}" for index, row in enumerate(ordered, 1)}
        for row in rows:
            row[field] = mapping[row[field]]
        rows.sort(key=lambda row: int(row[field][len(prefix):]))
        return mapping

    history = relabel(packet["evidence_sources"], "source_ref", "h")
    quotes = relabel(packet["quote_sources"], "quote_ref", "q")
    for row in packet["evidence"]:
        row["source_ref"] = history[row["source_ref"]]
    evidence = relabel(packet["evidence"], "evidence_id", "e")
    for row in packet["selections"]:
        if "quote_ref" in row:
            row["quote_ref"] = quotes[row["quote_ref"]]
        if "evidence_ids" in row:
            row["evidence_ids"] = sorted(evidence[key] for key in row["evidence_ids"])
    selections = relabel(packet["selections"], "selection_id", "s")
    for row in packet["recommendations"]:
        for leg in row["legs"]:
            leg["selection_id"] = selections[leg["selection_id"]]
    return packet


def _compact_prose(packet, target=100_000):
    """Adapt excerpts only; numeric fields and every ticket/leg remain intact."""
    before, changed = len(_json(packet)), set()
    for context_limit, weather_limit, evidence_limit in ((48, 80, 110), (32, 48, 90), (24, 32, 70)):
        if len(_json(wire_packet(packet))) <= target:
            break
        for fixture in packet["fixtures"]:
            weather = fixture.get("weather", {})
            text = weather.get("summary")
            if isinstance(text, str) and len(text) > weather_limit:
                weather["summary"] = text[:weather_limit].rstrip(" …") + " …"
                # This marker survives the provider's weather whitelist.
                changed.add((fixture["fixture_id"], "weather"))
            for index, context in enumerate(fixture.get("existing_context", [])):
                text = context.get("summary")
                if isinstance(text, str) and len(text) > context_limit:
                    context["summary"] = text[:context_limit].rstrip(" …") + " …"
                    context["summary_truncated"] = True
                    changed.add((fixture["fixture_id"], "context", index))
        for item in packet["evidence"]:
            text = item["statement"]
            # Historical match counts often live in prose. Preserve every
            # numeric token rather than clipping a useful 12/20 observation.
            if len(text) > evidence_limit and not re.search(r"\d", text[evidence_limit:]):
                item["statement"] = text[:evidence_limit].rstrip(" …") + " …"
                item["statement_truncated"] = True
                changed.add((item["evidence_id"], "statement"))
    return {"symbolic_references": True, "before_prose_compaction_characters": before,
            "input_characters": len(_json(packet)), "additional_truncated_text_fields": len(changed)}


def _expert_contexts(report, now):
    """Compact existing experts once; no inference, network or prior Grok verdicts."""
    advisory = report.get("expert_advisory", {})
    observed = _time(advisory.get("observed_at")) if isinstance(advisory, dict) else None
    if observed is None or observed > now:
        return {}
    fixtures = {row["fixture_id"]: row for row in report.get("fixtures", [])}
    result = {fid: {"observed_at": observed.isoformat()} for fid in fixtures}
    motivation = advisory.get("motivation", {})
    stamp = _time(motivation.get("observed_at"))
    if stamp and stamp <= observed:
        compact = compact_motivation_context(motivation)
        for row in compact["fixtures"]:
            if row["fixture_id"] not in result:
                continue
            clean = {"version": compact["version"], "observed_at": stamp.isoformat(),
                     **{key: row[key] for key in ("stakes", "claims", "claims_omitted",
                                                  "review_required")}}
            table = row.get("standings_context")
            if table:
                clean["standings"] = {key: table[key] for key in (
                    "status", "league_id", "season", "observed_at", "source_url", "rules_status",
                    "teams", "missing_teams", "ambiguous_teams")}
                clean["standings"]["source_url"] = _sports_source(table["source_url"])
                # Names already live on the fixture. Side-keyed facts share one
                # column schema across the whole batch instead of per-match tables.
                clean["standings"]["teams"] = {team["side"]: {key: value for key, value in team.items()
                    if key not in {"side", "team_name", "nearby_provider_zones", "games_remaining"}}
                    for team in table["teams"]}
            if row.get("standing_evidence"):
                clean["verified_stakes"] = row["standing_evidence"]
            result[row["fixture_id"]]["motivation"] = clean
    weather = advisory.get("weather", {})
    stamp = _time(weather.get("observed_at"))
    if stamp and stamp <= observed:
        for row in weather.get("fixtures", []):
            fid = row["fixture_id"]
            if fid not in result:
                continue
            clean = {"version": weather["version"], "observed_at": stamp.isoformat(),
                     **{key: row[key] for key in ("status", "reason", "score_multiplier",
                                                 "live_weight_eligible")},
                     "team_samples": {side: team.get("matched_history_count", 0)
                                      for side, team in row.get("teams", {}).items()}}
            for key in ("data_hash", "policy_hash"):
                if weather.get(key):
                    clean[key] = weather[key]
            forecast = row.get("forecast") or fixtures[fid].get("weather") or {}
            received = _time(forecast.get("observed_at"))
            if received and received <= stamp and (source := _url(forecast.get("source_url"))):
                clean.update(source_url=source, source_observed_at=received.isoformat())
            effects = []
            for side, team in row.get("teams", {}).items():
                validation = team.get("validation", {})
                for metric, signal in team.get("signals", {}).items():
                    if not signal.get("sufficient"):
                        continue
                    checks = validation.get("metrics", {}).get(metric, {})
                    effects.append({"side": side, "metric": metric,
                        "analog_count": signal["analog"]["count"],
                        "control_count": signal["control"]["count"],
                        "analog_ess": signal["analog"]["ess"],
                        "control_ess": signal["control"]["ess"],
                        "shrunk_effect": signal["shrunk_effect"], "interval": signal["interval"],
                        "training_count": validation["training_count"],
                        "holdout_count": validation["holdout_count"],
                        "retrospective_passed": checks["retrospective"]["passed"],
                        "forecast_passed": checks["forecast"]["passed"],
                        "current_anchor_matches": team.get("current_strength_matches_validation_anchor", False)})
            if effects:
                clean["effects"] = effects
            result[fid]["weather"] = clean
    return result


def _fixture_extra(report, now):
    """Existing sourced context is input data, never a prior review conclusion."""
    result, experts = {}, _expert_contexts(report, now)
    supplements = stats_contexts(report, now)
    refreshed = {item.get("fixture_id"): item.get("forecast") for item in
                 report.get("expert_advisory", {}).get("weather", {}).get("fixtures", [])
                 if isinstance(item, dict) and isinstance(item.get("forecast"), dict)}
    rows = report.get("fixtures", [])
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("fixture_id"), str):
            continue
        extra = {key: row[key] for key, limit in (("competition", 80), ("competition_type", 40),
                 ("provider_status", 40), ("venue_name", 160), ("venue_city", 160),
                 ("venue_country", 80), ("api_football_home_team_id", 40),
                 ("api_football_away_team_id", 40), ("api_football_league_id", 40),
                 ("source_provider", 40), ("config_league_id", 40), ("provider_fixture_id", 40),
                 ("provider_home_team_id", 40), ("provider_away_team_id", 40),
                 ("provider_league_id", 40), ("provider_season_id", 40))
                 if _text(row.get(key), limit)}
        if type(row.get("season")) is int and 1900 <= row["season"] <= 2200:
            extra["season"] = row["season"]
        if source := _sports_source(row.get("source_id")):
            extra["source_id"] = source
        numeric = model_context(report, row["fixture_id"], now)
        if numeric:
            extra["numeric_models"] = numeric
        if row["fixture_id"] in experts:
            extra["expert_context"] = experts[row["fixture_id"]]
        if row["fixture_id"] in supplements:
            extra["the_stats_api"] = supplements[row["fixture_id"]]
        weather = row.get("weather")
        new_weather = refreshed.get(row["fixture_id"])
        if new_weather and (received := _time(new_weather.get("observed_at"))) and received <= now:
            previous = _time(weather.get("observed_at")) if isinstance(weather, dict) else None
            if previous is None or previous < received:
                weather = new_weather
        if isinstance(weather, dict):
            clean = {key: weather[key] for key in ("status", "provider")
                     if _text(weather.get(key), 60)}
            if _text(weather.get("summary"), 2000):
                clean["summary"] = weather["summary"][:120]
            for key in ("temperature_c", "precipitation_probability_pct", "precipitation_mm",
                        "weather_code", "wind_speed_kmh", "wind_gusts_kmh"):
                if _number(weather.get(key), -100, 1000):
                    clean[key] = weather[key]
            if _time(weather.get("forecast_time")):
                clean["forecast_time"] = _time(weather["forecast_time"]).isoformat()
            if (received := _time(weather.get("observed_at"))) and received <= now:
                clean["observed_at"] = received.isoformat()
                if _text(weather.get("weather_kind"), 40):
                    clean["weather_kind"] = weather["weather_kind"]
            if _url(weather.get("source_url")):
                clean["source_url"] = _url(weather["source_url"])
            if clean:
                extra["weather"] = clean
        result[row["fixture_id"]] = extra
    for group in (("generated_tickets",) if "generated_tickets" in report else ("recommendations", "tickets")):
        for card in report.get(group, []):
            for leg in card.get("legs", []) if isinstance(card, dict) else []:
                if not isinstance(leg, dict) or leg.get("fixture_id") not in result:
                    continue
                form = form_context(leg, now)
                if not form:
                    continue
                forms = result[leg["fixture_id"]].setdefault("historical_form", {})
                previous = forms.get(form["period"])
                if previous and previous["profiles"] != form["profiles"]:
                    raise ValueError("CONFLICTING_HISTORICAL_FORM")
                # Source timestamps of every selected leg still travel in the
                # evidence table; identical shared form data needs one origin.
                forms.setdefault(form["period"], form)
    astra = report.get("astra", {})
    if not isinstance(astra, dict):
        return result
    observed = _time(astra.get("observed_at"))
    context = astra.get("fixture_context", [])
    for row in context if isinstance(context, list) and observed and observed <= now else []:
        if not isinstance(row, dict) or not isinstance(row.get("fixture_id"), str):
            continue
        if not all(_text(row.get(key), limit) for key, limit in
                   (("topic", 40), ("status", 40), ("summary", 4000))):
            continue
        items = result.setdefault(row["fixture_id"], {}).setdefault("existing_context", [])
        if len(items) >= 5:
            continue
        urls = row.get("source_urls", [])
        items.append({"topic": row["topic"], "status": row["status"],
                      "summary": row["summary"][:80],
                      "summary_truncated": len(row["summary"]) > 80,
                      "source_urls": [url for raw in urls[:3] if (url := _url(raw))]
                      if isinstance(urls, list) else [],
                      "observed_at": observed.isoformat(), "publication_time_verified": False})
    return result


def _config(root):
    defaults = {"enabled": False, "mode": "SHADOW", "model": MODEL,
                "daily_budget_usd": 1.0, "monthly_budget_usd": 20.0,
                "max_requests_per_day": 2, "cache_minutes": CACHE_MINUTES}
    try:
        raw = json.loads((Path(root) / "config/grok.json").read_text("utf-8-sig"))
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            return defaults
        if raw.get("mode") != "SHADOW" or raw.get("model", MODEL) != MODEL:
            return defaults
        for key, ceiling in (("daily_budget_usd", 1), ("monthly_budget_usd", 20)):
            value = raw.get(key, defaults[key])
            if not _number(value, 0.000001, ceiling):
                return defaults
            defaults[key] = float(value)
        count = raw.get("max_requests_per_day", 2)
        if type(count) is not int or not 1 <= count <= 2:
            return defaults
        defaults.update(enabled=True, max_requests_per_day=count)
        return defaults
    except (OSError, ValueError):
        return defaults


def enabled(root):
    return _config(root)["enabled"]


def _base(status, **extra):
    return {"status": status, "mode": "SHADOW", "automatic_changes": False, **extra}


def _packet(report, now):
    """Send every valid READY ticket, with shared fixtures/selections only once."""
    if not isinstance(report, dict):
        raise TypeError("INVALID_REPORT")
    run_id = report.get("run_id")
    observed = _time(report.get("observed_at"))
    if (not isinstance(run_id, str) or not _ID.fullmatch(run_id)
            or observed is None or observed > now):
        raise ValueError("INVALID_REPORT_IDENTITY")
    target = report.get("date")
    if not isinstance(target, str) or date.fromisoformat(target).isoformat() != target:
        raise ValueError("INVALID_DATE")
    rows, grouped = [], {}
    for field in (("generated_tickets",) if "generated_tickets" in report else ("recommendations", "tickets")):
        source = report.get(field, [])
        if not isinstance(source, list) or len(source) > 1000:
            raise ValueError("INVALID_RECOMMENDATIONS")
        rows.extend(source)
    for row in rows:
        if not isinstance(row, dict):
            continue
        profile = row.get("profile_id")
        if isinstance(profile, str):
            grouped.setdefault(profile, []).append(row)
    excluded, all_recs, all_fixtures, selections, sources, versions = [], [], {}, {}, {}, {}
    evidence, fixture_extra = {}, _fixture_extra(report, now)
    locked = set()
    for profile, candidates in sorted(grouped.items()):
        reason = None
        if profile not in PROFILE_IDS:
            excluded.append({"profile_id": profile[:160], "reason_code": "UNKNOWN_PROFILE"})
            continue
        candidates = [row for row in candidates if row.get("status") in _REVIEWABLE]
        if not candidates:
            excluded.append({"profile_id": profile, "reason_code": "NO_COMPLETE_TICKET"})
            continue
        # A mirrored recommendation and main ticket are one ticket, not two.
        signatures = {_digest([{key: leg.get(key) for key in (
            "fixture_id", "market_key", "selection_key", "period", "decimal_price")}
            for leg in row.get("legs", []) if isinstance(leg, dict)])
            for row in candidates if isinstance(row.get("legs"), list)}
        if len(signatures) != 1:
            excluded.append({"profile_id": profile, "reason_code": "AMBIGUOUS_PROFILE"})
            continue
        row = candidates[0]
        legs = row.get("legs")
        if not isinstance(legs, list) or not legs:
            excluded.append({"profile_id": profile, "reason_code": "NO_COMPLETE_TICKET"})
            continue
        if len(legs) > 20:
            raise OverflowError("PACKET_TOO_LARGE")
        fixtures, local_selections, references = {}, {}, []
        for leg in legs:
            if not isinstance(leg, dict):
                reason = "INVALID_LEG"
                break
            identifier, kickoff = leg.get("fixture_id"), _time(leg.get("kickoff"))
            if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                    or identifier in fixtures or kickoff is None
                    or kickoff.astimezone(BERLIN).date().isoformat() != target
                    or not _text(leg.get("home_team")) or not _text(leg.get("away_team"))
                    or not all(_text(leg.get(key), 80)
                               for key in ("market_key", "selection_key", "period"))
                    or not _number(leg.get("decimal_price"), 1.000001, 10000)):
                reason = "INVALID_LEG"
                break
            fixture = {"fixture_id": identifier, "home_team": leg["home_team"],
                       "away_team": leg["away_team"], "kickoff": kickoff.isoformat(),
                       **fixture_extra.get(identifier, {})}
            if identifier in all_fixtures and all_fixtures[identifier] != fixture:
                raise ValueError("CONFLICTING_FIXTURE")
            fixtures[identifier] = fixture
            clean = {key: leg[key] for key in
                     ("fixture_id", "market_key", "selection_key", "period", "decimal_price")}
            probability = leg.get("model_probability")
            if _number(probability, 0, 1):
                clean["model_probability"] = round(probability, 6)
            detail = leg.get("model_details", {})
            numeric = detail.get("shared_model_probabilities", {}) if isinstance(detail, dict) else {}
            if isinstance(numeric, dict):
                numeric = {key: round(value, 6) for key, value in numeric.items()
                           if key in {"gauss", "kepler", "laplace"} and _number(value, 0, 1)}
                if numeric:
                    clean["model_probabilities"] = numeric
            for key, lower, upper in (("model_stability", 0, 1), ("estimated_edge", -1, 1000000)):
                if _number(leg.get(key), lower, upper):
                    clean[key] = round(leg[key], 6)
            for key in ("quote_provider", "confidence_kind"):
                if _text(leg.get(key), 80):
                    clean[key] = leg[key]
            for key in ("bookmaker", "region", "currency"):
                value = leg.get(key, row.get(key))
                if _text(value, 80):
                    clean[key] = value
            if quote_source := _sports_source(leg.get("quote_source_id")):
                clean["quote_source_id"] = quote_source
            if leg.get("quoted_at") is not None:
                quote_time = _time(leg["quoted_at"])
                if quote_time is None or quote_time > now:
                    raise ValueError("INVALID_QUOTE_TIMESTAMP")
                clean["quoted_at"] = quote_time.isoformat()
            if leg.get('quote_provider') == 'the_stats_api':
                from goal_analysis.providers.the_stats_odds import (
                    QUOTE_PROVENANCE,
                    valid_quote_source,
                )

                if not valid_quote_source(leg) or _time(leg.get('quote_observed_at')) > now:
                    raise ValueError('INVALID_NATIVE_QUOTE_PROVENANCE')
                clean.update({key: leg[key] for key in QUOTE_PROVENANCE if key in leg})
            if isinstance(detail, dict) and detail.get("calibration") in {"unvalidated", "validated", "unknown"}:
                clean["calibration"] = detail["calibration"]
            model_times = [report.get("model_cutoff")]
            if isinstance(detail, dict):
                model_times.append(detail.get("cutoff"))
            if any(value is not None and (_time(value) is None or _time(value) > now)
                   for value in model_times):
                for key in ("model_probability", "model_probabilities", "model_stability",
                            "estimated_edge", "confidence_kind", "calibration"):
                    clean.pop(key, None)
            local_evidence = _history_evidence(leg, now)
            if local_evidence:
                evidence.update(local_evidence)
                clean["evidence_ids"] = sorted(local_evidence)
            selection_id = _digest(clean)[:16]
            local_selections[selection_id] = {"selection_id": selection_id, **clean}
            references.append({"selection_id": selection_id})
        if reason:
            excluded.append({"profile_id": profile, "reason_code": reason})
            continue
        # Stable IDs bind identical semantics across timestamp-only reruns.
        rid = _digest([profile, references])[:16]
        source_id = row.get("recommendation_id", row.get("ticket_id", profile))
        sources[rid] = source_id if isinstance(source_id, str) and _ID.fullmatch(source_id) else profile
        rec = {"profile_id": profile, "recommendation_id": rid, "legs": references,
               "combined_price": round(math.prod(leg["decimal_price"] for leg in legs), 6),
               "protected": profile == "daily223", "status": row["status"]}
        persona = row.get("persona", {})
        if isinstance(persona, dict) and isinstance(persona.get("target_odds_band"), dict):
            style = {"min_legs": persona.get("min_legs"), "max_legs": persona.get("max_legs"),
                     "target_odds_min": persona["target_odds_band"].get("min"),
                     "target_odds_max": persona["target_odds_band"].get("max")}
            if all(_number(value, 1, 10000) for value in style.values()):
                rec["style"] = style
        all_recs.append(rec)
        all_fixtures.update(fixtures)
        selections.update(local_selections)
        versions[profile] = row.get("strategy_version")
        if any(_time(fixture["kickoff"]) <= now + timedelta(minutes=15)
               for fixture in fixtures.values()):
            excluded.append({"profile_id": profile, "reason_code": "PREMATCH_LOCKOUT"})
            locked.add(profile)
    if len(all_recs) > 40 or len(all_fixtures) > 160 or len(selections) > 800:
        raise OverflowError("PACKET_TOO_LARGE")
    evidence_sources, quote_sources = {}, {}
    for item in evidence.values():
        metadata = {key: item.pop(key) for key in ("source_id", "observed_at", "claim_type")}
        reference = _digest(metadata)[:16]
        evidence_sources[reference] = {"source_ref": reference, **metadata}
        item["source_ref"] = reference
        if item.get("statement_truncated") is False:
            item.pop("statement_truncated")
    for item in selections.values():
        metadata = {key: item.pop(key) for key in ("quote_provider", "quoted_at", "bookmaker",
                    "region", "currency", "quote_source_id", 'quote_observed_at', 'quote_source_sha256',
                    'quote_time_basis', 'provider_bookmaker', 'provider_market_id') if key in item}
        if metadata:
            reference = _digest(metadata)[:16]
            quote_sources[reference] = {"quote_ref": reference, **metadata}
            item["quote_ref"] = reference
    objective = {"target_win_count": 3, "target_winning_odds_sum": 30, "stake_eur": 1,
                 "total_ticket_count": len(all_recs), "total_stake_eur": len(all_recs),
                 "breakeven_winning_odds_sum": len(all_recs), "aspiration_not_guarantee": True}
    semantic = {"date": target, "objective": objective,
                "fixtures": [all_fixtures[key] for key in sorted(all_fixtures)],
                "selections": [selections[key] for key in sorted(selections)],
                "evidence": [evidence[key] for key in sorted(evidence)],
                "evidence_sources": [evidence_sources[key] for key in sorted(evidence_sources)],
                "quote_sources": [quote_sources[key] for key in sorted(quote_sources)],
                "recommendations": all_recs,
                "strategy_identity": {"profiles": versions,
                    "manifest": report.get("strategy_manifest", {}).get("manifest_sha256")
                    if isinstance(report.get("strategy_manifest"), dict) else None}}
    selected = [rec for rec in all_recs if rec["profile_id"] not in locked]
    selected_ids = {leg["selection_id"] for rec in selected for leg in rec["legs"]}
    fixture_ids = {selections[key]["fixture_id"] for key in selected_ids}
    evidence_ids = {eid for sid in selected_ids for eid in selections[sid].get("evidence_ids", [])}
    evidence_source_ids = {evidence[key]["source_ref"] for key in evidence_ids}
    quote_source_ids = {selections[key]["quote_ref"] for key in selected_ids
                        if "quote_ref" in selections[key]}
    semantic_hash = _digest(semantic)
    packet = {"date": target, "cutoff": now.isoformat(), "objective": objective,
              "fixtures": [all_fixtures[key] for key in sorted(fixture_ids)],
              "selections": [selections[key] for key in sorted(selected_ids)],
              "evidence": [evidence[key] for key in sorted(evidence_ids)],
              "evidence_sources": [evidence_sources[key] for key in sorted(evidence_source_ids)],
              "quote_sources": [quote_sources[key] for key in sorted(quote_source_ids)],
              "recommendations": selected}
    packet = _short_references(deepcopy(packet))
    # The provider validator canonicalizes a few strings after this stage.
    # Reserve a small margin inside the unchanged 100k input ceiling.
    assembled = deepcopy(packet)
    compaction = _compact_prose(packet, target=99_000)
    wire = wire_packet(packet)
    wire_characters = len(_json(wire))
    compaction.update(expanded_characters=len(_json(packet)), input_characters=wire_characters,
                      lossless_column_tables=wire_characters < len(_json(packet)))
    coverage = {"profile_ids": [row["profile_id"] for row in selected],
                "fixture_ids": sorted(fixture_ids), "profile_count": len(selected),
                "fixture_count": len(fixture_ids), "selection_count": len(selected_ids),
                "ready_profile_count": len(grouped), "excluded": excluded,
                "current_source_ids": sources, "semantic_packet_sha256": semantic_hash,
                "scope": "ALL_TICKETS_ONE_BATCH", "daily223_review_only": True,
                "supplied_sports_data": {
                    "history_count_entries": sum("history_counts" in e for e in packet["evidence"]),
                    "fixtures_with_full_model_predictions": sum("numeric_models" in f for f in packet["fixtures"]),
                    "fixtures_with_historical_form": sum("historical_form" in f for f in packet["fixtures"]),
                    "quote_source_count": len(packet["quote_sources"]),
                    "fresh_sports_requests": 0,
                    "policy": "USE_EXISTING_DATA_NO_SPORTS_REFETCH"},
                "compaction": compaction,
                "packet_accounting": packet_accounting(
                    assembled, packet, wire, excluded, refused=wire_characters > 100_000)}
    if wire_characters > 100_000:
        raise PacketTooLarge(coverage)
    return packet, coverage, run_id, _digest(report)


def _path(root):
    return Path(root) / "data/arthur/grok.sqlite3"


def _open(root):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS grok_requests (
            request_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, run_id TEXT NOT NULL,
            report_hash TEXT NOT NULL, target_date TEXT NOT NULL, budget_day TEXT NOT NULL,
            budget_month TEXT NOT NULL, created_at TEXT NOT NULL, reservation_usd REAL NOT NULL,
            model TEXT NOT NULL, packet_json TEXT NOT NULL, coverage_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS grok_requests_cache ON grok_requests(cache_key, created_at);
        CREATE INDEX IF NOT EXISTS grok_requests_day ON grok_requests(budget_day);
        CREATE TABLE IF NOT EXISTS grok_results (
            request_id TEXT PRIMARY KEY REFERENCES grok_requests(request_id),
            completed_at TEXT NOT NULL, status TEXT NOT NULL,
            charged_usd REAL NOT NULL, known_cost_usd REAL, result_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manual_review_requests (
            request_id TEXT PRIMARY KEY REFERENCES grok_requests(request_id),
            budget_day TEXT NOT NULL UNIQUE
        );
        CREATE TRIGGER IF NOT EXISTS manual_reviews_no_update BEFORE UPDATE ON manual_review_requests
            BEGIN SELECT RAISE(ABORT, 'Manual review allowance is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS manual_reviews_no_delete BEFORE DELETE ON manual_review_requests
            BEGIN SELECT RAISE(ABORT, 'Manual review allowance is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS grok_requests_no_update BEFORE UPDATE ON grok_requests
            BEGIN SELECT RAISE(ABORT, 'Grok requests are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS grok_requests_no_delete BEFORE DELETE ON grok_requests
            BEGIN SELECT RAISE(ABORT, 'Grok requests are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS grok_results_no_update BEFORE UPDATE ON grok_results
            BEGIN SELECT RAISE(ABORT, 'Grok results are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS grok_results_no_delete BEFORE DELETE ON grok_results
            BEGIN SELECT RAISE(ABORT, 'Grok results are immutable'); END;
    """)
    connection.commit()
    return connection


def _usage(connection, day, config):
    month = day[:7]
    rows = connection.execute("""
        SELECT q.budget_day, q.reservation_usd, r.charged_usd, r.known_cost_usd
        FROM grok_requests q LEFT JOIN grok_results r USING(request_id)
        WHERE q.budget_month = ?
    """, (month,)).fetchall()
    result = {"day_requests": 0, "day_charged_usd": 0.0, "month_charged_usd": 0.0,
              "day_known_cost_usd": 0.0, "month_known_cost_usd": 0.0,
              "day_reserved_unknown_usd": 0.0, "month_reserved_unknown_usd": 0.0,
              "daily_limit_usd": config["daily_budget_usd"],
              "monthly_limit_usd": config["monthly_budget_usd"],
              "max_daily_requests": config["max_requests_per_day"], "budget_day": day}
    for row in rows:
        charged = row["charged_usd"] if row["charged_usd"] is not None else row["reservation_usd"]
        known = row["known_cost_usd"] or 0.0
        for scope in ("month", "day") if row["budget_day"] == day else ("month",):
            result[f"{scope}_charged_usd"] += charged
            result[f"{scope}_known_cost_usd"] += known
            result[f"{scope}_reserved_unknown_usd"] += charged if row["known_cost_usd"] is None else 0
        result["day_requests"] += row["budget_day"] == day
    # Read-only snapshots must also work on databases created before this migration.
    has_manual_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_review_requests'"
    ).fetchone()
    used = bool(has_manual_table and connection.execute(
        "SELECT 1 FROM manual_review_requests WHERE budget_day=?", (day,)
    ).fetchone())
    result.update(manual_extra_review_used=used,
                  max_daily_requests_with_explicit_manual_review=min(3, config["max_requests_per_day"] + 1))
    return result


def _reserve(root, packet, coverage, run_id, report_hash, config, now, *, manual_review=False):
    if type(manual_review) is not bool:
        raise ValueError("manual_review must be an explicit boolean")
    provider_version = _provider_version()
    coverage.update(policy_version=POLICY_VERSION, provider_sha256=provider_version)
    cache_key = _digest([POLICY_VERSION, provider_version, config["model"],
                         coverage["semantic_packet_sha256"]])
    day = now.astimezone(BERLIN).date().isoformat()
    with closing(_open(root)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cached = connection.execute("""
                SELECT q.request_id, q.run_id, q.report_hash, q.created_at, q.coverage_json,
                       r.completed_at, r.result_json
                FROM grok_requests q JOIN grok_results r USING(request_id)
                WHERE q.cache_key = ? AND r.status IN ('COMPLETE','PARTIAL')
                ORDER BY q.created_at DESC LIMIT 1
            """, (cache_key,)).fetchone()
            if cached:
                created, completed = _time(cached["created_at"]), _time(cached["completed_at"])
                if created is not None and completed is not None and created <= completed <= now:
                    return _base("CACHED", request_id=cached["request_id"], run_id=run_id,
                                 coverage=json.loads(cached["coverage_json"]),
                                 current_coverage=coverage,
                                 cached_from_run_id=cached["run_id"],
                                 cached_from_report_hash=cached["report_hash"],
                                 cache_age_seconds=(now - created).total_seconds(),
                                 cache_stale=now - created >= timedelta(minutes=CACHE_MINUTES),
                                 result=json.loads(cached["result_json"]),
                                 created_at=cached["created_at"], completed_at=cached["completed_at"])
            pending = connection.execute("""
                SELECT q.created_at FROM grok_requests q LEFT JOIN grok_results r USING(request_id)
                WHERE r.request_id IS NULL ORDER BY q.created_at DESC LIMIT 1
            """).fetchone()
            if pending:
                stamp = _time(pending["created_at"])
                if stamp is None or now - stamp <= timedelta(seconds=LEASE_SECONDS):
                    return _base("BUSY", error_code="REQUEST_ALREADY_RESERVED")
            # A clock rollback must not make prior usage disappear into the future.
            future = connection.execute("SELECT 1 FROM grok_requests WHERE created_at > ? LIMIT 1",
                                        (now.isoformat(),)).fetchone()
            if future:
                return _base("BUDGET", error_code="CLOCK_ROLLBACK")
            usage = _usage(connection, day, config)
            remaining = min(config["daily_budget_usd"] - usage["day_charged_usd"],
                            config["monthly_budget_usd"] - usage["month_charged_usd"])
            extra = usage["day_requests"] >= config["max_requests_per_day"]
            manual_allowed = (manual_review and not usage["manual_extra_review_used"]
                              and usage["day_requests"]
                              < min(3, config["max_requests_per_day"] + 1))
            if (extra and not manual_allowed) or remaining <= 0.000000001:
                return _base("BUDGET", error_code="LOCAL_BUDGET_EXHAUSTED", usage=usage)
            request_id = uuid.uuid4().hex
            coverage["manual_extra_review"] = extra
            connection.execute("INSERT INTO grok_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                               (request_id, cache_key, run_id, report_hash, packet["date"], day,
                                day[:7], now.isoformat(), remaining, config["model"],
                                _json(packet), _json(coverage)))
            if extra:
                connection.execute("INSERT INTO manual_review_requests VALUES (?,?)", (request_id, day))
            connection.commit()
            return {"request_id": request_id, "reservation_usd": remaining, "manual_extra_review": extra}
        finally:
            if connection.in_transaction:
                connection.rollback()


def _safe_result(raw):
    if not isinstance(raw, dict) or raw.get("status") not in {
            "COMPLETE", "PARTIAL", "UNAVAILABLE", "SKIPPED_NO_DATA"}:
        raise ValueError("INVALID_PROVIDER_RESULT")
    result = {key: deepcopy(value) for key, value in raw.items() if key in _RESULT_KEYS}
    if len(_json(result)) > 131072:
        raise ValueError("OVERSIZED_PROVIDER_RESULT")
    code = result.get("error_code")
    if code is not None and (not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_]{1,80}", code)):
        result["error_code"] = "PROVIDER_ERROR"
    result["model"] = MODEL
    return result


def _analyze(root, report, *, advisor=None, now=None, completion_clock=None, manual_review=False):
    """One bounded batch; no retries, side effects to input, or strategy changes."""
    config = _config(root)
    if not config["enabled"]:
        return _base("DISABLED")
    if type(manual_review) is not bool:
        return _base("UNAVAILABLE", error_code="INVALID_MANUAL_REVIEW")
    explicit_now = now is not None
    clock = _time(now if explicit_now else datetime.now(UTC))
    if clock is None or (completion_clock is not None and not callable(completion_clock)):
        return _base("UNAVAILABLE", error_code="INVALID_CLOCK")
    try:
        packet, coverage, run_id, report_hash = _packet(report, clock)
    except OverflowError as exc:
        coverage = getattr(exc, "coverage", {"packet_accounting":
                           incomplete_refusal_inventory(report)})
        return _base("PACKET_TOO_LARGE", error_code="INPUT_LIMIT_EXCEEDED", coverage=coverage)
    except (ValueError, TypeError):
        return _base("UNAVAILABLE", error_code="INVALID_REPORT")
    if not packet["recommendations"]:
        return _base("NO_ELIGIBLE_FIXTURES", coverage=coverage, run_id=run_id)
    try:
        reservation = _reserve(root, packet, coverage, run_id, report_hash, config, clock,
                               manual_review=manual_review)
    except (OSError, sqlite3.Error):
        return _base("UNAVAILABLE", error_code="STORE_UNAVAILABLE")
    if "status" in reservation:
        if reservation["status"] == "CACHED":
            reservation["news_archive"] = archive_grok_result(
                root, reservation["result"], reservation["request_id"],
                reservation["completed_at"], reservation["result"].get("model", config["model"]),
            )
        return reservation
    try:
        if advisor is None:
            from goal_analysis.agents.grok_research import GrokAdvisor

            # The single package includes every ticket and its supplied sports
            # context. xAI tool use can legitimately exceed 150 seconds; keep
            # one request, no retry, but allow it to finish within five minutes.
            advisor = GrokAdvisor(model=config["model"], timeout=300)
        result = _safe_result(advisor.review(deepcopy(packet)))
    except Exception:  # noqa: BLE001 - no raw provider exceptions or credential-bearing content
        result = {"status": "UNAVAILABLE", "model": config["model"],
                  "error_code": "PROVIDER_UNAVAILABLE", "usage": {"cost_usd": None}}
    usage = result.get("usage")
    cost = usage.get("cost_usd") if isinstance(usage, dict) else None
    # An explicit no-network response is the only zero-cost inference permitted.
    if (cost is None and isinstance(usage, dict) and usage.get("request_count") == 0):
        cost = 0.0
    cost = float(cost) if _number(cost) else None
    charged = cost if cost is not None else reservation["reservation_usd"]
    completion_clock_status = "FIXED_INPUT_TIME" if explicit_now else "SYSTEM_CLOCK"
    if completion_clock is not None:
        try:
            completed = _time(completion_clock())
            if completed is None:
                raise ValueError("Invalid completion timestamp")
            completion_clock_status = "PROVIDED_CLOCK"
        except Exception:  # noqa: BLE001 - preserve the paid result if an injected clock fails
            completed = datetime.now(UTC)
            completion_clock_status = "SYSTEM_CLOCK_FALLBACK"
    else:
        completed = clock if explicit_now else datetime.now(UTC)
    completed = max(completed, clock)
    try:
        with closing(_open(root)) as connection:
            connection.execute("INSERT INTO grok_results VALUES (?,?,?,?,?,?)", (
                reservation["request_id"], completed.isoformat(), result["status"],
                charged, cost, _json(result)))
            connection.commit()
    except (OSError, sqlite3.Error):
        # Reservation remains charged if completion persistence fails.
        return _base("UNAVAILABLE", error_code="RESULT_NOT_PERSISTED",
                     request_id=reservation["request_id"])
    news_archive = archive_grok_result(
        root, result, reservation["request_id"], completed, result["model"],
    )
    return _base(result["status"], request_id=reservation["request_id"], run_id=run_id,
                 created_at=clock.isoformat(), completed_at=completed.isoformat(),
                 cache_stale=False, cache_age_seconds=0,
                 coverage=coverage, result=result, charged_usd=charged,
                 cost_known=cost is not None, news_archive=news_archive,
                 manual_extra_review=reservation["manual_extra_review"],
                 completion_clock_status=completion_clock_status)


def analyze(root, report, *, advisor=None, now=None, completion_clock=None, manual_review=False):
    """An optional sidecar must never break the already published core result.

    ``now`` alone retains the fixed-time simulation behavior. Live callers that
    provide a start timestamp must also pass their advancing ``completion_clock``;
    it is sampled after the response. Without ``now``, system UTC is used at both
    boundaries. Cached results always retain their persisted original completion.
    manual_review=True is reserved for a direct user-triggered review: one extra
    request per Berlin day, without bypassing monetary limits or unknown charges.
    Scheduled and automatic callers must retain the False default.
    """
    try:
        return _analyze(root, report, advisor=advisor, now=now, completion_clock=completion_clock,
                        manual_review=manual_review)
    except Exception:  # noqa: BLE001 - safe last boundary, never include exception text
        return _base("UNAVAILABLE", error_code="ADVISORY_UNAVAILABLE")


def snapshot(root, day=None):
    """Bounded local read only: never create a DB, claim work, or call an API."""
    now = datetime.now(UTC)
    target = day or now.astimezone(BERLIN).date().isoformat()
    if not isinstance(target, str) or date.fromisoformat(target).isoformat() != target:
        raise ValueError("Invalid date")
    config = _config(root)
    result = _base("NO_DATA" if config["enabled"] else "DISABLED", enabled=config["enabled"],
                   model=config["model"], day=target, latest=None)
    path = _path(root)
    if not path.is_file():
        return result
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=2)) as connection:
            connection.row_factory = sqlite3.Row
            result["usage"] = _usage(connection, target, config)
            row = connection.execute("""
                SELECT q.request_id, q.run_id, q.target_date, q.created_at, q.coverage_json,
                       r.completed_at, r.status, r.result_json
                FROM grok_requests q LEFT JOIN grok_results r USING(request_id)
                WHERE q.target_date = ? ORDER BY q.created_at DESC LIMIT 1
            """, (target,)).fetchone()
            if row:
                stamp = _time(row["created_at"])
                status = row["status"] or (
                    "BUSY" if stamp and now - stamp <= timedelta(seconds=LEASE_SECONDS)
                    else "INTERRUPTED")
                result["latest"] = {"request_id": row["request_id"], "run_id": row["run_id"],
                    "date": row["target_date"], "created_at": row["created_at"],
                    "completed_at": row["completed_at"], "status": status,
                    "cache_stale": bool(stamp and now - stamp >= timedelta(minutes=CACHE_MINUTES)),
                    "cache_age_seconds": (now - stamp).total_seconds() if stamp else None,
                    "coverage": json.loads(row["coverage_json"]),
                    "result": json.loads(row["result_json"]) if row["result_json"] else None}
                if config["enabled"]:
                    result["status"] = status
    except (OSError, sqlite3.Error, ValueError, TypeError):
        result.update(status="UNAVAILABLE", error_code="STORE_UNAVAILABLE", latest=None)
    return result
