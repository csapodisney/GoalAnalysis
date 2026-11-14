"""Actual HTTP serializer and page boundaries, using memory streams only."""
import io
import json

import pytest

from goal_analysis.jobs.agent_snapshot import json_text
from goal_analysis.portfolio_dashboard import make_handler
from goal_analysis.workshop_views import DETAIL_BYTES, page


def _handler():
    handler = object.__new__(make_handler(None, "TESTONLY-private-token"))
    statuses, headers, endings = [], [], []
    handler.wfile = io.BytesIO()
    handler.send_response = statuses.append
    handler.send_header = lambda name, value: headers.append((name, value))
    handler.end_headers = lambda: endings.append(True)
    return handler, statuses, headers, endings


def test_actual_send_matches_near_limit_multibyte_page_and_continuation():
    rows = [{"fixture_id": f"TESTONLY-{index}", "label": "árvíztűrő",
             "probability": 0.0, "available": False, "missing": None}
            for index in range(10000)]
    items = [{"ticket_id": "TESTONLY-first", "rows": rows, "padding": ""},
             {"ticket_id": "TESTONLY-next", "state": "UNKNOWN"}]
    metadata = {"run_id": "2026-11-15-TESTONLY", "target_date": "2026-11-15"}
    initial = page(items, offset=0, limit=1, maximum=32, metadata=metadata)
    remaining = DETAIL_BYTES - 1024 - len(json_text(initial).encode("utf-8"))
    assert remaining > 0
    items[0]["padding"] = "x" * remaining
    first = page(items, offset=0, limit=1, maximum=32, metadata=metadata)
    assert len(json_text(first).encode("utf-8")) == DETAIL_BYTES - 1024
    assert len(json.dumps(first, ensure_ascii=False, allow_nan=False).encode("utf-8")) > DETAIL_BYTES
    handler, statuses, headers, endings = _handler()
    handler._send(first)
    wire = handler.wfile.getvalue()
    assert len(wire) == len(json_text(first).encode("utf-8")) <= DETAIL_BYTES
    assert dict(headers)["Content-Length"] == str(len(wire))
    assert dict(headers)["Content-Type"] == "application/json; charset=utf-8"
    assert statuses == [200] and endings == [True]
    assert json.loads(wire) == first
    assert len(first["items"][0]["rows"]) == 10000
    assert first["items"][0]["rows"][0]["available"] is False
    assert first["items"][0]["rows"][0]["missing"] is None
    assert first["items"][0]["rows"][0]["probability"] == 0.0
    assert first["next_offset"] == 1 and first["total"] == 2
    second = page(items, offset=first["next_offset"], limit=1, maximum=32, metadata=metadata)
    assert second["items"] == [items[1]] and second["next_offset"] is None
    assert first["items"] + second["items"] == items


def test_actual_send_retains_text_bytes_error_headers_and_nonfinite_rejection():
    for value, media, expected in [
            ("Árvíz <html>", "text/html; charset=utf-8", "Árvíz <html>".encode()),
            (b"TESTONLY\x00bytes", "application/octet-stream", b"TESTONLY\x00bytes")]:
        handler, statuses, headers, endings = _handler()
        handler._send(value, status=206, media=media, download="TESTONLY.dat",
                      nonce="TESTONLY-nonce", close=True)
        saved = dict(headers)
        assert handler.wfile.getvalue() == expected
        assert statuses == [206] and endings == [True]
        assert saved["Content-Type"] == media and saved["Content-Length"] == str(len(expected))
        assert saved["Connection"] == "close" and saved["Cache-Control"] == "no-store"
        assert saved["X-Content-Type-Options"] == "nosniff"
        assert saved["Referrer-Policy"] == "no-referrer" and saved["X-Frame-Options"] == "DENY"
        assert saved["Content-Disposition"] == 'attachment; filename="TESTONLY.dat"'
        assert "'nonce-TESTONLY-nonce'" in saved["Content-Security-Policy"]
    handler, statuses, headers, endings = _handler()
    error = {"code": "TESTONLY_REFUSED", "details": [None, False, 0, "UNKNOWN", "ő"]}
    handler._send(error, status=400, inline="TESTONLY.json")
    assert statuses == [400] and endings == [True]
    assert json.loads(handler.wfile.getvalue()) == error
    assert dict(headers)["Content-Length"] == str(len(handler.wfile.getvalue()))
    assert dict(headers)["Content-Disposition"] == 'inline; filename="TESTONLY.json"'
    handler, statuses, headers, endings = _handler()
    with pytest.raises(ValueError):
        handler._send({"value": float("nan")})
    assert handler.wfile.getvalue() == b"" and statuses == headers == endings == []
