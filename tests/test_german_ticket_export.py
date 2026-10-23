"""German, match-only text export exposed on the Results page."""

import threading
import urllib.request
from http.server import ThreadingHTTPServer

from goal_analysis.portfolio_dashboard import german_ticket_text, make_handler


def leg(home, away, market, selection, period="FULL_TIME", **extra):
    return {
        "candidate_id": f"{home}-{away}-{market}-{selection}",
        "home_team": home,
        "away_team": away,
        "market_key": market,
        "selection_key": selection,
        "period": period,
        "decimal_price": 9.99,
        **extra,
    }


def snapshot():
    return {
        "selected_date": "2026-10-16",
        "latest": None,
        "recommendations": [
            {
                "recommendation_id": "daily-rec",
                "portfolio_ticket_id": "daily-ticket",
                "profile_id": "daily223",
                "status": "READY",
                "legs": [
                    leg("FC Eindhoven", "Vitesse", "h2h", "away"),
                    leg("Grenoble", "Boulogne", "h2h", "draw"),
                ],
            },
            {
                "recommendation_id": "rhythm-rec",
                "profile_id": "ritmusor",
                "legs": [leg("Frankfurt", "Köln", "btts", "yes")],
            },
            {"recommendation_id": "empty", "profile_id": "orszem", "legs": []},
        ],
        "tickets": [
            {
                "ticket_id": "daily-ticket",
                "profile_id": "daily223",
                "legs": [leg("FC Eindhoven", "Vitesse", "h2h", "away")],
            },
            {
                "ticket_id": "half-ticket",
                "profile_id": "h1_over05",
                "legs": [
                    leg(
                        "Cottbus",
                        "Nürnberg",
                        "totals_0_5",
                        "over",
                        "FIRST_HALF",
                    )
                ],
            },
        ],
    }


EXPECTED = """DAILY_223
FC Eindhoven – Vitesse | Auswärtssieg
Grenoble – Boulogne | Unentschieden

Rhythmuswächter
Frankfurt – Köln | Beide Teams treffen: Ja

Auftakt
Cottbus – Nürnberg | 1. Halbzeit: Über 0,5 Tore
"""


def test_german_ticket_text_has_only_names_matches_and_bet_types():
    result = german_ticket_text(snapshot())

    assert result == EXPECTED
    assert "READY" not in result
    assert "9,99" not in result and "9.99" not in result
    assert result.count("\n\n") == 2


def test_text_endpoint_opens_inline_with_selected_date_filename():
    class State:
        def snapshot(self, day=None, *, full_export=True):
            assert day == "2026-10-16"
            assert full_export is False
            return snapshot()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(State(), "local-session"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/tickets.txt?date=2026-10-16"
        with urllib.request.urlopen(url) as reply:
            assert reply.read().decode("utf-8") == EXPECTED
            assert reply.headers["Content-Type"] == "text/plain; charset=utf-8"
            assert reply.headers["Content-Disposition"] == (
                'inline; filename="Arthur-Tippscheine-2026-10-16.txt"'
            )
            assert reply.headers["Cache-Control"] == "no-store"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
