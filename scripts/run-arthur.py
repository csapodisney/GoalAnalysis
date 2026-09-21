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
from goal_analysis.jobs.portfolio import (
    RunLock,
    run_portfolio,
    safe_error,
    validate_target_day,
    write_portfolio_bundle,
)
from goal_analysis.providers.api_football import ApiFootballClient
from goal_analysis.providers.daily_223_odds import Daily223OddsFeed, UnavailableOddsFeed
from goal_analysis.storage import CacheStore, Database, SnapshotStore
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
    print(message, flush=True)


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


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.chdir(ROOT)
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
            result = make_reviewer(settings).check_connection()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("verified") else 2
        config = validate_live_config(json.loads(args.config.read_text("utf-8-sig")))
        if args.check_config:
            missing = [
                name
                for name in ("API_FOOTBALL_KEY", "THE_ODDS_API_KEY")
                if not os.environ.get(name, "").strip()
            ]
            print(
                f"Config valid: {len(config['leagues'])} competitions; backend=codex_chatgpt; model={settings['openai']['model']}; tickets={settings['target_ticket_count']}; maximum=5; stake=EUR5"
            )
            print("Missing environment variables: " + (", ".join(missing) or "none"))
            print(
                "OpenAI API keys are not used. No network request was made. Use --check-openai to verify ChatGPT/Codex access."
            )
            return 2 if missing else 0
        day = args.date or datetime.now(ZoneInfo("Europe/Berlin")).date()
        if args.live:
            validate_target_day(day, datetime.now(UTC))
        with RunLock(ROOT / "data/arthur/run.lock"):
            ledger = PortfolioLedger(ROOT / "data/arthur/ledger.sqlite3")
            db = Database(ROOT / "data/goal_analysis.sqlite3")
            db.initialize()
            snapshots = SnapshotStore(db, ROOT / "data/raw")
            football = ApiFootballClient(snapshot_store=snapshots)
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
                odds_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
                odds = (
                    Daily223OddsFeed(
                        odds_key, config["odds_region"], config["max_odds_credits"], snapshots
                    )
                    if odds_key
                    else UnavailableOddsFeed(config["odds_region"], config["max_odds_credits"])
                )
                report_progress(
                    "collecting", "Mérkőzések, szolgáltatói szorzók és háttéradatok gyűjtése…"
                )
                bundle = run_portfolio(
                    config,
                    settings,
                    football,
                    odds,
                    CacheStore(db),
                    day,
                    make_reviewer(settings),
                    ledger,
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
