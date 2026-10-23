"""One coherent run of the Arthur desk, with immutable reports and bounded AI review."""

from __future__ import annotations

import json
import os
import urllib.parse
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from itertools import zip_longest
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from goal_analysis.agents import canonical_sha256
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import MODEL_PROFILE_IDS, validate_settings
from goal_analysis.engine.publication_window import allows_new_leg, allows_new_ticket
from goal_analysis.features.daily_223_history import enrich_daily223_input
from goal_analysis.jobs.daily_223_live import run_daily223_live
from goal_analysis.jobs.league_scope import MINIMUM_PENDING_FIXTURES, resolve_default_leagues
from goal_analysis.quote_metadata import quote_metadata
from goal_analysis.tipsters.catalog import TIPSTER_PROFILE_IDS

BERLIN = ZoneInfo("Europe/Berlin")


def safe_error(error: object) -> str:
    message = str(error)
    for name in ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY", "OPENAI_API_KEY", "THE_STATS_API_KEY", "THESTATSAPI_API_KEY", "XAI_API_KEY"):
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
    if any(p in settings["enabled_profiles"] for p in ("ritmusor", "gauss", *TIPSTER_PROFILE_IDS)):
        extras.add("btts")
    if "h1_over05" in settings["enabled_profiles"]:
        extras.add("totals_h1")
    live["extra_markets"] = sorted(extras)
    return live


def _review_packets(enriched: dict, preliminary: dict, maximum: int) -> list[dict]:
    """Prioritize selected legs and the quote cohorts that can replace DAILY_223 legs."""
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
    daily_products = set()
    for ticket in preliminary.get("tickets", []):
        for leg in ticket["legs"]:
            identifier = leg["candidate_id"]
            if identifier not in seen and identifier in candidates:
                chosen.append(identifier)
                seen.add(identifier)
                product = (
                    leg["fixture_id"], leg["market_key"], leg["selection_key"], leg["period"]
                )
                products.add(product)
                if ticket.get("profile_id") == "daily223":
                    daily_products.add(product)

    # Astra assessments are candidate/quote specific. A cautious assessment can
    # make the immutable DAILY_223 solver select the same economic product from
    # another bookmaker. Review those equivalent quote candidates up front so
    # the final ticket never escapes review merely because its quote id changed.
    daily_quote_cohort = sorted(
        (
            identifier
            for identifier, item in candidates.items()
            if (
                item["fixture_id"],
                item["market_key"],
                item["selection_key"],
                item["period"],
            )
            in daily_products
        ),
        key=lambda identifier: (
            candidates[identifier].get("bookmaker", ""),
            -float(candidates[identifier].get("decimal_price", 0)),
            identifier,
        ),
    )
    for identifier in daily_quote_cohort:
        if len(chosen) >= maximum:
            break
        if identifier not in seen:
            chosen.append(identifier)
            seen.add(identifier)
    # A fixed accumulator needs all its legs reviewed together. Round-robin
    # sampling can otherwise spend the budget on one leg from every persona,
    # leaving no complete specialist ticket. Keep DAILY's preceding priority.
    from goal_analysis.engine.specialist_contracts import SPECIALIST_CONTRACTS

    for ticket in preliminary.get("profile_proposals", []):
        if ticket["profile_id"] not in SPECIALIST_CONTRACTS:
            continue
        missing = [leg["candidate_id"] for leg in ticket.get("legs", [])
                   if leg["candidate_id"] not in seen and leg["candidate_id"] in candidates]
        if len(chosen) + len(missing) > maximum:
            continue
        for identifier in missing:
            chosen.append(identifier)
            seen.add(identifier)
            row = candidates[identifier]
            products.add((row["fixture_id"], row["market_key"], row["selection_key"], row["period"]))
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


def _merge_astra_reviews(base: dict, supplement: dict) -> dict:
    """Merge a bounded follow-up review without losing its provenance or usage."""
    merged = deepcopy(base)
    for key in ("reviews", "sources", "web_odds", "fixture_context", "rejected_web_quotes"):
        combined = []
        seen = set()
        left = base.get(key, [])
        right = supplement.get(key, [])
        left = left if isinstance(left, list) else []
        right = right if isinstance(right, list) else []
        for item in [*left, *right]:
            identity = canonical_sha256(item)
            if identity not in seen:
                combined.append(deepcopy(item))
                seen.add(identity)
        merged[key] = combined
    by_candidate = {}
    for item in merged.get("reviews", []):
        by_candidate[item.get("candidate_id")] = item
    merged["reviews"] = [by_candidate[key] for key in sorted(by_candidate) if key]
    usage = {}
    for key in set(base.get("usage", {})) | set(supplement.get("usage", {})):
        left, right = base.get("usage", {}).get(key), supplement.get("usage", {}).get(key)
        usage[key] = left + right if isinstance(left, (int, float)) and isinstance(right, (int, float)) else right if right is not None else left
    merged["usage"] = usage
    merged["status"] = (
        "COMPLETE" if base.get("status") == supplement.get("status") == "COMPLETE" else
        supplement.get("status", base.get("status", "UNAVAILABLE"))
    )
    merged["research_status"] = (
        "COMPLETE_WITH_SOURCES"
        if base.get("research_status") == supplement.get("research_status") == "COMPLETE_WITH_SOURCES"
        else supplement.get("research_status", base.get("research_status", "UNAVAILABLE"))
    )
    merged["reviewed_at"] = supplement.get("reviewed_at", base.get("reviewed_at"))
    merged["prompt_sha256"] = canonical_sha256(
        [base.get("prompt_sha256"), supplement.get("prompt_sha256")]
    )
    merged["message"] = "Astra review completed with traceable references, including bounded DAILY_223 follow-up coverage."
    return merged


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
            "by_profile": section.get("by_profile", [])[:12],
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
    selected_competition_ids: list[int] | None = None,
    weather_provider=None,
    model_predictor=None,
    model_artifact_dir: Path | None = None,
    team_plan: dict | None = None,
    nemotron_stylist=None,
    package_reviewer=None,
    expert_reviewer=None,
    history_sample_shadow: bool = False,
    player_specialist_shadow_root: Path | None = None,
    native_history_shadow_root: Path | None = None,
    native_option_shadow: bool = False,
) -> dict:
    """Injectable coordinator. No wager is ever placed or marked automatically."""
    from goal_analysis.engine.diversification import allocate
    from goal_analysis.engine.model_recommendations import build_model_recommendations
    from goal_analysis.engine.portfolio import build_portfolio, permissive
    from goal_analysis.engine.portfolio_preview import build_previews, preview_candidates
    from goal_analysis.engine.recommendations import build_recommendations, recommendation_packets
    from goal_analysis.models import predict_models
    from goal_analysis.tipsters import build_tipster_recommendations

    if type(history_sample_shadow) is not bool:
        raise ValueError('history_sample_shadow must be boolean')
    if type(native_option_shadow) is not bool or native_option_shadow and native_history_shadow_root is None:
        raise ValueError('native_option_shadow requires a boolean and explicit native history root')
    settings = validate_settings(settings)
    from goal_analysis.team.routing import apply_publication_policy, generation_plan, validate_plan

    team_plan = validate_plan(team_plan, settings["enabled_profiles"]) if team_plan is not None else generation_plan(settings["enabled_profiles"])
    requested_settings = deepcopy(settings)
    settings = {**settings, "enabled_profiles": [p for p in settings["enabled_profiles"]
                if p not in team_plan["blocked_profiles"]]}
    live_settings = {**settings, "enabled_profiles": team_plan["live_profiles"]}
    governed = any(p not in team_plan["live_profiles"] for p in requested_settings["enabled_profiles"]
                   if p not in (*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS))
    construction_kwargs = {"allowed_profiles": team_plan["live_profiles"]} if governed else {}
    started = clock()
    validate_target_day(target_date, started)
    run_id = f"{target_date.isoformat()}-{uuid4().hex[:12]}"
    if not settings["enabled_profiles"]:
        from goal_analysis.strategy_provenance import attach_strategy_provenance

        report = {"schema_version": 3, "module": "ARTHUR_PORTFOLIO", "run_id": run_id,
                  "date": target_date.isoformat(), "observed_at": started.isoformat(),
                  "finished_at": started.isoformat(), "construction_status": "TEAM_PAUSED",
                  "tickets": [], "recommendations": [], "profiles": [], "settings": requested_settings,
                  "astra": {"status": "NO_DATA", "message": "Nincs aktív vagy próbaidős agent; nem történt API/AI-hívás."},
                  "usage": {}, "real_wager_placed": False}
        apply_publication_policy(report, team_plan)
        attach_strategy_provenance(report, requested_settings)
        report["report_sha256"] = canonical_sha256(report)
        return {"schema_version": 3, "artifacts": {"report": report}}
    bundle = run_daily223_live(
        effective_live_config(config, settings),
        football_client,
        odds_feed,
        cache,
        target_date,
        clock,
        construct_legacy=False,
        allow_incomplete=permissive(settings),
        selected_competition_ids=selected_competition_ids,
        calendar_scope=resolve_default_leagues,
    )
    artifacts = bundle["artifacts"]
    legacy = artifacts["report"]
    if (artifacts.get('odds_access', {}).get('status') == 'AUTH_REQUIRED'
            and not artifacts.get('candidate_input', {}).get('candidates')):
        # A known authentication failure cannot be repaired by an LLM opinion.
        # Keep collected data visible; a fresh run after credential repair is
        # sufficient. There is no permanent strategy veto or synthetic ticket.
        from goal_analysis.strategy_provenance import attach_strategy_provenance

        report = {'schema_version': 3, 'module': 'ARTHUR_PORTFOLIO', 'run_id': run_id,
                  'date': target_date.isoformat(), 'observed_at': clock().isoformat(),
                  'finished_at': clock().isoformat(), 'construction_status': 'DATA_BLOCKED',
                  'reason': 'The Odds API HTTP 401/403; friss áras szelvényhez a hozzáférés helyreállítása szükséges.',
                  'blocked_stage': 'ODDS_AUTH_REQUIRED', 'tickets': [], 'recommendations': [],
                  'profiles': [{'profile_id': profile, 'status': 'DATA_BLOCKED'}
                               for profile in requested_settings['enabled_profiles']],
                  'fixtures': artifacts.get('fixtures', {}).get('records', []),
                  'calendar': artifacts.get('calendar', {}), 'settings': requested_settings,
                  'astra': {'status': 'NOT_REQUESTED', 'usage': {'requests': 0},
                            'message': 'Szorzóforrás-hitelesítési hibánál nem indult fizetett elemzés.'},
                  'usage': legacy['usage'], 'real_wager_placed': False}
        apply_publication_policy(report, team_plan)
        attach_strategy_provenance(report, requested_settings)
        report['report_sha256'] = canonical_sha256(report)
        artifacts['report'] = report
        return {'schema_version': 3, 'artifacts': artifacts}
    review = {
        "status": "UNAVAILABLE",
        "model": settings["openai"]["model"],
        "reviews": [],
        "usage": {},
        "message": "Astra-elemzés még nem történt.",
    }
    weather_by_fixture = {}
    candidate_context, context_attempt = {}, None
    context_mode = 'OFF'
    # Both numerical agents see the full selected fixture calendar and shared history.
    # They never inherit Arthur's shortlist, heuristic rankings, or LLM-adjusted scores.
    enabled_models = tuple(p for p in settings["enabled_profiles"] if p in MODEL_PROFILE_IDS)
    model_cutoff = clock()
    fixture_records = artifacts.get("fixtures", {}).get("records", [])
    history_records = artifacts.get("history", {}).get("records", [])
    model_results = {}
    enriched = {}
    if enabled_models:
        try:
            model_results = (model_predictor or predict_models)(
                fixture_records, history_records, model_cutoff,
                enabled_profiles=enabled_models, artifact_dir=model_artifact_dir,
            )
            if not isinstance(model_results, dict):
                raise TypeError("Invalid numerical model response")
        except Exception:  # noqa: BLE001 - Optional local models must not hide existing tickets.
            model_results = {
                profile: {
                    "profile_id": profile, "status": "MODEL_ERROR", "predictions": [],
                    "message": "A helyi modellfuttatás nem fejeződött be; nem készült becslés.",
                    "diagnostics": [{"code": "MODEL_RUN_FAILED"}],
                }
                for profile in enabled_models
            }
    artifacts["numeric_models"] = model_results
    diversity_options = {}
    club_enriched = {}
    contrarian_contexts = {}
    veto_contexts = {}
    contrarian_review_status = "NOT_REQUESTED"
    history_status = "NO_LEDGER"
    if "candidate_input" in artifacts and "history" in artifacts:
        enriched = enrich_daily223_input(artifacts["candidate_input"], artifacts["history"])
        from goal_analysis.engine.odds_movement import annotate_odds_movement
        from goal_analysis.engine.portfolio import specialist_veto_override_contexts
        from goal_analysis.tipsters.recommendations import (
            club_veto_candidate_context,
            contrarian_candidate_context,
        )

        historical_runs = []
        if ledger is not None and hasattr(ledger, "list_runs"):
            try:
                historical_runs = ledger.list_runs(date=target_date.isoformat(), limit=1000)
                history_status = "LOCAL_SNAPSHOTS_READ"
            except Exception:  # noqa: BLE001 - Missing local history is an honest strategy gap.
                history_status = "LOCAL_HISTORY_UNAVAILABLE"
        if ledger is not None and getattr(ledger, "path", None) is not None:
            from goal_analysis.jobs.odds_monitor import monitor_history

            monitor_runs = monitor_history(Path(ledger.path).resolve().parents[2], clock())
            historical_runs.extend(monitor_runs)
            if monitor_runs:
                history_status = "LOCAL_RUN_AND_MONITOR_SNAPSHOTS_READ"
        club_enriched = annotate_odds_movement(
            {**enriched, "fixtures": fixture_records}, historical_runs, clock(),
        )
        veto_contexts = specialist_veto_override_contexts(enriched, settings, clock())
        for identifier, context in club_veto_candidate_context(
            club_enriched, model_results, settings, clock(),
        ).items():
            previous = veto_contexts.get(identifier, {})
            veto_contexts[identifier] = {
                **previous, **context,
                "gates": sorted(set(previous.get("gates", [])) | set(context.get("gates", []))),
                "profile_ids": sorted(set(previous.get("profile_ids", []))
                                      | set(context.get("profile_ids", []))),
            }
        for candidate in club_enriched.get("candidates", []):
            if candidate["candidate_id"] in veto_contexts:
                candidate["veto_override_context"] = deepcopy(veto_contexts[candidate["candidate_id"]])
        contrarian_contexts = contrarian_candidate_context(
            club_enriched, model_results, settings, clock(),
        )
        preliminary_options = {}
        preliminary = build_portfolio(enriched, settings, diversity_options=preliminary_options,
                                      **construction_kwargs)
        _guard_final_publication(preliminary, clock())
        allocate(preliminary, preliminary_options, settings, clock(), plan=team_plan)
        packets = _review_packets(enriched, preliminary, settings["openai"]["max_candidates"]) if any(
            p not in (*MODEL_PROFILE_IDS, *TIPSTER_PROFILE_IDS) for p in settings["enabled_profiles"]
        ) else []
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
        if contrarian_contexts or veto_contexts:
            from goal_analysis.engine.specialist_contracts import SPECIALIST_CONTRACTS

            daily_ids = {leg["candidate_id"] for item in preliminary.get("profile_proposals", [])
                         if item["profile_id"] == "daily223" for leg in item.get("legs", [])}
            fixed_specialist_ids = {leg["candidate_id"] for item in preliminary.get("profile_proposals", [])
                                    if item["profile_id"] in SPECIALIST_CONTRACTS
                                    for leg in item.get("legs", [])}
            packets = _contrarian_review_packets(
                packets, club_enriched, contrarian_contexts, daily_ids | fixed_specialist_ids,
                settings["openai"]["max_candidates"], veto_contexts=veto_contexts,
            )
        review_cutoff = clock()
        packets = [item for item in packets if allows_new_leg(item, review_cutoff)]
        if weather_provider is not None and packets:
            from goal_analysis.providers.open_meteo import unknown, weather_context

            fixtures = {item["fixture_id"]: item for item in artifacts.get("fixtures", {}).get("records", [])}
            for fixture_id in dict.fromkeys(item["fixture_id"] for item in packets):
                fixture = fixtures.get(fixture_id)
                if fixture is None:
                    continue
                try:
                    weather_by_fixture[fixture_id] = weather_provider.forecast(fixture)
                except Exception:  # noqa: BLE001 - Optional weather must never stop the analysis.
                    weather_by_fixture[fixture_id] = unknown(fixture, "Az Open-Meteo lekérés nem sikerült; az időjárás ismeretlen.")
            for packet in packets:
                packet["weather_forecast"] = weather_by_fixture.get(packet["fixture_id"])
        original_review_packets = deepcopy(packets)
        if packets and ledger is not None and getattr(ledger, 'path', None) is not None:
            try:
                from goal_analysis.jobs import candidate_context_review as context_job

                context_root = Path(ledger.path).resolve().parents[2]
                context_mode = context_job.policy(context_root)
                if context_mode in {'SHADOW', 'ACTIVE'}:
                    context_attempt = {'version': context_job.VERSION, 'mode': context_mode,
                                       'status': 'ATTEMPTED_BUDGET_MUST_NOT_RESTART'}
                    candidate_context = context_job.prepare(context_root, fixture_records, packets,
                                                            run_id=run_id, clock=clock)
                    augmented = context_job.attach(packets, candidate_context)
                    artifacts['candidate_context_inputs'] = candidate_context
                    artifacts['candidate_review_control_packets'] = deepcopy(packets)
                    artifacts['candidate_review_context_packets'] = deepcopy(augmented)
                    context_attempt.update(status='SOURCES_PREPARED',
                        collection_sha256=canonical_sha256(candidate_context['collection']),
                        as_of=candidate_context['as_of'])
                    if context_mode == 'ACTIVE':
                        packets = augmented
            except Exception:  # noqa: BLE001 - optional facts cannot discard the stable construction
                context_mode = 'UNAVAILABLE'
                if context_attempt is not None:
                    context_attempt['status'] = 'UNAVAILABLE_NO_AUTOMATIC_SECOND_COLLECTION'
        artifacts['candidate_review_actual_packets'] = deepcopy(packets)
        if packets and reviewer is not None:
            review = reviewer.review(
                packets,
                target_date.isoformat(),
                feedback=_compact_feedback(ledger),
            )
            if context_attempt is not None:
                artifacts['candidate_review_response_batches'] = [{
                    'packet_sha256': canonical_sha256(packets), 'response': deepcopy(review)}]
        contrarian_review_status = _attach_contrarian_reviews(
            club_enriched, packets, review, clock(),
        )
        _attach_veto_reviews(club_enriched, packets, review, clock())
        if weather_by_fixture:
            review["fixture_context"] = [
                *[item for item in review.get("fixture_context", []) if item.get("topic") != "weather"],
                *(weather_context(item) for item in weather_by_fixture.values()),
            ]
        elif not packets:
            review["message"] = "Nincs elemzésre alkalmas jelölt; nem történt OpenAI-hívás."
        report = build_portfolio(enriched, settings, astra_review=review,
                                 diversity_options=diversity_options, **construction_kwargs)
        # Caution assessments can legitimately move the protected DAILY_223
        # solver to another eligible product after the initial review batch was
        # chosen. Close that candidate-specific review gap with bounded follow-up
        # rounds over the same immutable data snapshot.
        daily_follow_up_rounds = 0
        candidate_by_id = {item["candidate_id"]: item for item in enriched["candidates"]}
        history_by_id = {
            item["candidate_id"]: item
            for item in enriched.get("history_analysis", {}).get("candidates", [])
        }
        while reviewer is not None and review.get("status") == "COMPLETE":
            reviewed_ids = {item.get("candidate_id") for item in review.get("reviews", [])}
            daily = next(
                (item for item in report.get("tickets", []) if item["profile_id"] == "daily223"),
                None,
            )
            missing_ids = [
                leg["candidate_id"] for leg in (daily or {}).get("legs", [])
                if leg["candidate_id"] not in reviewed_ids
            ]
            if not missing_ids or daily_follow_up_rounds >= 3:
                break
            follow_up_packets = []
            for identifier in missing_ids:
                if identifier not in candidate_by_id:
                    continue
                packet = deepcopy(candidate_by_id[identifier])
                packet["historical_context"] = history_by_id.get(identifier, {})
                if packet["fixture_id"] in weather_by_fixture:
                    packet["weather_forecast"] = weather_by_fixture[packet["fixture_id"]]
                follow_up_packets.append(packet)
            if not follow_up_packets:
                break
            if context_mode == 'ACTIVE':
                follow_up_packets = context_job.attach(follow_up_packets, candidate_context)
            artifacts.setdefault('candidate_review_follow_up_packets', []).append(deepcopy(follow_up_packets))
            supplement = reviewer.review(
                follow_up_packets,
                target_date.isoformat(),
                feedback=_compact_feedback(ledger),
            )
            if context_attempt is not None:
                artifacts.setdefault('candidate_review_response_batches', []).append({
                    'packet_sha256': canonical_sha256(follow_up_packets),
                    'response': deepcopy(supplement)})
            if supplement.get("status") != "COMPLETE":
                break
            review = _merge_astra_reviews(review, supplement)
            daily_follow_up_rounds += 1
            report = build_portfolio(
                enriched,
                settings,
                astra_review=review,
                diversity_options=diversity_options,
                **construction_kwargs,
            )
        review["daily223_follow_up_rounds"] = daily_follow_up_rounds
        # Review latency can change event eligibility, never price eligibility.
        finished = clock()
        delivered = []
        for ticket in report.get("tickets", []):
            if not allows_new_ticket(ticket, finished):
                report.setdefault("diagnostics", []).append(
                    {
                        "code": "EVENT_WITHIN_PUBLICATION_LOCKOUT_DURING_REVIEW",
                        "profile_id": ticket["profile_id"],
                        "message": "A mérkőzés az elemzés alatt a kezdés előtti 15 perces zárolásba került; új szelvényként nem adható ki.",
                    }
                )
                continue
            for leg in ticket["legs"]:
                leg.update(
                    quote_metadata(leg.get("quote_timestamp_raw", leg.get("quoted_at")), finished)
                )
            delivered.append(ticket)
        report["tickets"] = delivered
        report["preview_tickets"] = build_previews(unpriced, live_settings, delivered, finished, review)
        report["recommendations"] = build_recommendations(
            enriched, unpriced, settings, finished, report, review
        )
        if weather_by_fixture:
            contexts = {item["fixture_id"]: item for item in review["fixture_context"] if item["topic"] == "weather"}
            for ticket in [*report["tickets"], *report["preview_tickets"], *report["recommendations"]]:
                for leg in ticket.get("legs", []):
                    weather = weather_by_fixture.get(leg["fixture_id"])
                    if weather:
                        leg["weather_forecast"] = weather
                        context = [item for item in leg.get("fixture_context", []) if item.get("topic") != "weather"]
                        leg["fixture_context"] = [*context, contexts[leg["fixture_id"]]]
        report["review_requested_candidates"] = len(packets)
        artifacts["unpriced_candidates"] = unpriced
        published = {t["profile_id"] for t in delivered}
        for profile in report.get("profiles", []):
            profile["published"] = profile["profile_id"] in published
        if len(delivered) < settings["target_ticket_count"] or (
            "daily223" in team_plan["live_profiles"] and "daily223" not in published
        ):
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
    report.update(run_id=run_id, timezone="Europe/Berlin", astra=review, settings=requested_settings)
    if context_attempt is not None:
        report['candidate_context_attempt'] = context_attempt
        if candidate_context:
            report['sports_supplement'] = {'the_stats_api': candidate_context['collection']}
            report['candidate_context_review'] = {
                'version': context_job.VERSION, 'mode': context_mode,
                'status': 'APPLIED_EXISTING_REVIEW_CONTRACT' if context_mode == 'ACTIVE' else 'CAPTURED_FOR_SEPARATE_SHADOW_COMPARISON',
                'as_of': candidate_context['as_of'],
                'original_packet_sha256': canonical_sha256(original_review_packets),
                'input_context_sha256': canonical_sha256(candidate_context['contexts']),
                'usage': context_job.usage(packets, review) if context_mode == 'ACTIVE' else [],
                'new_probability_weights': False,
                'predictive_improvement_claimed': False,
            }
    report.setdefault("preview_tickets", [])
    report.setdefault("recommendations", [])
    numeric_recommendations = build_model_recommendations(
        model_results, fixture_records,
        artifacts.get("candidate_input", {}).get("candidates", []),
        {**settings, "date": target_date.isoformat()}, clock(), review,
        diversity_options=diversity_options,
    )
    report["recommendations"].extend(numeric_recommendations)
    persona_recommendations = build_tipster_recommendations(
        {**club_enriched, "date": target_date.isoformat(), "fixtures": fixture_records,
         "candidates": club_enriched.get("candidates", artifacts.get("candidate_input", {}).get("candidates", []))},
        model_results, settings, clock(),
        diversity_options=diversity_options,
    )
    nemotron_style = {"status": "DISABLED", "usage": {"requests": 0}, "selected": {}}
    if nemotron_stylist is not None:
        try:
            from goal_analysis.jobs.nemotron_tickets import apply as apply_nemotron_style

            nemotron_style = nemotron_stylist(
                diversity_options, persona_recommendations, target_date.isoformat(), clock())
            persona_recommendations = apply_nemotron_style(
                nemotron_style, diversity_options, persona_recommendations)
        except Exception:  # noqa: BLE001 - optional stylist never blocks publication
            nemotron_style = {"status": "UNAVAILABLE", "usage": {"requests": 0}, "selected": {}}
    report["recommendations"].extend(persona_recommendations)
    report["tipster_club"] = {
        "version": "tipster-club-v1", "max_daily": len(TIPSTER_PROFILE_IDS),
        "selection_basis": "SHARED_DATA_AND_MODELS_WITH_PERSONA_PREFERENCES",
        "independent_models": False,
        "additional_llm_calls": nemotron_style.get("usage", {}).get("requests", 0),
        "nemotron_style": {key: value for key, value in nemotron_style.items() if key != "selected"},
        "profile_count": len(persona_recommendations),
        "published_count": sum(bool(item.get("legs")) for item in persona_recommendations),
        "contrarian_history_status": history_status,
        "contrarian_review_status": contrarian_review_status,
        "contrarian_eligible_source_candidates": len(contrarian_contexts),
    }
    from goal_analysis.engine.odds_movement import quote_snapshot

    report["odds_history_snapshot"] = quote_snapshot(
        artifacts.get("candidate_input", {}).get("candidates", []), clock(),
    )
    for recommendation in persona_recommendations:
        report.setdefault("profiles", []).append({
            "profile_id": recommendation["profile_id"], "profile_name": recommendation["profile_name"],
            "construction_status": recommendation["status"], "published": bool(recommendation["legs"]),
            "experimental": True, "reason": " ".join(recommendation.get("quality_warnings", [])),
        })
    report["numeric_models"] = model_results
    report["model_cutoff"] = model_cutoff.isoformat()
    for recommendation in numeric_recommendations:
        report.setdefault("profiles", []).append({
            "profile_id": recommendation["profile_id"],
            "profile_name": recommendation["profile_name"],
            "candidate_count": len(
                model_results.get(recommendation["profile_id"], {}).get("predictions", [])
            ),
            "construction_status": recommendation["status"],
            "published": bool(recommendation["legs"]),
            "experimental": True,
            "reason": " ".join(recommendation.get("quality_warnings", [])),
        })
    apply_publication_policy(report, team_plan)
    report["recommendation_overlap"] = recommendation_overlap(report["recommendations"])
    if report["preview_tickets"] and not report["tickets"]:
        report["construction_status"] = "ODDS_PENDING"
    priced_fixtures = {
        c["fixture_id"] for c in artifacts.get("candidate_input", {}).get("candidates", [])
    }
    report["fixtures"] = [
        {
            **fixture,
            "odds_status": "PRICED" if fixture["fixture_id"] in priced_fixtures else "ODDS_PENDING",
            "weather": weather_by_fixture.get(fixture["fixture_id"]),
        }
        for fixture in artifacts.get("fixtures", {}).get("records", [])
    ]
    report["calendar"] = artifacts.get("calendar", {})
    report["selected_competition_ids"] = [
        league["api_football_id"] for league in artifacts.get("config", {}).get("leagues", [])
    ]
    configured_ids = {league["api_football_id"] for league in config["leagues"]}
    report["calendar"]["scope_policy"] = {
        "version": "arthur-league-scope-v1",
        "mode": "AUTOMATIC" if selected_competition_ids is None else "MANUAL",
        "serie_c_fallback_below": MINIMUM_PENDING_FIXTURES,
        "automatically_added_ids": (
            sorted(set(report["selected_competition_ids"]) - configured_ids)
            if selected_competition_ids is None else []
        ),
    }
    report["odds_queries"] = [
        {
            "provider": getattr(odds_feed, 'provider', 'the_odds_api'),
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
    published_at = clock()
    report["finished_at"] = published_at.isoformat()
    _guard_final_publication(report, published_at)
    _base_diversity_recommendations(diversity_options, enriched, settings, published_at,
                                   review, weather_by_fixture)
    from goal_analysis.engine.publication_odds import apply_publication_odds

    report, diversity_options = apply_publication_odds(report, diversity_options, settings)
    report['usage'] = deepcopy(legacy.get('usage', {}))
    if expert_reviewer is not None:
        original, original_options = deepcopy(report), deepcopy(diversity_options)
        try:
            reviewed = expert_reviewer(deepcopy(report), deepcopy(diversity_options))
            checked, checked_options = reviewed['report'], reviewed['options']
            for group in ('tickets', 'recommendations', 'profile_proposals', 'preview_tickets', 'profiles'):
                if ([r for r in checked.get(group, []) if r.get('profile_id') == 'daily223']
                        != [r for r in original.get(group, []) if r.get('profile_id') == 'daily223']):
                    raise ValueError('Protected DAILY changed by expert')
            if checked_options.get('daily223') != original_options.get('daily223'):
                raise ValueError('Protected DAILY options changed by expert')
            report, diversity_options = checked, checked_options
        except Exception:  # noqa: BLE001 - optional evidence cannot destroy valid construction
            report, diversity_options = original, original_options
            report['expert_advisory'] = {'status': 'UNAVAILABLE', 'daily223_protected': True}
    # Alias construction may take time. Freeze a fresh, explicit publication
    # cutoff for the final joint lockout/concentration decision and its report.
    published_at = clock()
    report["finished_at"] = published_at.isoformat()
    _guard_final_publication(report, published_at)
    allocate(report, diversity_options, settings, published_at, plan=team_plan)
    # Newly chosen leaf templates need the same team disclosure as their original
    # independently selected cards. This never promotes a paper profile.
    apply_publication_policy(report, team_plan)
    _guard_final_publication(report, published_at)
    if package_reviewer is not None:
        # One package-level second opinion before persistence. The callback may
        # replace only independently disputed non-DAILY cards from this run's
        # already validated options. It never collects another sports data set.
        original = deepcopy(report)
        try:
            checked = package_reviewer(deepcopy(report), diversity_options)
            for group in ("tickets", "recommendations", "profile_proposals", "preview_tickets", "profiles"):
                if ([r for r in checked.get(group, []) if r.get("profile_id") == "daily223"]
                        != [r for r in original.get(group, []) if r.get("profile_id") == "daily223"]):
                    raise ValueError("Protected DAILY changed")
            report = checked
        except Exception:  # noqa: BLE001 - optional second opinion preserves construction
            report = original
            report["grok_review"] = {"status": "UNAVAILABLE", "repair_status": "UNAVAILABLE_NO_CHANGE"}
        published_at = clock()
        report["finished_at"] = published_at.isoformat()
        _guard_final_publication(report, published_at)
    if report["preview_tickets"] and not report["tickets"]:
        report["construction_status"] = "ODDS_PENDING"
    fixture_use = {}
    for ticket in report["tickets"]:
        for leg in ticket["legs"]:
            fixture_use.setdefault(leg["fixture_id"], []).append(ticket["ticket_id"])
    report["overlap"] = [{"fixture_id": fixture, "ticket_ids": tickets, "ticket_count": len(tickets)}
                         for fixture, tickets in sorted(fixture_use.items()) if len(tickets) > 1]
    report["recommendation_overlap"] = recommendation_overlap(report["recommendations"])
    report["tipster_club"]["published_count"] = sum(
        bool(r.get("legs")) for r in report["recommendations"] if r["profile_id"] in TIPSTER_PROFILE_IDS
    )
    report["data_issues"] = legacy.get("data_issues", [])
    report["market_coverage"] = legacy.get("market_coverage", [])
    report["requested_market_coverage"] = legacy.get("requested_market_coverage", [])
    report["usage"] = {
        **legacy.get("usage", {}),
        "openai": review.get("usage", {}),
        "llm_calls": review.get("usage", {}).get("requests", 0),
    }
    standings_calls = report.get('expert_advisory', {}).get('standings_collection', {}).get('requests', 0)
    report['usage']['standings_calls'] = standings_calls
    report['usage']['football_calls'] = report['usage'].get('football_calls', 0) + standings_calls
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
    from goal_analysis.jobs.source_resolution import build_source_resolution

    try:
        source_resolution = build_source_resolution(report, artifacts, as_of=published_at)
        artifacts["source_resolution"] = source_resolution
        report["source_resolution"] = source_resolution["summary"]
    except Exception:  # noqa: BLE001 - SHADOW diagnostics cannot destroy stable tickets.
        report["source_resolution"] = {"mode": "SHADOW", "status": "UNAVAILABLE",
                                       "reason": "SOURCE_RESOLUTION_AUDIT_FAILED"}
    from goal_analysis.decision_trace import build_decision_data_usage

    report["decision_data_usage"] = build_decision_data_usage(
        report, artifacts, candidate_review=review,
        feedback_used=_compact_feedback(ledger) is not None,
        odds_movement_candidates=club_enriched.get("candidates", []),
    )
    from goal_analysis.portfolio_objectives import assess_objectives

    report["portfolio_objectives"] = assess_objectives(report)
    if report["portfolio_objectives"]["capacity_shortfall"]:
        report.setdefault("diagnostics", []).append({
            "code": "CAPACITY_SHORTFALL",
            "message": "Kevesebb mint 20 bizonyított READY ajánlás készült; "
                       "a rendszer nem adott gyenge töltelékszelvényt.",
            "missing_recommendations": report["portfolio_objectives"]["capacity_shortfall"],
        })
    from goal_analysis.strategy_provenance import attach_strategy_provenance

    attach_strategy_provenance(report, requested_settings)
    from goal_analysis.jobs.portfolio_distribution import build_portfolio_distribution

    try:
        report["portfolio_distribution"] = build_portfolio_distribution(report, artifacts)
    except Exception:  # noqa: BLE001 - A SHADOW scenario cannot veto stable tickets.
        report["portfolio_distribution"] = {"mode": "SHADOW", "status": "UNAVAILABLE",
            "reason": "PORTFOLIO_DISTRIBUTION_AUDIT_FAILED", "decision_effect": "NONE"}
    from goal_analysis.jobs.ticket_evidence import build_ticket_evidence

    try:
        report["ticket_evidence"] = build_ticket_evidence(report, artifacts)
    except Exception:  # noqa: BLE001 - An audit cannot veto stable selections.
        report["ticket_evidence"] = {"mode": "AUDIT_ONLY", "status": "UNAVAILABLE",
                                     "reason": "TICKET_EVIDENCE_AUDIT_FAILED", "decision_effect": "NONE"}
    if history_sample_shadow:
        from goal_analysis.models.history_sample_shadow import evaluate

        try:
            # Separate, opt-in diagnostics AFTER final selection. Neither control
            # nor treatment may replace numeric_models or recommendation inputs.
            report['history_sample_shadow'] = evaluate(
                fixture_records, history_records, model_cutoff, enabled=True,
                enabled_profiles=enabled_models,
                artifact_dir=(Path(model_artifact_dir) / 'history-sample-shadow'
                              if model_artifact_dir is not None else None),
            )
        except Exception:  # noqa: BLE001 - An experiment cannot veto stable selections.
            report['history_sample_shadow'] = {'mode': 'SHADOW', 'status': 'UNAVAILABLE',
                'reason': 'HISTORY_SAMPLE_SHADOW_FAILED', 'decision_effect': 'NONE'}
    if native_history_shadow_root is not None:
        from goal_analysis.jobs.native_history_shadow import replay_native_history

        try:
            kwargs = {'as_of': clock(), 'profiles': enabled_models,
                'artifact_dir': (Path(model_artifact_dir) / 'native-history-shadow'
                                 if model_artifact_dir is not None else None)}
            if native_option_shadow:
                from goal_analysis.jobs.native_options_shadow import replay_native_options

                report['native_history_shadow'] = replay_native_options(
                    native_history_shadow_root, report,
                    {**enriched, 'fixtures': fixture_records, 'date': target_date.isoformat()}, settings, **kwargs)
            else:
                report['native_history_shadow'] = replay_native_history(
                    native_history_shadow_root, fixture_records, **kwargs)
        except Exception:  # noqa: BLE001 - Optional SHADOW cannot veto stable publication.
            report['native_history_shadow'] = {'mode': 'SHADOW', 'status': 'UNAVAILABLE',
                'reason': 'NATIVE_HISTORY_SHADOW_FAILED', 'decision_effect': 'NONE'}
    if player_specialist_shadow_root is not None:
        from goal_analysis.jobs.player_option_shadow import review_player_options

        try:
            report['player_specialist_shadow'] = review_player_options(
                player_specialist_shadow_root, report, diversity_options,
                {**enriched, 'fixtures': fixture_records, 'date': target_date.isoformat()},
                model_results, as_of=clock(),
            )
        except Exception:  # noqa: BLE001 - An optional SHADOW cannot veto publication.
            report['player_specialist_shadow'] = {'mode': 'SHADOW', 'status': 'UNAVAILABLE',
                'reason': 'PLAYER_SPECIALIST_SHADOW_FAILED', 'live_decision_effect': False}
    report.pop("report_sha256", None)
    report["report_sha256"] = canonical_sha256(report)
    artifacts["daily223_report"] = legacy
    artifacts["report"] = report
    return {"schema_version": 3, "artifacts": artifacts}


def _contrarian_review_packets(packets, enriched, contexts, daily_ids, maximum, *, veto_contexts=None):
    """Keep DAILY coverage first and use the same existing bounded review call."""
    candidates = {item["candidate_id"]: item for item in enriched.get("candidates", [])}
    veto_contexts = veto_contexts or {}
    protected = [item for item in packets if item["candidate_id"] in daily_ids]
    # At most six specialist alternatives use existing slots; unrelated base
    # coverage remains available, and the provider/model call budget is unchanged.
    contrarian_order = sorted(contexts, key=lambda identifier: (
        -contexts[identifier].get("expected_return", 0), identifier,
    ))
    override_order = sorted(veto_contexts)
    ordered = list(dict.fromkeys(identifier for pair in zip_longest(
        contrarian_order, override_order) for identifier in pair if identifier is not None))
    ordered = ordered[:max(0, min(6, maximum - len(protected)))]
    result, seen = [], set()
    for item in [*protected, *(candidates[key] for key in ordered if key in candidates), *packets]:
        identifier = item["candidate_id"]
        if identifier in seen or len(result) >= maximum:
            continue
        packet = deepcopy(item)
        if identifier in contexts:
            packet["contrarian_context"] = deepcopy(contexts[identifier])
            packet["evidence_ids"] = list(dict.fromkeys(
                [*packet.get("evidence_ids", []), contexts[identifier]["evidence_id"]],
            ))
        if identifier in veto_contexts:
            packet["veto_override_context"] = deepcopy(veto_contexts[identifier])
        result.append(packet)
        seen.add(identifier)
    return result


def _attach_contrarian_reviews(enriched, packets, review, now):
    """Accept structured explanations only from the actual validated batch."""
    from goal_analysis.agents.astra_review import _ReviewError, _validate_reviews

    if not any(isinstance(item.get("contrarian_context"), dict) for item in packets):
        return "NOT_REQUESTED"
    if review.get("status") != "COMPLETE":
        return "ASTRA_UNAVAILABLE"
    try:
        records = _validate_reviews({"reviews": review.get("reviews")}, packets, review.get("sources", []))
    except (_ReviewError, KeyError, TypeError, ValueError):
        return "STRUCTURED_EXPLANATION_MISSING_OR_INVALID"
    by_id = {item["candidate_id"]: item for item in enriched.get("candidates", [])}
    attached = 0
    for item in records:
        rationale = item.get("contrarian_rationale")
        if rationale is None or item["candidate_id"] not in by_id:
            continue
        by_id[item["candidate_id"]]["contrarian_review"] = {
            "status": "COMPLETE", "candidate_id": item["candidate_id"],
            "reviewed_at": now.isoformat(), **deepcopy(rationale),
        }
        attached += 1
    return "COMPLETE" if attached else "STRUCTURED_EXPLANATION_MISSING_OR_INVALID"


def _attach_veto_reviews(enriched, packets, review, now):
    """Bind optional override evidence to this quote and the actual review batch."""
    from goal_analysis.agents.astra_review import _ReviewError, _validate_reviews
    from goal_analysis.engine.veto_overrides import veto_candidate_signature

    contexts = {item["candidate_id"]: item["veto_override_context"] for item in packets
                if isinstance(item.get("veto_override_context"), dict)}
    if not contexts or review.get("status") != "COMPLETE":
        return
    try:
        records = _validate_reviews({"reviews": review.get("reviews")}, packets, review.get("sources", []))
    except (_ReviewError, KeyError, TypeError, ValueError):
        return
    by_id = {item["candidate_id"]: item for item in enriched.get("candidates", [])}
    for item in records:
        identifier = item["candidate_id"]
        if item.get("veto_override") is None or identifier not in by_id:
            continue
        candidate = by_id[identifier]
        candidate["veto_override_context"] = deepcopy(contexts[identifier])
        candidate["astra_veto_review"] = {
            "status": "COMPLETE", "reviewed_at": now.isoformat(),
            "candidate_snapshot_sha256": veto_candidate_signature(candidate),
            "sources": deepcopy(review.get("sources", [])), "reviews": [deepcopy(item)],
        }


def _base_diversity_recommendations(options, enriched, settings, now, review, weather):
    """Materialize accurate base-card aliases from the unchanged candidate snapshot."""
    from goal_analysis.engine.recommendations import build_recommendations

    candidates = {item["candidate_id"]: item for item in enriched.get("candidates", [])}
    for profile, choices in options.items():
        materialized = []
        for option in choices:
            if "ticket" not in option:
                materialized.append(option)
                continue
            ticket = option["ticket"]
            if not allows_new_ticket(ticket, now):
                continue
            identifiers = [leg["candidate_id"] for leg in ticket["legs"]]
            if any(identifier not in candidates for identifier in identifiers):
                continue
            subset = {**enriched, "candidates": [candidates[leg["candidate_id"]] for leg in ticket["legs"]]}
            cards = build_recommendations(subset, [], {**settings, "enabled_profiles": [profile]},
                                          now, {"profile_proposals": [ticket], "tickets": []}, review)
            # A partially expired combination must not acquire a shorter alias
            # from the research fallback: the original ticket is indivisible.
            if not cards or sorted(leg["candidate_id"] for leg in cards[0]["legs"]) != sorted(identifiers):
                continue
            option["recommendation"] = cards[0]
            for item in (ticket, option["recommendation"]):
                for leg in item["legs"]:
                    leg.update(quote_metadata(leg.get("quote_timestamp_raw", leg.get("quoted_at")), now))
                    if leg["fixture_id"] in weather:
                        leg["weather_forecast"] = weather[leg["fixture_id"]]
            materialized.append(option)
        options[profile] = materialized


def _guard_final_publication(report: dict, now: datetime) -> None:
    """No new publication inside the last 15 minutes, including the DAILY wrapper."""
    def blocked(item):
        return bool(item.get("legs")) and not allows_new_ticket(item, now)

    removed = set()
    for group in ("tickets", "preview_tickets", "profile_proposals"):
        items = report.get(group, [])
        removed.update(item["profile_id"] for item in items if blocked(item))
        report[group] = [item for item in items if not blocked(item)]
    for item in report.get("recommendations", []):
        if not blocked(item):
            continue
        removed.add(item["profile_id"])
        item.update(legs=[], combined_price=None, bookmaker=None, playable=False,
                    selected_for_portfolio=False, portfolio_ticket_id=None, stake_eur=None,
                    status="ABSTAINED" if item["profile_id"] in TIPSTER_PROFILE_IDS else "NO_FIXTURES",
                    reason_code="EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT",
                    abstention_reason="A kezdésig legfeljebb 15 perc van; ebből a futásból új ajánlat nem adható ki.")
        item.pop("recommendation_id", None)
        item.setdefault("quality_warnings", []).append(item["abstention_reason"])
    if removed:
        report["construction_status"] = "PARTIAL" if report.get("tickets") else "CONSTRUCTION_INCOMPLETE"
        report.setdefault("diagnostics", []).append({
            "code": "EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT", "profiles": sorted(removed),
            "message": "A végső kiadás a kezdés előtti 15 perces zárolásban lévő új ajánlatokat kihagyta.",
        })
        for profile in report.get("profiles", []):
            if profile["profile_id"] in removed:
                profile.update(published=False, construction_status="EVENT_WITHIN_15_MINUTE_PUBLICATION_LOCKOUT")


def recommendation_overlap(recommendations: list[dict]) -> list[dict]:
    """Describe shared exposure without claiming independent or calibrated tickets."""
    from itertools import combinations

    overlaps = []
    for first, second in combinations(recommendations, 2):
        shared = sorted(
            {str(leg["fixture_id"]) for leg in first.get("legs", [])}
            & {str(leg["fixture_id"]) for leg in second.get("legs", [])}
        )
        if shared:
            overlaps.append({
                "profile_a": first["profile_id"], "profile_b": second["profile_id"],
                "fixture_ids": shared, "shared_fixtures": len(shared),
                "interpretation": "Közös mérkőzéskitettség; nem független szelvények.",
            })
    return overlaps


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
    for recommendation in report.get("recommendations", []):
        if recommendation.get("profile_id") in TIPSTER_PROFILE_IDS:
            price = recommendation.get("combined_price")
            lines.extend([
                f"## {recommendation['profile_name']} · Tipster klub", "",
                f"Állapot: {recommendation['status']} · {recommendation.get('publication_scope', 'UNKNOWN')}",
                f"Összszorzó: {price:.2f}×" if price is not None else "Nincs kiadható szorzós szelvény.", "",
            ])
            for leg in recommendation.get("legs", []):
                leg_price = leg.get("decimal_price")
                leg_price_label = f"{leg_price:.2f}×" if leg_price is not None else "szorzó nélkül"
                lines.append(f"- {leg['home_team']} – {leg['away_team']}: "
                             f"{leg['market_key']} / {leg['selection_key']} · {leg_price_label}")
            lines.extend(recommendation.get("quality_warnings", []))
            lines.append("")
            continue
        if recommendation.get("method_kind") != "LOCAL_STATISTICAL_MODEL":
            continue
        lines.extend([
            f"## {recommendation['profile_name']} · kísérleti modell / papírteszt", "",
            f"Állapot: {recommendation['status']}", "",
        ])
        for leg in recommendation.get("legs", []):
            probability = leg.get("model_probability")
            estimate = f"{probability:.1%}" if probability is not None else "nincs becslés"
            lines.append(
                f"- {leg['home_team']} – {leg['away_team']}: "
                f"{leg['market_key']} / {leg['selection_key']} · modellbecslés: {estimate}"
            )
        lines.extend(recommendation.get("quality_warnings", []))
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
