"""Prospective, price-aware paper evidence for the deterministic team supervisor.

No provider calls, reconstructed odds, LLM opinions or actual wager selection
enter the decision cohort. Bootstrap intervals are estimates, not guarantees.
"""

from __future__ import annotations

import math
import random
import re
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.portfolio import PROFILE_IDS
from goal_analysis.learning._metrics import _summarize, digest
from goal_analysis.storage.portfolio_ledger import _stamp

PROFILES = tuple(PROFILE_IDS)
BERLIN = ZoneInfo("Europe/Berlin")
POLICY = {
    "version": "grail-prospective-unit-v2-roster30",
    "min_tickets": 260, "min_days": 90, "min_unique_fixtures": 300,
    "result_maturity_hours": 72, "decision_cooldown_days": 30,
    "bootstrap_draws": 10000, "family_size": len(PROFILES), "family_alpha": 0.05,
    "promotion_margin_pct": 5.0, "suspension_margin_pct": -5.0,
    "unit_stake": 1.0, "max_combined_price": 1000.0,
    "scope": "PROSPECTIVE_PAPER_UNIT",
    "uncertainty": "DAY_CLUSTER_BOOTSTRAP_BONFERRONI_ESTIMATE_NOT_PROOF",
    "limits": [
        "A papírhozam eredeti API-árak elméleti szorzatán alapul, nem tényleges nyereség.",
        "A napi klaszterezés és harmincprofilos korrekció becslés; az ismételt napi vizsgálat nem ad garantált hibaarányt.",
        "A személyiségek közös adatokat és modelleket használhatnak; harminc tag nem harminc független bizonyíték.",
        "A jövő, az árak tényleges elérhetősége és a modell kalibráltsága nem bizonyított; költség nincs levonva.",
        "Csak a belépés és a legutóbbi kézi státuszváltás utáni, változatlan verziójú publikáció dönthet.",
        "A hiányos vagy vitatott érett rekord nem rejthető el egy kedvező részhalmaz kiválasztásával.",
    ],
}
REASONS = {
    "INITIAL_MEMBER": "Még nincs a taghoz tartozó aktuális felügyeleti értékelés.",
    "MANUAL_OVERRIDE": "Kézi státuszmódosítás; az új bizonyítékgyűjtés innen indul.",
    "NO_PROSPECTIVE_DATA": "Még nincs a belépés után publikált, megfelelő új minta.",
    "UNKNOWN_VERSION": "Az eredeti stratégia-verzió nem ellenőrizhető.",
    "CURRENT_VERSION_MISMATCH": "A meglévő minta nem a most futó kód és beállítások változatához tartozik.",
    "AMBIGUOUS_PROVENANCE": "Egy vagy több ajánlás ár- vagy verzióeredete nem ellenőrizhető.",
    "MISSING_ODDS": "Egy vagy több érett ajánlás eredeti szorzója hiányzik.",
    "UNRESOLVED_RESULTS": "Egy vagy több érett ajánlás eredménye vagy void-elszámolása nem egyértelmű.",
    "INSUFFICIENT_TICKETS": "Nincs még legalább 260 értékelhető új papírszelvény.",
    "INSUFFICIENT_DAYS": "Nincs még legalább 90 külön értékelhető nap.",
    "INSUFFICIENT_FIXTURES": "Nincs még legalább 300 külön értékelhető mérkőzés.",
    "SHARED_FIXTURES_ACROSS_DAYS": "Napok között ismétlődő mérkőzések miatt a klaszterezés nem elégséges.",
    "DECISION_COOLDOWN": "A státuszváltás óta még nem telt el a 30 napos megfigyelési idő.",
    "INTERVAL_INCONCLUSIVE": "A becsült bizonytalansági tartomány nem támaszt alá státuszváltást.",
    "SUSPENDED_REQUIRES_MANUAL_RETURN": "A felfüggesztett tag csak kézi döntéssel térhet vissza próbára.",
    "EVIDENCE_POSITIVE": "A minősített új papírminta alsó hozambecslése meghaladja a +5%-os határt.",
    "EVIDENCE_NEGATIVE": "A minősített új papírminta felső hozambecslése is -5% alatt van.",
    "KEEP_ACTIVE": "A minősített minta alapján nem indokolt az aktív státusz felfüggesztése.",
}


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _version(record):
    manifest = record.get("strategy_manifest")
    if not isinstance(manifest, dict):
        return None
    try:
        expected = manifest.get("manifest_sha256")
        base = {k: v for k, v in manifest.items() if k not in {"manifest_sha256", "profiles"}}
        profile = str(record.get("profile_id", "")).lower()
        recorded = manifest.get("profiles", {}).get(profile, {}).get("version_id")
        if (not _hex(expected) or digest(base) != expected
                or record.get("strategy_manifest_sha256") != expected
                or record.get("version") != recorded
                or not _hex(manifest.get("code_sha256"))
                or not _hex(manifest.get("settings_sha256"))
                or not manifest.get("source_files") or not record.get("original_run_match")):
            return None
    except (TypeError, AttributeError, ValueError):
        return None
    # The full manifest hash, not a short display ID, isolates settings/code changes.
    return expected


def _quote_valid(leg):
    quote = leg.get("quote_provenance") or {}
    bookmaker = quote.get("bookmaker")
    if not isinstance(bookmaker, str) or not bookmaker.strip():
        return False
    try:
        url = urlsplit(quote.get("quote_source_id") or "")
        if url.scheme != "https" or url.username or url.password or url.fragment:
            return False
        if quote.get('quote_provider') == 'the_stats_api':
            from goal_analysis.providers.the_stats_odds import valid_quote_source

            return valid_quote_source({**quote, 'fixture_id': leg.get('fixture_id')})
        if quote.get("quote_provider") == "api_football":
            query = parse_qs(url.query)
            canonical = re.fullmatch(r"api[_-]football:([1-9][0-9]*)", str(leg.get("fixture_id", "")))
            if not canonical:
                return False
            fixture = canonical[1]
            if quote.get("api_football_fixture_id") is not None and str(quote["api_football_fixture_id"]) != fixture:
                return False
            period, market = leg.get("market", ":").split(":", 1)
            # Only reviewed pre-match IDs; live IDs use a different catalogue.
            # API-Sports documentation/football/v3 (odds/bets), plus the
            # project's recorded-shape API-Football normalizer contract.
            expected_bet = {("FULL_TIME", "h2h"): 1, ("FULL_TIME", "btts"): 8,
                            ("FIRST_HALF", "h2h"): 13}.get((period, market))
            if re.fullmatch(r"totals_[0-9]+_5", market):
                expected_bet = {"FULL_TIME": 5, "FIRST_HALF": 6}.get(period)
            if expected_bet is None or str(quote.get("provider_market_id")) != str(expected_bet):
                return False
            return (url.hostname == "v3.football.api-sports.io" and url.path == "/odds"
                    and fixture.isdigit() and query.get("fixture") == [fixture]
                    and len(query.get("bookmaker", [])) == 1
                    and query["bookmaker"][0].isdigit()
                    and bookmaker.endswith("_api_football_" + query["bookmaker"][0])
                    and query.get("bet") == [str(expected_bet)])
        if quote.get("quote_provider") == "the_odds_api":
            parts = url.path.strip("/").split("/")
            if len(parts) != 6:
                return False
            period, market = leg.get("market", ":").split(":", 1)
            expected_id = "d223-" + canonical_sha256({
                "fixture_id": leg["fixture_id"], "event_id": parts[4], "market": market,
                "selection": leg["selection"], "period": period, "bookmaker": bookmaker,
            })[:24]
            # Existing producer's original ID binds both providers' event IDs to
            # the exact market, period, selection and bookmaker. Never guess it.
            if (quote.get("quote_candidate_id") or quote.get("source_candidate_id")
                    or quote.get("candidate_id")) != expected_id:
                return False
            return (url.hostname == "api.the-odds-api.com" and len(parts) == 6
                    and parts[:2] == ["v4", "sports"] and parts[3] == "events"
                    and parts[5] == "odds" and bool(parts[4])
                    and (not quote.get("sport_key") or parts[2] == quote["sport_key"]))
    except (TypeError, ValueError):
        pass
    return False


def _unit_return(record):
    legs = record["legs"]
    if not legs or any(type(l.get("decimal_price")) not in (int, float)
                       or not math.isfinite(l["decimal_price"])
                       or not 1 < l["decimal_price"] <= POLICY["max_combined_price"] for l in legs):
        return None, "MISSING_ODDS"
    if any(not _quote_valid(leg) for leg in legs):
        return None, "AMBIGUOUS_PROVENANCE"
    books = {(l["quote_provenance"].get("bookmaker"), l["quote_provenance"].get("region"),
              l["quote_provenance"].get("currency")) for l in legs}
    price = math.prod(l["decimal_price"] for l in legs)
    if len(books) != 1 or not math.isfinite(price) or price > POLICY["max_combined_price"]:
        return None, "AMBIGUOUS_PROVENANCE"
    # Partial void products cannot establish the bookmaker's settled combination.
    if record.get("outcome") not in {"WON", "LOST"} or any(
            l.get("outcome") not in {"WON", "LOST"} for l in legs):
        return None, "UNRESOLVED_RESULTS"
    expected = "WON" if all(l["outcome"] == "WON" for l in legs) else "LOST"
    if expected != record["outcome"]:
        return None, "UNRESOLVED_RESULTS"
    return price - 1 if expected == "WON" else -1.0, None


def _interval(daily, identity):
    """Resample whole publication-day clusters; never treat legs as iid bets."""
    groups = [daily[key] for key in sorted(daily)]
    rng = random.Random(int(digest(identity)[:16], 16))
    draws = []
    for _ in range(POLICY["bootstrap_draws"]):
        chosen = rng.choices(groups, k=len(groups))
        draws.append(sum(x[0] for x in chosen) / sum(x[1] for x in chosen) * 100)
    draws.sort()
    tail = POLICY["family_alpha"] / (2 * POLICY["family_size"])
    return draws[int(len(draws) * tail)], draws[min(len(draws) - 1, math.ceil(len(draws) * (1 - tail)))]


def build_cohorts(measured, members, as_of, *, expected_manifest=None):
    """Measure full history descriptively, but make decisions only prospectively."""
    cutoff = _stamp(as_of)
    records = measured.get("records", {})
    output, evidence = {}, []
    for member in members:
        profile = member["profile_id"]
        start = _stamp(member["evidence_since"]) if member.get("evidence_since") else cutoff
        all_profile = [r for r in records.get("published", [])
                       if str(r.get("profile_id", "")).lower() == profile
                       and start < _stamp(r["created_at"]) <= cutoff]
        all_profile.sort(key=lambda r: (_stamp(r["created_at"]), r["id"]))
        version = _version(all_profile[-1]) if all_profile else None
        expected = expected_manifest.get("manifest_sha256") if isinstance(expected_manifest, dict) else None
        mismatch = expected_manifest is not None and (not _hex(expected) or version != expected)
        if expected_manifest is not None:
            version = expected if _hex(expected) else None
        current = [r for r in all_profile if _version(r) == version]
        if expected_manifest is not None and not _hex(expected):
            current = []
        # Distinct immutable versions are never pooled to pass minimum thresholds.
        values, refs, daily, fixtures = [], {}, defaultdict(lambda: [0.0, 0]), defaultdict(set)
        counters = {"pending": 0, "missing_odds": 0, "ambiguous_provenance": 0,
                    "unresolved_results": 0, "immature_tickets": 0}
        for record in current:
            for ref in [*record["source_refs"], record.get("run_reference")]:
                if ref:
                    refs[digest(ref)] = ref
            latest = max((_stamp(l["kickoff"]) for l in record["legs"]), default=cutoff)
            if latest + timedelta(hours=POLICY["result_maturity_hours"]) > cutoff:
                counters["immature_tickets"] += 1
                counters["pending"] += int(record.get("outcome") in {"PENDING", "REVIEW"})
                continue
            value, error = _unit_return(record)
            if record.get("publication_scope") not in {"LIVE", "PAPER_AUDITION"} or not version:
                error = "AMBIGUOUS_PROVENANCE"
            if error:
                counters[{"MISSING_ODDS": "missing_odds", "AMBIGUOUS_PROVENANCE": "ambiguous_provenance",
                          "UNRESOLVED_RESULTS": "unresolved_results"}[error]] += 1
                counters["pending"] += int(record.get("outcome") in {"PENDING", "REVIEW"})
                continue
            day = _stamp(record["created_at"]).astimezone(BERLIN).date().isoformat()
            daily[day][0] += value
            daily[day][1] += 1
            values.append(value)
            for leg in record["legs"]:
                fixtures[leg["fixture_id"]].add(day)
        metrics = {
            "scope": POLICY["scope"], "version": version, "tickets": len(values),
            "total_tickets": len(current), "other_version_tickets": len(all_profile) - len(current),
            "days": len(daily), "unique_fixtures": len(fixtures), **counters,
            "shared_fixtures_across_days": sum(len(days) > 1 for days in fixtures.values()),
            "roi_pct": sum(values) / len(values) * 100 if values else None,
            "unit_net": sum(values) if values else None, "ci_low_pct": None, "ci_high_pct": None,
            "first_publication": current[0]["created_at"] if current else None,
            "last_publication": current[-1]["created_at"] if current else None,
            "as_of": cutoff.isoformat(),
        }
        blockers = []
        if not current:
            blockers.append("NO_PROSPECTIVE_DATA")
        if version is None:
            blockers.append("UNKNOWN_VERSION")
        if mismatch:
            blockers.append("CURRENT_VERSION_MISMATCH")
        if measured.get("coverage", {}).get("invalid_records", 0):
            blockers.append("AMBIGUOUS_PROVENANCE")
        for field, code in (("missing_odds", "MISSING_ODDS"),
                            ("ambiguous_provenance", "AMBIGUOUS_PROVENANCE"),
                            ("unresolved_results", "UNRESOLVED_RESULTS"),
                            ("shared_fixtures_across_days", "SHARED_FIXTURES_ACROSS_DAYS")):
            if metrics[field]:
                blockers.append(code)
        for field, minimum, code in (("tickets", "min_tickets", "INSUFFICIENT_TICKETS"),
                                      ("days", "min_days", "INSUFFICIENT_DAYS"),
                                      ("unique_fixtures", "min_unique_fixtures", "INSUFFICIENT_FIXTURES")):
            if metrics[field] < POLICY[minimum]:
                blockers.append(code)
        if not blockers:
            metrics["ci_low_pct"], metrics["ci_high_pct"] = _interval(daily, [profile, version, daily])
        for scope in ("actual", "research", "frozen"):
            scoped = [r for r in records.get(scope, []) if str(r.get("profile_id", "")).lower() == profile]
            metrics[scope] = _summarize(scoped, actual=scope == "actual")
            for record in scoped:
                for ref in [*record["source_refs"], record.get("run_reference")]:
                    if ref:
                        refs[digest(ref)] = ref
        item = {"scope": POLICY["scope"], "profile_id": profile, "version": version,
                "metrics": deepcopy(metrics), "source_refs": [refs[k] for k in sorted(refs)]}
        item["evidence_id"] = "team-ev-" + digest(item)[:24]
        evidence.append(item)
        output[profile] = {"metrics": metrics, "qualification": {
            "eligible": not blockers, "sample_complete": not blockers, "blockers": blockers},
            "evidence_ids": [item["evidence_id"]]}
    return output, evidence


def assess(cohorts, previous_statuses, as_of):
    """Pure deterministic status decisions; cannot execute code or place a wager."""
    cutoff, decisions = _stamp(as_of), []
    for member in previous_statuses:
        profile, previous = member["profile_id"], member["status"]
        cohort = deepcopy(cohorts[profile])
        blockers = cohort["qualification"]["blockers"]
        if previous == "SUSPENDED":
            blockers.append("SUSPENDED_REQUIRES_MANUAL_RETURN")
        since = member.get("status_since")
        if since and cutoff < _stamp(since) + timedelta(days=POLICY["decision_cooldown_days"]):
            blockers.append("DECISION_COOLDOWN")
        metrics = cohort["metrics"]
        status, decision = previous, "KEEP"
        reason = blockers[0] if blockers else "INTERVAL_INCONCLUSIVE"
        if not blockers:
            if previous == "PROBATION" and metrics["ci_low_pct"] > POLICY["promotion_margin_pct"]:
                status, decision, reason = "ACTIVE", "PROMOTE", "EVIDENCE_POSITIVE"
            elif previous == "ACTIVE" and metrics["ci_high_pct"] < POLICY["suspension_margin_pct"]:
                status, decision, reason = "SUSPENDED", "SUSPEND", "EVIDENCE_NEGATIVE"
            elif previous == "ACTIVE":
                reason = "KEEP_ACTIVE"
        cohort["qualification"]["eligible"] = not blockers
        training = [{"code": code, "title": REASONS[code],
                     "detail": "Adatgyűjtési vagy ellenőrzési feladat; nem módosít modell-súlyokat, kódot vagy tétet.",
                     "evidence_ids": cohort["evidence_ids"]} for code in blockers[:4]]
        decisions.append({**member, **cohort, "previous_status": previous, "status": status,
                          "decision": decision, "reason_code": reason, "reason": REASONS[reason],
                          "eligible_for_live": status == "ACTIVE", "training_plan": training,
                          "awards": [], "podium": None})
    # Earned-only descriptive ranking. This is not a significance test between
    # profiles and never implies actual-money profit or a calibrated model.
    ranked = sorted((member for member in decisions if member["status"] == "ACTIVE"
                     and member["qualification"]["eligible"]
                     and member["metrics"]["ci_low_pct"] is not None
                     and member["metrics"]["ci_low_pct"] > POLICY["promotion_margin_pct"]),
                    key=lambda m: (-m["metrics"]["ci_low_pct"], -m["metrics"]["roi_pct"], m["profile_id"]))
    for rank, member in enumerate(ranked, 1):
        badge = {"earned": True, "qualified": True, "title": "Minősített papírteljesítmény",
                 "scope": POLICY["scope"], "evidence_ids": member["evidence_ids"],
                 "detail": "Előre rögzített küszöböket teljesítő papírminta; nem igazolt tényleges profit."}
        member["awards"] = [badge]
        if rank <= 3:
            member["podium"] = {**badge, "rank": rank, "title": "Minősített papírminta rangsora",
                                 "detail": "Leíró sorrend a becsült alsó hozamhatár alapján; a tagok közötti különbség nem bizonyított."}
    return decisions
