"""Lossless provider-native odds archival; no live product promotion or I/O.

Decimal strings are canonical for identity, while native fragments and the full
response retain original spelling/precision. A receipt never becomes quote time.
SUPPORTED only means this archive parser understands the named product shape.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import parse_qs, urlsplit

PROVIDERS = ("api_football", "the_odds_api", "the_stats_api")
_API_MARKETS = {
    "match winner": ("h2h", "FULL_TIME"), "first half winner": ("h2h", "FIRST_HALF"),
    "goals over/under": ("totals", "FULL_TIME"),
    "goals over/under first half": ("totals", "FIRST_HALF"),
    "both teams score": ("btts", "FULL_TIME"),
    "both teams score - first half": ("btts", "FIRST_HALF"),
}
_STATS_MARKETS = {
    "match_odds": ("h2h", "FULL_TIME"), "first_half_result": ("h2h", "FIRST_HALF"),
    "btts": ("btts", "FULL_TIME"), "btts_first_half": ("btts", "FIRST_HALF"),
    "total_goals": ("totals", "FULL_TIME"), "first_half_total_goals": ("totals", "FIRST_HALF"),
    "draw_no_bet": ("dnb", "FULL_TIME"),
    "asian_handicap": ("asian_handicap", "FULL_TIME"),
    "handicap_result": ("handicap_3_way", "FULL_TIME"),
}
_ODDS_MARKETS = {
    "h2h": ("h2h", "FULL_TIME"), "totals": ("totals", "FULL_TIME"),
    "btts": ("btts", "FULL_TIME"), "h2h_h1": ("h2h", "FIRST_HALF"),
    "totals_h1": ("totals", "FIRST_HALF"), "btts_h1": ("btts", "FIRST_HALF"),
}


def _copy(value):
    def validate(item):
        if type(item) is dict:
            if any(not isinstance(key, str) for key in item):
                raise ValueError
            for child in item.values():
                validate(child)
        elif type(item) is list:
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, bool, int, float):
            raise ValueError
    try:
        validate(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("FINITE_JSON_REQUIRED") from None


def _text(value):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or any(char in value for char in "\x00\r\n")):
        raise ValueError("NONEMPTY_NATIVE_TEXT_REQUIRED")
    return value


def _opaque_selection(value):
    """Retain unsupported native labels without inferring a canonical outcome.

    Only newly recovered JSON-number labels get an extra identity descriptor;
    existing string products retain their complete original representation.
    """
    if type(value) in (int, float):
        return json.dumps(value, allow_nan=False, separators=(",", ":")), "JSON_NUMBER"
    if isinstance(value, str):
        if not value.strip() or any(char in value for char in "\x00\r\n"):
            raise ValueError("NONEMPTY_NATIVE_TEXT_REQUIRED")
        return value, None
    if value is None:
        raise ValueError("NONEMPTY_NATIVE_TEXT_REQUIRED")
    raise ValueError("UNSUPPORTED_NATIVE_SELECTION_TYPE")


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError("OBJECT_REQUIRED")  # noqa: TRY004 - uniform external record rejection
    return value


def _list(value):
    if not isinstance(value, list):
        raise ValueError("LIST_REQUIRED")  # noqa: TRY004 - uniform external record rejection
    return value


def _id(value, *, integer=False):
    if integer:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("POSITIVE_NATIVE_ID_REQUIRED")
        value = str(value)
        if not re.fullmatch(r"[0-9]+", value) or not value.strip("0"):
            raise ValueError("POSITIVE_NATIVE_ID_REQUIRED")
        return value
    return _text(value)


def _optional_id(value):
    if value is None:
        return None
    return _id(value, integer=True) if type(value) is int else _id(value)


def _number(value, *, price=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("INVALID_DECIMAL")  # noqa: TRY004 - uniform external record rejection
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or price and number <= 1 or len(str(value)) > 100
                or abs(number.as_tuple().exponent) > 50 or number.adjusted() > 50
                or len(number.as_tuple().digits) > 100):
            raise ValueError
        if number == 0:
            return "0"
        fixed = format(number, "f")
        return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError("INVALID_DECIMAL_PRICE" if price else "INVALID_DECIMAL_LINE") from None


def _stamp(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("AWARE_TIMESTAMP_REQUIRED") from None


def _quote_time(value, observed):
    if value is None:
        return None, "MISSING"
    try:
        quote = _stamp(value)
    except ValueError:
        return None, "INVALID"
    if quote > observed:
        return None, "FUTURE"
    return quote.isoformat(timespec="microseconds"), "KNOWN"


def _path(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


class _Archive:
    def __init__(self, provider, observed, url, sha, context):
        self.provider, self.observed, self.url, self.sha = provider, observed, url, sha
        self.context = context
        self.records, self.unsupported, self.rejected = [], [], []

    def issue(self, path, value, reason, *, unsupported=False):
        (self.unsupported if unsupported else self.rejected).append(
            {"path": path, "reason": reason, "raw": _copy(value)})

    def add(self, *, fixture, book, book_name, book_id, native_market, market_id, selection,
            selection_id, family, period, line, price, price_kind, quote_time, raw, path,
            supported=True, competition=None, season=None, product_metadata=None):
        quoted, status = _quote_time(quote_time, self.observed)
        self.records.append({
            "provider": self.provider, "fixture_id": fixture,
            "competition_id": competition, "season_id": season,
            "bookmaker": book, "bookmaker_name": book_name, "bookmaker_id": book_id,
            "market_key": family, "provider_market_key": native_market,
            "provider_market_id": market_id, "selection_key": selection,
            "provider_selection_id": selection_id, "period": period,
            "settlement": "REGULATION" if supported else "UNKNOWN",
            "line": _number(line) if line is not None else None,
            "region": self.context.get("region"), "currency": self.context.get("currency"),
            "decimal_price": _number(price, price=True), "price_kind": price_kind,
            "quoted_at": quoted, "quote_time_status": status,
            "observed_at": self.observed.isoformat(timespec="microseconds"),
            "source_url": self.url, "source_sha256": self.sha,
            "mapping_status": "SUPPORTED" if supported else "UNSUPPORTED",
            "raw_path": path, "raw": _copy(raw if isinstance(raw, dict) else {"value": raw}),
            **(product_metadata or {}),
        })


def _api(archive, payload):
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("INVALID_API_FOOTBALL_ODDS_RESPONSE")
    for ri, row in enumerate(_list(payload.get("response"))):
        path = _path("/response", ri)
        try:
            row = _mapping(row)
            fixture = _id(_mapping(row.get("fixture")).get("id"), integer=True)
            league = _mapping(row.get("league"))
            competition = _id(league.get("id"), integer=True)
            season = league.get("season")
            if type(season) is int:
                season = str(season)
            if season is not None:
                season = _text(season)
            books = _list(row.get("bookmakers"))
        except ValueError as error:
            archive.issue(path, row, str(error))
            continue
        for bi, book in enumerate(books):
            bp = _path(_path(path, "bookmakers"), bi)
            try:
                book = _mapping(book)
                book_id = _id(book.get("id"), integer=True)
                name, bets = _text(book.get("name")), _list(book.get("bets"))
            except ValueError as error:
                archive.issue(bp, book, str(error))
                continue
            for mi, market in enumerate(bets):
                mp = _path(_path(bp, "bets"), mi)
                try:
                    market = _mapping(market)
                    native = _text(market.get("name"))
                    market_id = _id(market.get("id"), integer=True)
                    family, period = _API_MARKETS.get(" ".join(native.casefold().split()), (native, "UNKNOWN"))
                    supported = period != "UNKNOWN"
                    values = _list(market.get("values"))
                    if not supported:
                        archive.issue(mp, market, "UNSUPPORTED_MARKET", unsupported=True)
                except ValueError as error:
                    archive.issue(mp, market, str(error))
                    continue
                for oi, outcome in enumerate(values):
                    op = _path(_path(mp, "values"), oi)
                    try:
                        outcome = _mapping(outcome)
                        selection_type = None
                        if supported:
                            native_selection = _text(outcome.get("value"))
                        else:
                            native_selection, selection_type = _opaque_selection(outcome.get("value"))
                        selection, line = native_selection, None
                        if supported and family == "totals":
                            match = re.fullmatch(r"(Over|Under) ([+-]?[0-9]+(?:\.[0-9]+)?)", native_selection)
                            if not match:
                                raise ValueError("UNSUPPORTED_TOTALS_SELECTION")
                            selection, line = match[1].lower(), match[2]
                        elif supported:
                            selection = native_selection.casefold()
                            if selection not in ({"home", "draw", "away"} if family == "h2h" else {"yes", "no"}):
                                raise ValueError("UNSUPPORTED_SELECTION")
                        archive.add(fixture=fixture, book=book_id, book_name=name, book_id=book_id,
                            native_market=native, market_id=market_id, selection=selection,
                            selection_id=_id(outcome["id"], integer=True) if outcome.get("id") is not None else None,
                            family=family, period=period, line=line, price=outcome.get("odd"),
                            price_kind="CURRENT", quote_time=row.get("update"), raw=outcome, path=op,
                            supported=supported, competition=competition, season=season)
                        if selection_type is not None:
                            archive.records[-1]["provider_selection_type"] = selection_type
                    except ValueError as error:
                        archive.issue(op, outcome, str(error),
                            unsupported=str(error) == "UNSUPPORTED_NATIVE_SELECTION_TYPE")


def _odds(archive, payload):
    events = payload if isinstance(payload, list) else [payload]
    for ei, event in enumerate(events):
        path = _path("", ei) if isinstance(payload, list) else ""
        try:
            event = _mapping(event)
            fixture = _id(event.get("id"))
            books = _list(event.get("bookmakers"))
        except ValueError as error:
            archive.issue(path, event, str(error))
            continue
        for bi, book in enumerate(books):
            bp = _path(_path(path, "bookmakers"), bi)
            try:
                book = _mapping(book)
                key = _id(book.get("key"))
                title = _text(book.get("title", key))
                markets = _list(book.get("markets"))
            except ValueError as error:
                archive.issue(bp, book, str(error))
                continue
            for mi, market in enumerate(markets):
                mp = _path(_path(bp, "markets"), mi)
                try:
                    market = _mapping(market)
                    native = _text(market.get("key"))
                    family, period = _ODDS_MARKETS.get(native, (native, "UNKNOWN"))
                    supported = period != "UNKNOWN" and str(event.get("sport_key", "")).startswith("soccer_")
                    if not supported:
                        family, period = native, "UNKNOWN"
                    values = _list(market.get("outcomes"))
                    if not supported:
                        archive.issue(mp, market, "UNSUPPORTED_MARKET", unsupported=True)
                except ValueError as error:
                    archive.issue(mp, market, str(error))
                    continue
                # An explicitly invalid market time cannot borrow the book time.
                quoted = market["last_update"] if "last_update" in market else book.get("last_update")
                for oi, outcome in enumerate(values):
                    op = _path(_path(mp, "outcomes"), oi)
                    try:
                        outcome = _mapping(outcome)
                        selection = _text(outcome.get("name"))
                        if supported and family == "h2h":
                            home, away = event.get("home_team"), event.get("away_team")
                            if home == away or selection not in (home, away, "Draw"):
                                raise ValueError("AMBIGUOUS_H2H_SELECTION")
                            selection = "home" if selection == home else "away" if selection == away else "draw"
                        elif supported:
                            selection = selection.casefold()
                            if selection not in ({"over", "under"} if family == "totals" else {"yes", "no"}):
                                raise ValueError("UNSUPPORTED_SELECTION")
                        if supported and family == "totals" and outcome.get("point") is None:
                            raise ValueError("MISSING_TOTALS_LINE")
                        archive.add(fixture=fixture, book=key, book_name=title, book_id=key,
                            native_market=native, market_id=_optional_id(market.get("id")), selection=selection,
                            selection_id=_optional_id(outcome.get("id")), family=family, period=period,
                            line=outcome.get("point"), price=outcome.get("price"), price_kind="CURRENT",
                            quote_time=quoted, raw=outcome, path=op, supported=supported,
                            competition=event.get("sport_key"))
                    except ValueError as error:
                        archive.issue(op, outcome, str(error))


def _stats_lines(archive, value, path, family):
    """Reject ambiguous aliases and non-quarter lines, retaining original raw data."""
    if not isinstance(value, dict):
        archive.issue(path, value, "UNSUPPORTED_LINE_SHAPE", unsupported=True)
        return
    parsed = []
    for native, selections in value.items():
        lp = _path(path, native)
        try:
            line = _number(native)
            with localcontext() as context:
                context.prec = 110
                number = Decimal(line)
                if number % (1 if family == "handicap_3_way" else Decimal(".25")):
                    raise ValueError("UNSUPPORTED_LINE_INCREMENT")
                if family == "totals" and number < 0:
                    raise ValueError("NEGATIVE_TOTALS_LINE")
            parsed.append((line, selections, lp))
        except ValueError as error:
            archive.issue(lp, selections, str(error), unsupported=True)
    counts = {}
    for line, _, _ in parsed:
        counts[line] = counts.get(line, 0) + 1
    for line, selections, lp in parsed:
        if counts[line] != 1:
            archive.issue(lp, selections, "AMBIGUOUS_CANONICAL_LINE", unsupported=True)
        else:
            yield line, selections, lp


def _stats_groups(archive, family, value, path):
    if family == "asian_handicap":
        if not isinstance(value, dict):
            archive.issue(path, value, "UNSUPPORTED_SELECTION_SHAPE", unsupported=True)
            return
        for selection, ladder in value.items():
            sp = _path(path, selection)
            if selection not in {"home", "away"}:
                archive.issue(sp, ladder, "UNSUPPORTED_SELECTION", unsupported=True)
                continue
            for line, raw, lp in _stats_lines(archive, ladder, sp, family):
                # Lines are relative to the selected team, including away lines.
                # Do not negate them or assume an opposite side is priced.
                yield line, {selection: raw}, lp, True
    elif family in {"totals", "handicap_3_way"}:
        for line, selections, lp in _stats_lines(archive, value, path, family):
            yield line, selections, lp, False
    else:
        yield None, value, path, False


def _stats_product(family, line):
    """Payout rules are distinct from REGULATION match duration. No live admission."""
    if family == "dnb":
        rule, basis = "DRAW_REFUND", "NONE"
    elif family == "handicap_3_way":
        rule, basis = "EUROPEAN_THREE_WAY", "HOME_TEAM"
    elif family in {"asian_handicap", "totals"} and line is not None:
        with localcontext() as context:
            context.prec = 110
            fraction = abs(Decimal(line)) % 1
        rule = "SPLIT_HALF_STAKES" if fraction in {Decimal(".25"), Decimal(".75")} else (
            "PUSH_ON_EQUAL" if fraction == 0 else "BINARY_NO_PUSH")
        if family == "totals" and fraction == Decimal(".5"):
            return {}  # Preserve the existing half-line product descriptor.
        basis = "SELECTED_TEAM" if family == "asian_handicap" else "TOTAL_GOALS"
    else:
        return {}
    return {"payout_rule": rule, "line_basis": basis, "eligibility": "SHADOW_ONLY"}


def _stats(archive, payload):
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    data = _mapping(data)
    fixture = _id(data.get("match_id"))
    if not re.fullmatch(r"mt_[0-9]+", fixture):
        raise ValueError("INVALID_STATS_MATCH_ID")
    for bi, book in enumerate(_list(data.get("bookmakers"))):
        bp = _path("/data/bookmakers" if isinstance(payload, dict) and "data" in payload else "/bookmakers", bi)
        try:
            book = _mapping(book)
            key = _text(book.get("bookmaker"))
            markets = _mapping(book.get("markets"))
        except ValueError as error:
            archive.issue(bp, book, str(error), unsupported=True)
            continue
        for native, value in markets.items():
            mp = _path(_path(bp, "markets"), native)
            family, period = _STATS_MARKETS.get(native, (native, "UNKNOWN"))
            supported = period != "UNKNOWN"
            if not supported:
                archive.issue(mp, value, "UNSUPPORTED_MARKET", unsupported=True)
            groups = _stats_groups(archive, family, value, mp) if supported else [(None, value, mp, False)]
            for line, selections, sp, selection_in_path in groups:
                if not isinstance(selections, dict):
                    archive.issue(sp, selections, "UNSUPPORTED_SELECTION_SHAPE", unsupported=True)
                    continue
                for selection, raw in selections.items():
                    op = sp if selection_in_path else _path(sp, selection)
                    if supported and selection not in ({"home", "draw", "away"} if family == "h2h" else
                            {"over", "under"} if family == "totals" else
                            {"home", "away"} if family in {"dnb", "asian_handicap"} else
                            {"home", "draw", "away"} if family == "handicap_3_way" else {"yes", "no"}):
                        archive.issue(op, raw, "UNSUPPORTED_SELECTION", unsupported=True)
                        continue
                    values = raw if isinstance(raw, dict) else {"last_seen": raw}
                    for native_price, price_kind in (("opening", "OPENING"), ("last_seen", "LAST_SEEN")):
                        if values.get(native_price) is None:
                            continue
                        try:
                            archive.add(fixture=fixture, book=key, book_name=key, book_id=_optional_id(book.get("id")),
                                native_market=native, market_id=None, selection=selection,
                                selection_id=_optional_id(values.get("id")), family=family, period=period, line=line,
                                price=values[native_price], price_kind=price_kind,
                                quote_time=values.get(native_price + "_at"), raw=raw,
                                path=_path(op, native_price) if isinstance(raw, dict) else op,
                                supported=supported, competition=data.get("competition_id"), season=data.get("season_id"),
                                product_metadata=_stats_product(family, line) if supported else None)
                        except ValueError as error:
                            archive.issue(op, raw, str(error))


def normalize_odds_snapshot(provider, raw, *, observed_at, source_url, source_sha256, context=None):
    """Return archived records and explicit diagnostics; never query or join names.

    region/currency are retained only when supplied explicitly in context. Native
    IDs and unlike bookmaker products stay scoped to provider. Bad quote times
    do not erase prices: quote_time_status records the missing/invalid/future
    evidence while quoted_at remains null. The original timestamp stays in raw.
    """
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("UNSUPPORTED_ODDS_PROVIDER")
    observed = _stamp(observed_at)
    source_url = _source_url(source_url)
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise ValueError("SHA256_REQUIRED")
    if context is not None and not isinstance(context, Mapping):
        raise ValueError("CONTEXT_MAPPING_REQUIRED")
    context = dict(context or {})
    for field in ("region", "currency"):
        if context.get(field) is not None:
            _text(context[field])
    frozen = _copy(raw)
    archive = _Archive(provider, observed, source_url, source_sha256.lower(), context)
    {"api_football": _api, "the_odds_api": _odds, "the_stats_api": _stats}[provider](archive, frozen)
    return {"records": archive.records, "unsupported": archive.unsupported,
            "rejected": archive.rejected, "raw": frozen}


def _source_url(value):
    if value is None:
        return None
    try:
        value = _text(value)
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or any(char.isspace() for char in value)
                or parsed.port not in (None, 443)
                or any(re.search(r"key|token|secret|password|authorization", key, re.IGNORECASE)
                       for key in parse_qs(parsed.query, keep_blank_values=True))):
            raise ValueError
    except ValueError:
        raise ValueError("CREDENTIAL_FREE_HTTPS_SOURCE_REQUIRED") from None
    return value


def _saved_fixture(provider, row):
    """Only provider URL identity is evidence; Arthur fixture IDs never alias it."""
    try:
        url = urlsplit(_source_url(row.get("quote_source_id")) or "")
        if provider == "api_football" and url.hostname == "v3.football.api-sports.io" and url.path == "/odds":
            values = parse_qs(url.query).get("fixture", [])
            if len(values) == 1:
                return _id(values[0], integer=True)
        if provider == "the_odds_api" and url.hostname == "api.the-odds-api.com":
            match = re.fullmatch(r"/v4/sports/[A-Za-z0-9_-]+/events/([A-Za-z0-9_-]+)/odds", url.path)
            if match:
                return _id(match[1])
        if provider == "the_stats_api" and url.hostname == "api.thestatsapi.com":
            # Keep the archived pre-migration spelling readable too. Neither
            # spelling promotes a record into live quote eligibility.
            match = re.fullmatch(r"(?:/api)?/football/matches/(mt_[0-9]+)/odds", url.path)
            if match:
                return match[1]
    except ValueError:
        pass
    return None


def normalize_saved_quotes(provider, rows, *, observed_at, source_url=None, source_sha256):
    """Archive compact monitor quotes without claiming their parent hash is API bytes.

    Native fixture identity comes only from an exact provider event URL. Otherwise
    an explicit UNKNOWN_NATIVE_ID namespace preserves the Arthur reference.
    """
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("UNSUPPORTED_ODDS_PROVIDER")
    observed, source_url = _stamp(observed_at), _source_url(source_url)
    if not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise ValueError("SHA256_REQUIRED")
    frozen = _copy(_list(rows))
    archive = _Archive(provider, observed, source_url, source_sha256.lower(), {})
    for index, row in enumerate(frozen):
        path = _path("", index)
        try:
            row = _mapping(row)
            if row.get("quote_provider", provider) != provider:
                raise ValueError("QUOTE_PROVIDER_MISMATCH")
            fixture = _saved_fixture(provider, row)
            known = fixture is not None
            if not known:
                fixture = "UNKNOWN_NATIVE_ID:" + _text(row.get("fixture_id"))
                archive.issue(path, row, "UNKNOWN_NATIVE_ID", unsupported=True)
            market, selection = _text(row.get("market_key")), _text(row.get("selection_key"))
            recognized = market in {"h2h", "h2h_h1", "h2h_3_way_h1", "totals", "totals_h1", "btts", "btts_h1"}
            if not recognized:
                archive.issue(path, row, "UNSUPPORTED_MARKET", unsupported=True)
            period, settlement = _text(row.get("period")), _text(row.get("settlement"))
            price_kind = row.get("price_kind", "LAST_SEEN" if provider == "the_stats_api" else "CURRENT")
            if price_kind not in {"CURRENT", "OPENING", "LAST_SEEN", "PRE_CLOSING", "CLOSING"}:
                raise ValueError("UNKNOWN_PRICE_KIND")
            archive.context = {field: row.get(field) for field in ("region", "currency")}
            for value in archive.context.values():
                if value is not None:
                    _text(value)
            archive.add(fixture=fixture, book=_text(row.get("bookmaker")),
                book_name=_text(row.get("bookmaker_name", row.get("provider_bookmaker", row.get("bookmaker")))),
                book_id=_optional_id(row.get("bookmaker_id")),
                native_market=_text(row.get("provider_market_key", market)),
                market_id=_optional_id(row.get("provider_market_id")), selection=selection,
                selection_id=_optional_id(row.get("provider_selection_id")), family=market,
                period=period, line=row.get("line"), price=row.get("decimal_price"),
                price_kind=price_kind, quote_time=row.get("quote_timestamp_raw", row.get("quoted_at")),
                raw=row, path=path, supported=known and recognized)
            archive.records[-1]["settlement"] = settlement
            archive.records[-1]["fixture_identity_status"] = "NATIVE_URL" if known else "UNKNOWN_NATIVE_ID"
        except ValueError as error:
            archive.issue(path, row, str(error))
    return {"records": archive.records, "unsupported": archive.unsupported,
            "rejected": archive.rejected, "raw": frozen}
