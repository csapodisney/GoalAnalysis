"""Arthur portfolio CLI; used by both the dashboard and Windows scheduler."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from goal_analysis.agents import canonical_sha256
from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.config.daily_223_live import validate_live_config
from goal_analysis.config.portfolio import load_settings, validate_settings
from goal_analysis.jobs.expert_advisory import refresh_news, review_experts
from goal_analysis.jobs.grok_gate import review_and_repair as review_grok_package
from goal_analysis.jobs.portfolio import (
    RunLock,
    run_portfolio,
    safe_error,
    validate_target_day,
    write_portfolio_bundle,
)
from goal_analysis.jobs.sports_context_archive import make_weather_provider
from goal_analysis.providers.the_stats_odds import TheStatsOddsFeed
from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
from goal_analysis.storage import CacheStore, Database
from goal_analysis.storage.portfolio_ledger import PortfolioLedger


def parser():
    value = argparse.ArgumentParser(description="Arthur — önálló tipsterek és Astra-elemzés")
    value.add_argument("--config", type=Path, default=ROOT / "config/daily223-live.json")
    value.add_argument("--settings", type=Path, default=ROOT / "config/arthur-settings.json")
    value.add_argument("--date", type=date.fromisoformat)
    value.add_argument("--live", action="store_true")
    value.add_argument("--settle", action="store_true")
    value.add_argument("--check-config", action="store_true")
    value.add_argument("--check-openai", action="store_true")
    value.add_argument("--check-codex-login", action="store_true")
    value.add_argument("--strictness", type=int)
    value.add_argument("--ticket-count", type=int)
    value.add_argument("--competition-ids", type=str)
    return value


def report_progress(stage, message):
    path = ROOT / "data/arthur/progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {"stage": stage, "message": message, "at": datetime.now(UTC).isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temp.replace(path)
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(message.encode(encoding, errors="replace").decode(encoding), flush=True)


def make_reviewer(settings: dict):
    ai = settings["openai"]
    return CodexReviewer(
        progress=report_progress,
        model=ai["model"],
        reasoning_effort=ai["reasoning_effort"],
        web_search=ai["web_search"],
        max_output_tokens=ai["max_output_tokens"],
        cache_dir=ROOT / "data/arthur/codex-cache",
    )


def review_package(report, options):
    from goal_analysis.jobs.grok_advisory import enabled

    if enabled(ROOT):
        report_progress("grok_package_review", "Grok: a teljes szelvénycsomag közös ellenőrzése…")
    return refresh_news(ROOT, review_grok_package(ROOT, report, options))


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.chdir(ROOT)
    selected_competitions = None
    if args.competition_ids is not None:
        parts = args.competition_ids.split(",")
        if not parts or len(parts) > 40 or any(not part.isdecimal() for part in parts):
            parser().error("--competition-ids: 1–40 comma-separated numeric IDs required")
        selected_competitions = [int(part) for part in parts]
        if len(set(selected_competitions)) != len(selected_competitions):
            parser().error("--competition-ids: duplicate IDs are not allowed")
    if not any(
        (args.live, args.settle, args.check_config, args.check_openai, args.check_codex_login)
    ):
        parser().print_help()
        return 0
    try:
        settings = load_settings(args.settings)
        if args.strictness is not None:
            settings["strictness"] = args.strictness
        if args.ticket_count is not None:
            settings["target_ticket_count"] = args.ticket_count
        settings = validate_settings(settings)
        if args.check_codex_login:
            result = make_reviewer(settings).login_status()
            print(json.dumps(result))
            return 0 if result["login_available"] else 2
        if args.check_openai:
            reviewer = make_reviewer(settings)
            reviewer.progress = None
            result = reviewer.check_connection()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("verified") else 2
        config = validate_live_config(json.loads(args.config.read_text("utf-8-sig")))
        if args.check_config:
            missing = [
                name for name in ("THE_STATS_API_KEY",) if not os.environ.get(name, "").strip()
            ]
            print(
                f"Config valid: {len(config['leagues'])} competitions; backend=codex_chatgpt; model={settings['openai']['model']}; tickets={settings['target_ticket_count']}; maximum_generated={settings['max_ticket_count']}; maximum_wagers=5; stake=EUR1"
            )
            print("Missing environment variables: " + (", ".join(missing) or "none"))
            print(
                "TheStats supplies sports data and odds; retired providers make no live requests."
            )
            print(
                "OpenAI API keys are not used. No network request was made. Use --check-openai to verify ChatGPT/Codex access."
            )
            return 2 if missing else 0
        day = args.date or datetime.now(ZoneInfo("Europe/Berlin")).date()
        if args.live:
            validate_target_day(day, datetime.now(UTC))
        with RunLock(ROOT / "data/arthur/run.lock") as normal_weather_lock:
            # Read one coherent configuration after any completed restore.
            settings = load_settings(args.settings)
            if args.strictness is not None:
                settings["strictness"] = args.strictness
            if args.ticket_count is not None:
                settings["target_ticket_count"] = args.ticket_count
            settings = validate_settings(settings)
            config = validate_live_config(json.loads(args.config.read_text("utf-8-sig")))
            ledger = PortfolioLedger(ROOT / "data/arthur/ledger.sqlite3")
            db = Database(ROOT / "data/goal_analysis.sqlite3")
            db.initialize()
            football = TheStatsPrimaryClient(ROOT, max_requests=config['max_football_calls'] + 20)
            settlement_failed = False
            settlement_diagnostic = None
            if args.settle:
                try:
                    settlement = ledger.refresh_results(football)
                    print("Settlement: " + json.dumps(settlement, ensure_ascii=False))
                except Exception as error:  # noqa: BLE001 - Continue collection after a bounded settlement failure.
                    settlement_failed = True
                    settlement_diagnostic = {
                        "code": "SETTLEMENT_FAILED",
                        "message": safe_error(error),
                    }
                    print("Settlement failed: " + settlement_diagnostic["message"])
            if not args.live:
                return 2 if settlement_failed else 0
            try:
                from goal_analysis.jobs.nemotron_tickets import curate as curate_nemotron_tickets
                from goal_analysis.team.manager import TeamManager
                from goal_analysis.team.routing import generation_plan

                team_plan = generation_plan(settings["enabled_profiles"], TeamManager(ROOT).enroll_missing()["snapshot"])
                odds = TheStatsOddsFeed(ROOT, config['odds_region'], config['max_odds_credits'])
                report_progress(
                    "collecting", "Mérkőzések, szolgáltatói szorzók és háttéradatok gyűjtése…"
                )
                selection_kwargs = (
                    {"selected_competition_ids": selected_competitions}
                    if selected_competitions is not None else {}
                )
                weather = make_weather_provider(ROOT, f'{day}-{datetime.now(UTC).isoformat()}',
                    root_run_lock=normal_weather_lock)
                cache = CacheStore(db)
                bundle = run_portfolio(
                    config,
                    settings,
                    football,
                    odds,
                    cache,
                    day,
                    make_reviewer(settings),
                    ledger,
                    weather_provider=weather,
                    expert_reviewer=lambda report, options: review_experts(
                        ROOT, report, options, weather_provider=weather,
                        standings_client=football, standings_cache=cache,
                        max_standings_requests=min(8, max(0, config['max_football_calls']
                            - report.get('usage', {}).get('football_calls', 0)))),
                    model_artifact_dir=ROOT / "data/arthur/models",
                    normal_history_root=ROOT,
                    team_plan=team_plan,
                    nemotron_stylist=lambda options, cards, target, now: curate_nemotron_tickets(
                        ROOT, options, cards, target, now),
                    package_reviewer=review_package,
                    **selection_kwargs,
                )
            except Exception as error:  # noqa: BLE001 - Persist a visible, redacted failed run.
                now = datetime.now(UTC).isoformat()
                report = {
                    "schema_version": 3,
                    "module": "ARTHUR_PORTFOLIO",
                    "run_id": f"{day}-{uuid4().hex[:12]}",
                    "date": day.isoformat(),
                    "observed_at": now,
                    "finished_at": now,
                    "construction_status": "DATA_BLOCKED",
                    "tickets": [],
                    "profiles": [],
                    "settings": settings,
                    "reason": safe_error(error),
                    "diagnostics": [{"code": "RUN_FAILED", "message": safe_error(error)}],
                    "astra": {
                        "status": "UNAVAILABLE",
                        "model": settings["openai"]["model"],
                        "message": "Az előkészítés nem fejeződött be.",
                    },
                    "usage": {},
                    "real_wager_placed": False,
                }
                bundle = {"schema_version": 3, "artifacts": {"report": report}}
            report = bundle["artifacts"]["report"]
            if settlement_diagnostic:
                report.setdefault("diagnostics", []).append(settlement_diagnostic)
                report["settlement_status"] = "FAILED"
            report.pop("report_sha256", None)
            report["report_sha256"] = canonical_sha256(report)
            output = ROOT / "reports/arthur" / report["run_id"]
            write_portfolio_bundle(output, bundle)
            ledger.save_run(report)
            # The entire package was reviewed once before persistence. Do not
            # send its repaired version back for an unbounded model dialogue.
            grok_advice = report.get("grok_review", {"status": "DISABLED"})
            if grok_advice.get("status") != "DISABLED":
                print("Grok: " + grok_advice.get("status", "UNAVAILABLE"))
            from goal_analysis.jobs.nemotron import analyze, enabled

            if enabled(ROOT):
                report_progress("codex_advisory", "Codex: a mentett ajánlatok párhuzamos kockázati ellenőrzése…")
                advisory = analyze(ROOT, report)
                print("Codex: " + advisory.get("status", "UNAVAILABLE"))
            report_progress(
                "saved", "Az ajánlatok és az Astra eredménye elmentve a helyi adatbázisba."
            )
            print("Astra: " + report["astra"].get("message", report["astra"]["status"]))
            print(
                f"ARTHUR: {report['construction_status']}; tickets={len(report['tickets'])}; previews={len(report.get('preview_tickets', []))}; Astra={report['astra']['status']}; output={output.relative_to(ROOT)}"
            )
            if report.get("reason"):
                print(report["reason"])
            print("No wager was placed. Actual wagers must be marked in the dashboard.")
            delivered = (
                len(report["tickets"])
                + len(report.get("preview_tickets", []))
                + sum(bool(r["legs"]) for r in report.get("recommendations", []))
            )
            return 0 if delivered and not settlement_failed else 2
    except Exception as error:  # noqa: BLE001 - CLI boundary returns a clean error and nonzero status.
        print("Arthur: " + safe_error(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
