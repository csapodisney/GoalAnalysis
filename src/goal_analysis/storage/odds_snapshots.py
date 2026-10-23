"""Append-only SHADOW quote archive, separate from all selection rules.

Source receipts and product descriptors are stored once. Native bookmaker and
market namespaces stay provider-scoped; archive membership is not eligibility.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import utc

from .canonical_registry import digest, encoded

PROVIDERS = {"the_stats_api", "api_football", "the_odds_api", "football_data_co_uk"}
PARENT = {"provider", "observed_at", "source_url", "source_sha256"}
VALUE = {"decimal_price", "price_kind", "quoted_at", "quote_time_status", "raw", "raw_path"}
IDENTITY = {"fixture_id", "bookmaker", "market_key", "selection_key", "period", "settlement"}
TABLES = ("sports_odds_sources", "sports_odds_products", "sports_odds_quotes", "sports_odds_publications")


def stamp(value):
    return utc(value).isoformat(timespec="microseconds")


def price(value):
    if not isinstance(value, str):
        raise ValueError("EXACT_DECIMAL_PRICE_REQUIRED")  # noqa: TRY004 - uniform archive rejection
    try:
        number = Decimal(value)
        if (not number.is_finite() or number <= 1 or len(value) > 100
                or not -100 <= number.as_tuple().exponent <= 100 or number.adjusted() > 99):
            raise ValueError
        text = format(number, "f")
        if len(text) > 100:
            raise ValueError
        return text.rstrip("0").rstrip(".") if "." in text else text
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_DECIMAL_PRICE") from None


def _public_url(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("INVALID_PUBLIC_SOURCE_URL")  # noqa: TRY004 - uniform archive rejection
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(re.search("key|token|secret|password|authorization", key, re.IGNORECASE)
                                     for key in parse_qs(parsed.query))):
        raise ValueError("CREDENTIAL_FREE_PUBLIC_HTTPS_REQUIRED")
    return value


class OddsSnapshots:
    def __init__(self, database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))
        self._last = None

    def initialize(self):
        with self.database.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS sports_odds_sources (
                source_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, source_url TEXT, source_ref TEXT NOT NULL,
                observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
                issue_counts_json TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sports_odds_products (
                product_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                fixture_id TEXT NOT NULL, bookmaker TEXT NOT NULL,
                market_key TEXT NOT NULL, selection_key TEXT NOT NULL,
                product_json TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sports_odds_quotes (
                quote_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sports_odds_sources(source_id),
                product_id TEXT NOT NULL REFERENCES sports_odds_products(product_id),
                decimal_price TEXT NOT NULL, price_kind TEXT NOT NULL,
                quoted_at TEXT, quote_time_status TEXT NOT NULL,
                raw_path TEXT NOT NULL, raw_json TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sports_odds_publications (
                source_id TEXT PRIMARY KEY REFERENCES sports_odds_sources(source_id),
                sealed_at TEXT NOT NULL, quote_count INTEGER NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS sports_odds_source_time ON sports_odds_sources(provider,observed_at,ingested_at)")
            db.execute("CREATE INDEX IF NOT EXISTS sports_odds_fixture ON sports_odds_products(provider,fixture_id,bookmaker,market_key)")
            db.execute("CREATE INDEX IF NOT EXISTS sports_odds_product_time ON sports_odds_quotes(product_id,quoted_at,source_id)")
            db.execute("CREATE INDEX IF NOT EXISTS sports_odds_source_quotes ON sports_odds_quotes(source_id)")
            db.execute("CREATE INDEX IF NOT EXISTS sports_odds_time_status ON sports_odds_quotes(quote_time_status,source_id)")
            for table in TABLES:
                for operation in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT,'odds snapshots are append-only'); END""")

    def ingest(self, records, *, provider, observed_at, source_url=None,
               source_sha256, source_ref, issue_counts=None, _publish=True):
        """Atomically import one proven source; exact retries preserve its first receipt.

        Original quote timestamps may be missing/invalid; such data is archived
        with an explicit status, never silently labelled a current market quote.
        A source reference denotes an existing raw/monitor/archive record; the
        offline job is responsible for verifying that exact parent evidence.
        """
        if provider not in PROVIDERS or not isinstance(source_ref, str) or not source_ref.strip():
            raise ValueError("PROVIDER_AND_SOURCE_REFERENCE_REQUIRED")
        if type(_publish) is not bool:
            raise ValueError("INVALID_PUBLICATION_FLAG")
        if not isinstance(source_sha256, str) or not re.fullmatch("[0-9a-f]{64}", source_sha256):
            raise ValueError("SOURCE_SHA256_REQUIRED")
        observed, url = stamp(observed_at), _public_url(source_url)
        counts_meta = json.loads(encoded({} if issue_counts is None else issue_counts))
        if not isinstance(counts_meta, dict) or any(type(v) is not int or v < 0 for v in counts_meta.values()):
            raise ValueError("INVALID_ISSUE_COUNTS")
        source = {"provider": provider, "observed_at": observed, "source_url": url,
                  "source_sha256": source_sha256, "source_ref": source_ref}
        source_id = digest(source)
        counts = Counter(sources_created=0, products_created=0, quotes_created=0)
        with self.database.transaction(immediate=True) as db:
            now = self._now(db)
            if now < utc(observed):
                raise ValueError("FUTURE_SOURCE_RECEIPT")
            counts["sources_created"] += db.execute(
                "INSERT OR IGNORE INTO sports_odds_sources VALUES (?,?,?,?,?,?,?,?)",
                (source_id, provider, source_sha256, url, source_ref, observed,
                 stamp(now), encoded(counts_meta))).rowcount
            sealed = db.execute("SELECT 1 FROM sports_odds_publications WHERE source_id=?", (source_id,)).fetchone()
            for raw in records:
                record = json.loads(encoded(raw))
                if (not isinstance(record, dict) or not (PARENT | VALUE | IDENTITY) <= set(record)
                        or any(not isinstance(record.get(key), str) or not record[key].strip()
                               for key in IDENTITY)):
                    raise ValueError("COMPLETE_NATIVE_QUOTE_IDENTITY_REQUIRED")
                if (record["provider"] != provider or stamp(record["observed_at"]) != observed
                        or record["source_url"] != url or record["source_sha256"] != source_sha256):
                    raise ValueError("QUOTE_PARENT_PROVENANCE_MISMATCH")
                decimal = price(record["decimal_price"])
                status, quoted = record["quote_time_status"], record["quoted_at"]
                if status not in {"KNOWN", "MISSING", "INVALID", "FUTURE"}:
                    raise ValueError("INVALID_QUOTE_TIME_STATUS")
                if quoted is not None:
                    quoted = stamp(quoted)
                if (status == "KNOWN" and (quoted is None or quoted > observed)
                        or status != "KNOWN" and quoted is not None):
                    raise ValueError("QUOTE_TIME_STATUS_CONFLICT")
                if record["price_kind"] not in {"CURRENT", "OPENING", "LAST_SEEN", "PRE_CLOSING", "CLOSING"}:
                    raise ValueError("UNKNOWN_PRICE_KIND")
                if not isinstance(record["raw_path"], str) or not isinstance(record["raw"], dict):
                    raise ValueError("RAW_QUOTE_PROVENANCE_REQUIRED")  # noqa: TRY004 - uniform archive rejection
                product = {key: value for key, value in record.items() if key not in PARENT | VALUE}
                line = product.get("line")
                if line is not None:
                    try:
                        number = Decimal(line) if isinstance(line, str) and len(line) <= 100 else None
                        if (number is None or not number.is_finite()
                                or not -100 <= number.as_tuple().exponent <= 100 or number.adjusted() > 99):
                            raise ValueError
                    except (InvalidOperation, ValueError):
                        raise ValueError("EXACT_DECIMAL_LINE_REQUIRED") from None
                    text = format(number, "f")
                    if len(text) > 100:
                        raise ValueError("EXACT_DECIMAL_LINE_REQUIRED")
                    product["line"] = (text.rstrip("0").rstrip(".") if "." in text else text) if number else "0"
                product_id = digest([provider, product])
                counts["products_created"] += db.execute("INSERT OR IGNORE INTO sports_odds_products VALUES (?,?,?,?,?,?,?)",
                    (product_id, provider, record["fixture_id"], record["bookmaker"],
                     record["market_key"], record["selection_key"], encoded(product))).rowcount
                value = {key: record[key] for key in VALUE}
                value.update(decimal_price=decimal, quoted_at=quoted)
                quote_id = digest([source_id, product_id, value])
                if sealed and not db.execute("SELECT 1 FROM sports_odds_quotes WHERE quote_id=?", (quote_id,)).fetchone():
                    raise ValueError("SEALED_SOURCE_NEW_QUOTE")
                counts["quotes_created"] += db.execute("INSERT OR IGNORE INTO sports_odds_quotes VALUES (?,?,?,?,?,?,?,?,?)",
                    (quote_id, source_id, product_id, decimal, record["price_kind"], quoted,
                     status, record["raw_path"], encoded(record["raw"]))).rowcount
        if _publish:
            # Data transaction has committed before the availability witness.
            self.seal(source_id)
        return {"source_id": source_id, **counts}

    def _now(self, db):
        now = utc(self.clock())
        for query in ("SELECT max(ingested_at) FROM sports_odds_sources",
                      "SELECT max(sealed_at) FROM sports_odds_publications"):
            bound = db.execute(query).fetchone()[0]
            if bound is not None:
                self._last = max(self._last or utc(bound), utc(bound))
        if self._last is not None and now < self._last:
            raise ValueError("CLOCK_MOVED_BACKWARDS")
        self._last = now
        return now

    def seal(self, source_id):
        """Witness already committed quotes; only a whole validated import may seal.

        Bulk jobs stage short atomic chunks, hidden from public reads. This
        separate transaction witnesses their committed presence after the last
        chunk. Sealed source contents cannot be extended retrospectively.
        """
        # Reserve the writer before establishing the publication read snapshot.
        # A deferred snapshot cannot upgrade after another WAL writer commits.
        with self.database.transaction(immediate=True) as db:
            source = db.execute("SELECT source_id FROM sports_odds_sources WHERE source_id=?", (source_id,)).fetchone()
            if source is None:
                raise ValueError("UNKNOWN_SOURCE_TO_SEAL")
            existing = db.execute("SELECT * FROM sports_odds_publications WHERE source_id=?", (source_id,)).fetchone()
            count = db.execute("SELECT count(*) FROM sports_odds_quotes WHERE source_id=?", (source_id,)).fetchone()[0]
            now = self._now(db)
            if existing is not None:
                if existing["quote_count"] != count:
                    raise ValueError("SEALED_SOURCE_QUOTE_COUNT_CHANGED")
                return dict(existing)
            db.execute("INSERT INTO sports_odds_publications VALUES (?,?,?)", (source_id, stamp(now), count))
        return {"source_id": source_id, "sealed_at": stamp(now), "quote_count": count}

    def quotes(self, *, as_of, provider=None, fixture_id=None, product_id=None, limit=1000):
        """Bounded archival view, including explicitly unusable quote timestamps.

        This is not a latest-price resolver: concurrent contradictory quotes
        remain separate rows. Callers must not infer comparability from ordering.
        """
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("INVALID_QUOTE_LIMIT")
        cutoff = stamp(as_of)
        query = """SELECT s.*,p.product_json,q.*,v.sealed_at FROM sports_odds_quotes q
            JOIN sports_odds_sources s USING(source_id) JOIN sports_odds_products p USING(product_id)
            JOIN sports_odds_publications v USING(source_id)
            WHERE s.observed_at<=? AND s.ingested_at<=? AND v.sealed_at<=?"""
        args = [cutoff, cutoff, cutoff]
        for key, value in (("s.provider", provider), ("p.fixture_id", fixture_id), ("q.product_id", product_id)):
            if value is not None:
                query += f" AND {key}=?"
                args.append(value)
        query += " ORDER BY s.observed_at,q.quoted_at,q.quote_id LIMIT ?"
        args.append(limit)
        with closing(self.database.connect()) as db:
            rows = db.execute(query, args).fetchall()
        return [{**json.loads(row["product_json"]), **{key: row[key] for key in (
            "quote_id", "product_id", "source_id", "provider", "source_sha256", "source_url", "source_ref",
            "observed_at", "ingested_at", "sealed_at", "decimal_price", "price_kind", "quoted_at", "quote_time_status", "raw_path")},
            "raw": json.loads(row["raw_json"])} for row in rows]

    def summary(self):
        with closing(self.database.connect()) as db:
            staged = db.execute("""SELECT count(*) FROM sports_odds_sources s WHERE NOT EXISTS
                (SELECT 1 FROM sports_odds_publications p WHERE p.source_id=s.source_id)""").fetchone()[0]
            counts = {"sports_odds_sources": db.execute("SELECT count(*) FROM sports_odds_publications").fetchone()[0],
                "sports_odds_products": db.execute("SELECT count(*) FROM sports_odds_products" if not staged else
                    "SELECT count(DISTINCT product_id) FROM sports_odds_quotes JOIN sports_odds_publications USING(source_id)").fetchone()[0],
                "sports_odds_quotes": db.execute("SELECT coalesce(sum(quote_count),0) FROM sports_odds_publications").fetchone()[0]}
            times = [dict(row) for row in db.execute("""SELECT quote_time_status,count(*) AS count
                FROM sports_odds_quotes JOIN sports_odds_publications USING(source_id)
                GROUP BY quote_time_status ORDER BY quote_time_status""")]
        return {"mode": "SHADOW", **counts, "staged_sources": staged, "quote_time_status": times}
