"""Price timestamps never change the API -> Astra -> ticket -> ledger workflow."""

from datetime import timedelta
from math import prod

import pytest
from test_api_football_odds import odds_rows
from test_portfolio_flow import NOW, RecordedReviewer, environment

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.jobs.portfolio import run_portfolio, write_portfolio_bundle
from goal_analysis.portfolio_dashboard import ArthurState
from goal_analysis.storage.portfolio_ledger import PortfolioLedger

STAMP_CASES = [
    ((NOW - timedelta(days=30)).isoformat(), "OLDER"),
    ((NOW + timedelta(days=7)).isoformat(), "FUTURE"),
    (None, "MISSING"),
    ("", "MISSING"),
    ("invalid-provider-time", "INVALID"),
    ("2026-09-20T10:00:00", "INVALID"),
]


def collect(root, provider, strictness, stamp):
    cfg, football, odds, cache, _, state, _ = environment(root)
    if provider == "api_football":
        state["events"] = []
        state["football_odds"] = odds_rows()
        for row in state["football_odds"]:
            if stamp is None:
                row.pop("update")
            else:
                row["update"] = stamp
    else:
        for index, event in enumerate(state["events"]):
            book = event["bookmakers"][0]
            book.pop("last_update")
            if stamp is not None:
                if index == 0:  # Market-level timestamps take precedence, even if invalid.
                    book["last_update"] = NOW.isoformat() if stamp else stamp
                    for market in book["markets"]:
                        market["last_update"] = stamp
                else:
                    book["last_update"] = stamp
    reviewer = RecordedReviewer()
    bundle = run_portfolio(
        cfg,
        {"strictness": strictness},
        football,
        odds,
        cache,
        NOW.date(),
        reviewer,
        clock=lambda: NOW,
    )
    return bundle, reviewer


@pytest.mark.parametrize("provider", ["the_odds_api", "api_football"])
@pytest.mark.parametrize("strictness", [0, 35, 100])
@pytest.mark.parametrize("stamp,timing_status", STAMP_CASES)
def test_timestamp_does_not_change_prices_calculations_or_delivery(
    tmp_path, provider, strictness, stamp, timing_status
):
    baseline, _ = collect(tmp_path / "baseline", provider, strictness, NOW.isoformat())
    bundle, reviewer = collect(tmp_path / "changed", provider, strictness, stamp)
    report = bundle["artifacts"]["report"]
    fresh = baseline["artifacts"]["report"]
    candidates = bundle["artifacts"]["candidate_input"]["candidates"]
    original = baseline["artifacts"]["candidate_input"]["candidates"]
    assert candidates and {(c["candidate_id"], c["decimal_price"]) for c in candidates} == {
        (c["candidate_id"], c["decimal_price"]) for c in original
    }
    assert all(c["quote_timestamp_status"] == timing_status for c in candidates)
    assert all(c["quote_timestamp_raw"] == stamp for c in candidates)
    assert all(
        c["quoted_at"] == (stamp if timing_status in {"OLDER", "FUTURE"} else None)
        for c in candidates
    )

    def decisions(tickets):
        return [
            (
                t["profile_id"],
                t["combined_price"],
                t["status"],
                t["quality_warnings"],
                t.get("confidence"),
                [l["candidate_id"] for l in t["legs"]],
            )
            for t in tickets
        ]

    assert decisions(report["tickets"]) == decisions(fresh["tickets"])
    if strictness < 100:
        assert report["tickets"]
    assert report["recommendations"]
    assert decisions(report["recommendations"]) == decisions(fresh["recommendations"])
    assert all(not t["requires_refresh"] for t in report["tickets"])
    for ticket in report["tickets"] + report["recommendations"]:
        if ticket.get("odds_pending"):
            continue
        assert ticket["combined_price"] == pytest.approx(
            prod(l["decimal_price"] for l in ticket["legs"])
        )
    assert len(reviewer.calls) == 1
    packed, _ = CodexReviewer(command=["codex"])._prepare(
        reviewer.calls[0][0], report["date"], None
    )
    priced_packets = [c for c in packed["candidates"] if c.get("decimal_price") is not None]
    assert priced_packets and all(c["decimal_price"] > 1 for c in priced_packets)
    assert all(c["quote_timestamp_status"] == timing_status for c in priced_packets)
    assert {l["candidate_id"] for t in report["tickets"] for l in t["legs"]} <= {
        c["candidate_id"] for c in priced_packets
    }
    assert not any("QUOTE_STALE" in str(d) for d in report["diagnostics"])
    ledger = PortfolioLedger(tmp_path / "data/arthur/ledger.sqlite3", now=lambda: NOW)
    ledger.save_run(report)
    saved = ledger.list_tickets(date=report["date"])
    view = ArthurState(tmp_path).snapshot(report["date"])
    assert decisions(saved) == decisions(view["tickets"])
    assert all(l["quote_timestamp_raw"] == stamp for t in view["tickets"] for l in t["legs"])
    assert view["recommendations"]
    assert all(
        l["quote_timestamp_raw"] == stamp
        for t in view["recommendations"]
        for l in t["legs"]
        if l.get("decimal_price") is not None
    )
    write_portfolio_bundle(tmp_path / "export", bundle)
    assert '"quote_timestamp_status"' in (tmp_path / "export/report.json").read_text()
