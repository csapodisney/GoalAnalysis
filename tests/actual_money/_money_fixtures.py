"""TESTONLY original fixture builders; no provider or live data."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from goal_analysis.storage.portfolio_ledger import _financial


def write_fixture_evidence(root, ledger, filename, payload):
    """Emit actual TESTONLY products once; no production data/clock claims."""
    project = Path(__file__).resolve().parents[2]
    app_files = (
        "src/goal_analysis/storage/portfolio_ledger.py",
        "src/goal_analysis/storage/rolling_ticket_kpis.py",
        "src/goal_analysis/storage/monetary_projection.py",
        "src/goal_analysis/learning/_metrics.py",
        "src/goal_analysis/portfolio_dashboard.py",
        "src/goal_analysis/web/app.js",
        "src/goal_analysis/web/index.html",
    )
    def sha(value):
        return hashlib.sha256(value).hexdigest()
    source = {name: sha((project / name).read_bytes()) for name in app_files}
    tables = (
        "portfolio_runs", "portfolio_tickets", "portfolio_wagers", "portfolio_results",
        "portfolio_result_observations", "portfolio_settlements", "portfolio_settlement_observations",
        "portfolio_bookmaker_returns", "portfolio_bookmaker_return_provenance",
        "portfolio_freezes", "portfolio_freeze_revisions",
    )
    products = {}
    with closing(sqlite3.connect(ledger.path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in names:
                products[table] = {"present": False, "rows": None}
                continue
            rows = [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            assert len(rows) <= 1000
            encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            products[table] = {"present": True, "rows": len(rows), "rows_sha256": sha(encoded),
                               "records": rows}
    physical = []
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(ledger.path) + suffix)
        if path.is_file():
            physical.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size,
                             "sha256": sha(path.read_bytes())})
    result = {**payload, "TESTONLY": True, "source_sha256": source,
              "timeline_role": "CONTROLLED_UNIT_TIMELINE_NOT_ACTUAL_UTC_PIT_OR_FORECAST",
              "fixture_ledger": {"relative_path": str(ledger.path.relative_to(root)),
                                 "tables": products, "physical_products": physical}}
    destination = root / filename
    assert not destination.exists()
    destination.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return result

NOW = datetime(2026, 10, 11, 10, tzinfo=UTC)

def record(identifier="one", profile="daily223", prices=(2.0, 3.0), outcomes=None, when=NOW, **updates):
    legs, settled = [], []
    for n, price in enumerate(prices, 1):
        leg = {"fixture_id": f"api_football:{n}", "api_football_fixture_id": str(n),
               "home_team": f"Home {n}", "away_team": f"Away {n}",
               "kickoff": (when + timedelta(hours=3)).isoformat(), "market_key": "h2h",
               "selection_key": "home", "period": "FULL_TIME", "decimal_price": price,
               "bookmaker": "book_api_football_8", "region": "eu", "currency": "EUR",
               "quote_provider": "api_football", "provider_market_id": 1,
               "quote_source_id": f"https://v3.football.api-sports.io/odds?fixture={n}&bookmaker=8&bet=1",
               "quoted_at": when.isoformat()}
        legs.append(leg)
        outcome = (outcomes or ["WON"] * len(prices))[n - 1]
        settled.append({**{key: leg[key] for key in ("fixture_id", "market_key", "selection_key", "period")},
                        "outcome": outcome,
                        "reason": "BOOKMAKER_VOID_CONFIRMED" if outcome == "VOID" else "PERIOD_SCORE_CONFIRMED",
                        "note": "Explicit void confirmation" if outcome == "VOID" else None})
    statuses = {leg["outcome"] for leg in settled}
    outcome = next((status for status in ("LOST", "REVIEW", "PENDING") if status in statuses),
                   "VOID" if statuses == {"VOID"} else "WON")
    return {"recommendation_id": identifier, "run_id": "run-" + identifier, "profile_id": profile,
            "profile_name": profile, "date": when.date().isoformat(), "created_at": when.isoformat(),
            "publication_scope": "PAPER_AUDITION", "legs": legs, "active": True,
            "outcome": outcome, "settlement": {"outcome": outcome, "legs": settled}, **updates}

NOW = datetime(2026, 9, 20, 10, tzinfo=UTC)

def ticket(identifier="ticket-1", profile="DAILY_223", fixture_ids=(1, 2, 3), **updates):
    item = {
        "ticket_id": identifier,
        "profile_id": profile,
        "profile_name": profile,
        "date": "2026-09-20",
        "created_at": NOW.isoformat(),
        "bookmaker": "test-book",
        "region": "eu",
        "currency": "EUR",
        "combined_price": 12.0,
        "stake_eur": 5,
        "status": "READY",
        "legs": [
            {
                "fixture_id": f"api_football:{fixture_id}",
                "kickoff": (NOW + timedelta(hours=3)).isoformat(),
                "market_key": "totals_2_5",
                "selection_key": "over",
                "period": "FULL_TIME",
                "settlement": "REGULATION_90_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_PENALTIES",
                "decimal_price": price,
                "quoted_at": NOW.isoformat(),
            }
            for fixture_id, price in zip(fixture_ids, (2, 2, 3), strict=True)
        ],
    }
    item.update(updates)
    return item

def report(*tickets, identifier="run-1", observed_at=NOW):
    return {
        "run_id": identifier,
        "date": "2026-09-20",
        "observed_at": observed_at.isoformat(),
        "tickets": list(tickets),
        "usage": {"football_calls": 2, "odds_calls": 1},
        "astra": {"usage": {"input_tokens": 100, "output_tokens": 20}},
    }

def fixture(identifier, fulltime=(2, 1), halftime=(1, 0), status="FT", goals=None):
    return {
        "fixture": {"id": identifier, "status": {"short": status}},
        "score": {
            "fulltime": {"home": fulltime[0], "away": fulltime[1]},
            "halftime": {"home": halftime[0], "away": halftime[1]},
        },
        "goals": {"home": (goals or fulltime)[0], "away": (goals or fulltime)[1]},
    }

class Football:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def request(self, endpoint, params):
        self.calls.append((endpoint, params))
        item = self.rows[params["id"]]
        if isinstance(item, Exception):
            raise item
        return {"response": [item]}

def finish(ledger, rows=None):
    ledger.now = lambda: NOW + timedelta(hours=6)
    client = Football(rows or {i: fixture(i) for i in (1, 2, 3)})
    return ledger.refresh_results(client), client

def test_postponed_fixture_requires_bookmaker_void_confirmation(ledger):
    ledger.save_run(report(ticket()))
    ledger.record_wager("ticket-1", actual_combined_price=12.6)
    finish(ledger, {1: fixture(1, status="PST"), 2: fixture(2), 3: fixture(3)})
    assert ledger.get_ticket("ticket-1")["outcome"] == "REVIEW"
    view = ledger.record_void(
        "ticket-1", "api_football:1", "Bookmaker explicitly refunded this selection"
    )
    assert view["outcome"] == "REVIEW"
    assert view["financial"]["payout_eur"] == 0
    assert not view["financial"]["settled"]
    # Actual accepted first-leg odds were 2.1: total odds alone cannot determine this.
    view = ledger.confirm_bookmaker_return("ticket-1", 30, "Bookmaker confirmed 30 EUR returned")
    assert view["outcome"] == "WON"
    assert view["financial"]["payout_eur"] == 30
    assert view["financial"]["net_eur"] == 29
    ledger.record_void("ticket-1", "api_football:2", "Bookmaker explicitly voided leg 2")
    assert ledger.get_ticket("ticket-1")["outcome"] == "REVIEW"
    view = ledger.record_void("ticket-1", "api_football:3", "Bookmaker explicitly voided leg 3")
    assert view["outcome"] == "VOID"
    assert view["financial"]["payout_eur"] == 1
    assert view["financial"]["net_eur"] == 0

END = date(2026, 10, 22)

def item(identifier='a', profile='p1', day=END, price=10, outcome='WON', version='v1', actual=False):
    when=datetime.combine(day,datetime.min.time(),UTC)+timedelta(hours=10)
    value=record(identifier,profile,when=when,combined_price=price,outcomes=[outcome,outcome],
                 ticket_id=identifier,stake_eur=5 if actual else 1,strategy_version=version)
    for leg,settled in zip(value['legs'],value['settlement']['legs'],strict=True):
        leg['fixture_id']=settled['fixture_id']=identifier+':'+leg['fixture_id']
    value['settlement']['settled_at']=(when+timedelta(hours=6)).isoformat()
    if actual:
        value['wager']={'stake_eur':5,'actual_combined_price':price,'actual_price_source':'USER_CONFIRMED'}
    value['financial']=_financial(value) if price is not None else None
    return value
