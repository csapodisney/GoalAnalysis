"""Append-only SHADOW results, with reversible, evidenced cross-source links.

Native fixture identities never move. A match view resolves equivalence links at
the requested cutoff; its representative is not a permanent replacement ID.
Legacy archives and the betting history path are deliberately not modified.
"""
import json
import re
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from urllib.parse import parse_qs, urlsplit

from goal_analysis.normalization.canonical import canonical_id, utc
from goal_analysis.normalization.identity import exact_name

from .canonical_registry import digest, encoded

PROVIDERS = ("the_stats_api", "api_football", "football_data_co_uk")
SCOPES = {"REGULATION", "SOURCE_FULL_TIME_UNVERIFIED"}
FIELDS = {"provider", "provider_id", "competition_id", "season_id", "home_id", "away_id",
          "home_team", "away_team", "kickoff", "match_date", "kickoff_local_time", "status",
          "score_scope", "home_goals", "away_goals", "half_home_goals", "half_away_goals",
          "observed_at", "source_url", "source_sha256", "raw"}
TABLES = ("historical_result_matches", "historical_result_versions", "historical_result_links")


def stamp(value):
    return utc(value).isoformat(timespec="microseconds")


def proof_record(proof, *, now):
    """Freeze evidence; it cannot carry credentials or a future receipt."""
    result = json.loads(encoded(proof))
    if set(result) != {"source_url", "source_sha256", "observed_at"}:
        raise ValueError("EXACT_SOURCE_PROOF_REQUIRED")
    url = urlsplit(result["source_url"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment
            or any(re.search(r"key|token|secret|password|authorization", key, re.IGNORECASE)
                   for key in parse_qs(url.query))):
        raise ValueError("CREDENTIAL_FREE_HTTPS_SOURCE_REQUIRED")
    if not re.fullmatch(r"[0-9a-f]{64}", str(result["source_sha256"])):
        raise ValueError("SHA256_REQUIRED")
    result["observed_at"] = stamp(result["observed_at"])
    if utc(result["observed_at"]) > utc(now):
        raise ValueError("FUTURE_SOURCE_PROOF")
    return result


def validate(record, *, now):
    item = json.loads(encoded(record))
    if set(item) != FIELDS or item["provider"] not in PROVIDERS:
        raise ValueError("INVALID_RESULT_CONTRACT")
    for key in ("provider_id", "competition_id", "season_id", "home_team", "away_team", "status"):
        if not isinstance(item[key], str) or not item[key].strip():
            raise ValueError("NONEMPTY_RESULT_IDENTITY_REQUIRED")
    for key in ("home_id", "away_id"):
        if item[key] is not None and (not isinstance(item[key], str) or not item[key].strip()):
            raise ValueError("INVALID_TEAM_ID")
    if (item["home_id"] is not None and item["home_id"] == item["away_id"]
            or (item["home_id"] is None or item["away_id"] is None)
            and exact_name(item["home_team"]) == exact_name(item["away_team"])):
        raise ValueError("DISTINCT_TEAMS_REQUIRED")
    if item["status"] not in {"FT", "AET", "PEN", "finished", "FINISHED"}:
        raise ValueError("NOT_FINISHED")
    if item["score_scope"] not in SCOPES or not isinstance(item["raw"], dict):
        raise ValueError("INVALID_RESULT_SCOPE")
    for key in ("home_goals", "away_goals", "half_home_goals", "half_away_goals"):
        if item[key] is not None and (type(item[key]) is not int or item[key] < 0):
            raise ValueError("INVALID_SCORE")
    if (item["home_goals"] is None) != (item["away_goals"] is None):
        raise ValueError("SCORE_PAIR_REQUIRED")
    for side in ("home", "away"):
        half, full = item[f"half_{side}_goals"], item[f"{side}_goals"]
        if half is not None and full is not None and half > full:
            raise ValueError("HALF_SCORE_EXCEEDS_FULL_SCORE")
    match_date = date.fromisoformat(item["match_date"])
    if match_date.isoformat() != item["match_date"]:
        raise ValueError("CANONICAL_DATE_REQUIRED")
    source = proof_record({key: item[key] for key in ("source_url", "source_sha256", "observed_at")}, now=now)
    item.update(source)
    if item["kickoff"] is not None:
        item["kickoff"] = stamp(item["kickoff"])
        kickoff = utc(item["kickoff"])
        if kickoff >= utc(item["observed_at"]) or kickoff.date() != match_date:
            raise ValueError("INVALID_FINISHED_KICKOFF")
        if item["kickoff_local_time"] is not None:
            raise ValueError("UNKNOWN_LOCAL_TIME_MUST_NOT_ACCOMPANY_UTC")
    elif match_date > utc(item["observed_at"]).date():
        raise ValueError("FUTURE_UNKNOWN_MATCH_DATE")
    local = item["kickoff_local_time"]
    if local is not None and (not isinstance(local, str) or time.fromisoformat(local).tzinfo is not None):
        raise ValueError("NAIVE_LOCAL_TIME_REQUIRED")
    return item


def identity(record):
    # Scores/receipts/raw data do not define an event. A corrected identity
    # invalidates old exact-match edges until that identity has fresh evidence.
    return digest({key: record[key] for key in (
        "provider", "provider_id", "competition_id", "home_id", "away_id",
        "home_team", "away_team", "kickoff", "match_date", "kickoff_local_time")})


def decision_content(record):
    return {key: value for key, value in record.items()
            if key not in {"raw", "source_url", "source_sha256", "observed_at"}}


def event_key(record):
    if not record["kickoff"]:
        return None
    return (record["provider"], record["competition_id"], record["kickoff"],
            exact_name(record["home_team"]), exact_name(record["away_team"]))


def identity_claims(records, conflicts):
    """An unusable score is still evidence of a second native event identity."""
    claims = defaultdict(set)
    for row in records.values():
        claims[event_key(row["record"])].add(row["match_id"])
    for versions in conflicts.values():
        for row in versions:
            claims[event_key(row["record"])].add(row["match_id"])
    return claims


def unique_exact(record, claims):
    key = event_key(record)
    return bool(key and key[-1] != key[-2] and len(claims[key]) == 1)


def _groups(records, links, conflicts=None, *, claims=None):
    claims = identity_claims(records, conflicts or {}) if claims is None else claims
    parent = {key: key for key in records}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for link in links:
        left, right = link["left_id"], link["right_id"]
        if (left not in records or right not in records
                or records[left]["identity_sha256"] != link["left_identity"]
                or records[right]["identity_sha256"] != link["right_identity"]):
            continue
        if link["method"] == "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE" and not all(
                unique_exact(records[key]["record"], claims) for key in (left, right)):
            continue
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups = defaultdict(list)
    for key in records:
        groups[find(key)].append(key)
    for members in groups.values():
        providers = [records[key]["record"]["provider"] for key in members]
        if len(set(providers)) != len(providers):
            raise ValueError("AMBIGUOUS_TRANSITIVE_PROVIDER_IDENTITY")
    return groups


class HistoricalResults:
    def __init__(self, database, *, clock=None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def initialize(self):
        with self.database.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS historical_result_matches (
                match_id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
                UNIQUE(provider,provider_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS historical_result_versions (
                version_id TEXT PRIMARY KEY,
                match_id TEXT NOT NULL REFERENCES historical_result_matches(match_id),
                identity_sha256 TEXT NOT NULL, record_json TEXT NOT NULL,
                observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS historical_result_links (
                event_id TEXT PRIMARY KEY, link_id TEXT NOT NULL, action TEXT NOT NULL,
                left_id TEXT NOT NULL REFERENCES historical_result_matches(match_id),
                right_id TEXT NOT NULL REFERENCES historical_result_matches(match_id),
                left_identity TEXT NOT NULL, right_identity TEXT NOT NULL,
                method TEXT NOT NULL, proof_json TEXT NOT NULL,
                observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL)""")
            db.execute("""CREATE INDEX IF NOT EXISTS historical_result_asof
                ON historical_result_versions(match_id, observed_at, ingested_at)""")
            db.execute("""CREATE INDEX IF NOT EXISTS historical_result_link_asof
                ON historical_result_links(link_id, observed_at, ingested_at)""")
            for table in TABLES:
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT,'historical results are append-only'); END""")

    def ingest(self, records):
        """One atomic batch. Replaying identical source receipts adds no rows."""
        now = utc(self.clock())
        counts = Counter(matches_created=0, versions_created=0)
        with self.database.transaction() as db:
            for raw in records:
                item = validate(raw, now=now)
                match_id = canonical_id("fixture", item["provider"] + ":" + item["provider_id"])
                version_id = digest(item)
                counts["matches_created"] += db.execute(
                    "INSERT OR IGNORE INTO historical_result_matches VALUES (?,?,?)",
                    (match_id, item["provider"], item["provider_id"])).rowcount
                counts["versions_created"] += db.execute(
                    "INSERT OR IGNORE INTO historical_result_versions VALUES (?,?,?,?,?,?)",
                    (version_id, match_id, identity(item), encoded(item), item["observed_at"],
                     stamp(now))).rowcount
        return dict(counts)

    def _read(self, db, cutoff, *, known_at=None):
        known_at = known_at or cutoff
        rows = db.execute("""SELECT * FROM historical_result_versions
            WHERE observed_at<=? AND ingested_at<=?
            ORDER BY match_id,observed_at DESC,version_id""", (cutoff, known_at))
        latest, conflicts, times = {}, {}, {}
        for row in rows:
            key = row["match_id"]
            if key in times and row["observed_at"] != times[key]:
                continue
            times[key] = row["observed_at"]
            record = json.loads(row["record_json"])
            if digest(record) != row["version_id"] or identity(record) != row["identity_sha256"]:
                raise ValueError("CORRUPT_HISTORICAL_RESULT")
            value = {**dict(row), "record": record}
            value.pop("record_json")
            if key in latest and decision_content(latest[key]["record"]) != decision_content(record):
                conflicts.setdefault(key, [latest[key]])
                conflicts[key].append(value)
            else:
                latest.setdefault(key, value)
        for key in conflicts:
            latest.pop(key, None)
        events = db.execute("""SELECT * FROM historical_result_links
            WHERE observed_at<=? AND ingested_at<=?
            ORDER BY link_id,observed_at DESC,ingested_at DESC,event_id""", (cutoff, known_at))
        states = {}
        for event in events:
            states.setdefault(event["link_id"], dict(event))
        return latest, [v for v in states.values() if v["action"] == "ADD"], conflicts

    def view(self, *, as_of):
        cutoff = stamp(as_of)
        with self.database.connect() as db:
            latest, links, conflicts = self._read(db, cutoff)
        # A date-only row cannot reveal whether its unknown local event has
        # finished at this UTC instant, even when the provider claims FINISHED.
        latest = {key: row for key, row in latest.items() if row["record"]["kickoff"] is not None
                  or row["record"]["match_date"] < utc(cutoff).date().isoformat()}
        matches = []
        for representative, members in sorted(_groups(latest, links, conflicts).items()):
            records = sorted((latest[key] for key in members),
                             key=lambda item: (PROVIDERS.index(item["record"]["provider"]), item["match_id"]))
            resolutions = {}
            for scope in sorted({r["record"]["score_scope"] for r in records}):
                usable = [r for r in records if r["record"]["score_scope"] == scope
                          and r["record"]["home_goals"] is not None]
                values = {(r["record"]["home_goals"], r["record"]["away_goals"]) for r in usable}
                chosen = usable[0] if usable else None
                resolutions[scope] = {"status": "CONFLICT" if len(values) > 1 else
                    "AVAILABLE" if chosen else "MISSING", "selected_version_id": chosen["version_id"] if chosen else None,
                    "resolution_reason": "PRIMARY_SOURCE_PRIORITY" if chosen and chosen["record"]["provider"] == PROVIDERS[0]
                    else "FALLBACK_PRIMARY_UNAVAILABLE" if chosen else "NO_COMPLETE_SCORE",
                    "score": [chosen["record"]["home_goals"], chosen["record"]["away_goals"]] if chosen else None}
            matches.append({"representative_id": representative, "member_ids": sorted(members),
                "link_status": "VERIFIED_SAME_EVENT" if len(members) > 1 else "UNLINKED",
                "source_records": records, "score_resolutions": resolutions})
        return {"mode": "SHADOW", "effective_data_path": "LEGACY_UNCHANGED", "as_of": cutoff,
            "deduplication_scope": "NATIVE_IDS_AND_VERIFIED_LINKS_ONLY", "matches": matches,
            "same_time_conflicts": conflicts,
            "counts": {"native_matches": len(latest), "unified_matches": len(matches),
                "verified_multi_source_matches": sum(len(m["member_ids"]) > 1 for m in matches),
                "unlinked_matches": sum(len(m["member_ids"]) == 1 for m in matches),
                "same_time_conflicts": len(conflicts),
                "score_conflicts": sum(any(v["status"] == "CONFLICT" for v in m["score_resolutions"].values())
                                       for m in matches)}}

    def link(self, left_id, right_id, *, method, evidence, competition_pair=None,
             _connection=None, _state=None):
        """Append a current-identity link; no fuzzy names, dates-only, or rebinding.

        VERIFIED_PROVIDER_MAPPING is reserved for supplied authoritative ID maps.
        Exact matching additionally needs evidenced, explicit competition IDs.
        """
        now = utc(self.clock()) if _state is None else _state[1]
        proofs = [proof_record(p, now=now) for p in evidence]
        if not proofs or left_id == right_id or method not in {
                "VERIFIED_PROVIDER_MAPPING", "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE"}:
            raise ValueError("EXPLICIT_LINK_EVIDENCE_REQUIRED")
        observed = max(p["observed_at"] for p in proofs)
        boundary = self.database.transaction() if _connection is None else nullcontext(_connection)
        with boundary as db:
            current, links, conflicts = self._read(db, stamp(now)) if _state is None else _state[0]
            if any(key not in current or current[key]["observed_at"] > observed for key in (left_id, right_id)):
                raise ValueError("MISSING_LINK_ENDPOINT_AT_EVIDENCE_TIME")
            left, right = current[left_id], current[right_id]
            a, b = left["record"], right["record"]
            if a["provider"] == b["provider"]:
                raise ValueError("SAME_PROVIDER_LINK_FORBIDDEN")
            if a["kickoff"] and b["kickoff"] and a["kickoff"] != b["kickoff"]:
                raise ValueError("CONFLICTING_KICKOFF")
            if method == "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE":
                expected = sorted([[a["provider"], a["competition_id"]], [b["provider"], b["competition_id"]]])
                if (competition_pair != expected or not a["kickoff"] or a["kickoff"] != b["kickoff"]
                        or exact_name(a["home_team"]) == exact_name(a["away_team"])
                        or exact_name(a["home_team"]) != exact_name(b["home_team"])
                        or exact_name(a["away_team"]) != exact_name(b["away_team"])):
                    raise ValueError("EXACT_EVENT_AND_EXPLICIT_COMPETITION_REQUIRED")
            claims = identity_claims(current, conflicts) if _state is None else _state[2]
            if method == "EXACT_NAMES_KICKOFF_EXPLICIT_LEAGUE" and not all(
                    unique_exact(record, claims) for record in (a, b)):
                raise ValueError("AMBIGUOUS_EXACT_EVENT")
            endpoints = sorted([(left_id, left["identity_sha256"]), (right_id, right["identity_sha256"])])
            proof = {"sources": proofs, "competition_pair": competition_pair}
            link_id = digest([endpoints, method, proof])
            event = {"left_id": endpoints[0][0], "right_id": endpoints[1][0],
                "left_identity": endpoints[0][1], "right_identity": endpoints[1][1], "method": method}
            # A transitive component must never contain two different native IDs
            # of one provider, including through a third provider's records.
            _groups(current, [*links, event], conflicts, claims=claims)
            count = db.execute("INSERT OR IGNORE INTO historical_result_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (digest([link_id, "ADD"]), link_id, "ADD", event["left_id"], event["right_id"],
                 event["left_identity"], event["right_identity"], method, encoded(proof),
                 observed, stamp(now))).rowcount
            if count and _state is not None:
                links.append(event)
        return {"link_id": link_id, "links_created": count}

    def link_many(self, requests):
        """One atomic reconciliation, one read of the immutable source history."""
        now = utc(self.clock())
        with self.database.transaction() as db:
            read = self._read(db, stamp(now))
            state = (read, now, identity_claims(read[0], read[2]))
            return [self.link(**request, _connection=db, _state=state) for request in requests]

    def revoke(self, link_id, *, evidence):
        now = utc(self.clock())
        proof = proof_record(evidence, now=now)
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM historical_result_links WHERE link_id=? AND action='ADD'",
                             (link_id,)).fetchone()
            if row is None or proof["observed_at"] <= row["observed_at"]:
                raise ValueError("LATER_REVOCATION_EVIDENCE_REQUIRED")
            return db.execute("INSERT OR IGNORE INTO historical_result_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (digest([link_id, "REVOKE", proof]), link_id, "REVOKE", row["left_id"], row["right_id"],
                 row["left_identity"], row["right_identity"], row["method"], encoded(proof),
                 proof["observed_at"], stamp(now))).rowcount
