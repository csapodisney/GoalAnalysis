"""Opt-in, bounded historical results import; no scheduler or ticket changes.

Provider IDs must be supplied explicitly. Raw list responses retain every field;
xG/odds availability flags are not mistaken for downloaded xG/odds observations.
Historical availability is actual receipt time, never backdated to match time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from goal_analysis.providers.the_stats_api import (
    MAX_RESPONSE_BYTES,
    TheStatsApiClient,
    TheStatsApiError,
    _default_transport,
)
from goal_analysis.storage import Database, SnapshotStore
from goal_analysis.storage.sports_archive import SportsArchive

from .history_quota import latest_quota, quota_block

VERSION = "thestatsapi-history-v1"
PROVIDER = "the_stats_api"
ENDPOINT = "/football/matches"
TERMS_URL = "https://www.thestatsapi.com/terms"
REQUEST_TIMEOUT_SECONDS = 20
CHECKPOINT_RESERVE_SECONDS = 5


def _bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _stamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return stamp.astimezone(UTC)


def _id(value, prefix):
    return isinstance(value, str) and re.fullmatch(prefix + r"_\d{1,30}", value) is not None


def prepare_plan(competition_ids, *, as_of=None, years=5, date_from=None, date_to=None,
                 season_ids=None):
    """Freeze five years by default, newest first, alternating competitions.

    season_ids optionally maps a comp_ ID to an sn_ ID from this provider. No
    numeric API-Football identifiers or team-name matching are accepted.
    Today's data belongs to daily collection; backfill ends with yesterday.
    Older explicitly saved ten-year plans remain readable for audit/recovery.
    """
    observed = _stamp(as_of or datetime.now(UTC))
    competitions = list(dict.fromkeys(competition_ids))
    seasons = dict(season_ids or {})
    if (not 1 <= len(competitions) <= 50 or any(not _id(x, "comp") for x in competitions)
            or type(years) is not int or not 1 <= years <= 10
            or set(seasons) - set(competitions) or any(not _id(x, "sn") for x in seasons.values())):
        raise ValueError("EXPLICIT_PROVIDER_IDS_AND_BOUNDED_YEARS_REQUIRED")
    end = date.fromisoformat(date_to) if date_to else observed.date() - timedelta(days=1)
    try:
        earliest = observed.date().replace(year=observed.year - years)
    except ValueError:  # Leap-day start uses the last valid day of that February.
        earliest = observed.date().replace(year=observed.year - years, day=28)
    start = date.fromisoformat(date_from) if date_from else earliest
    if not earliest <= start <= end < observed.date():
        raise ValueError("HISTORICAL_WINDOW_MUST_BE_WITHIN_PAST_TEN_YEARS")
    windows, cursor = [], end
    while cursor >= start:
        lower = max(start, cursor.replace(day=1))
        windows.append((lower, cursor))
        cursor = lower - timedelta(days=1)
    queries = []
    for lower, upper in windows:
        for competition in competitions:
            query = {"competition_id": competition, "status": "finished", "sort": "-utc_date",
                     "date_from": lower.isoformat(), "date_to": upper.isoformat(),
                     "utc_offset": "+00:00"}
            if competition in seasons:
                query["season_id"] = seasons[competition]
            queries.append(query)
    plan = {"version": VERSION, "provider": PROVIDER, "as_of": observed.isoformat(),
            "date_from": start.isoformat(), "date_to": end.isoformat(),
            "competition_ids": competitions, "season_ids": seasons, "years": years,
            "per_page": 100, "queries": queries,
            "purpose": "INTERNAL_MATCH_ANALYSIS",
            "rights": {"terms_url": TERMS_URL, "active_subscription_required": True,
                       "post_subscription_use": "NOT_AUTHORIZED_BY_REVIEWED_TERMS",
                       "raw_data_resale": "PROHIBITED",
                       "retention_scope": "REASONABLY_NECESSARY_APPLICATION_DATA_ONLY"}}
    return {**plan, "plan_sha256": _hash(plan)}


def _validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("INVALID_PLAN")  # noqa: TRY004 - uniform external plan rejection
    expected = prepare_plan(plan.get("competition_ids", []), as_of=plan.get("as_of"),
                            years=plan.get("years"), date_from=plan.get("date_from"),
                            date_to=plan.get("date_to"), season_ids=plan.get("season_ids"))
    if plan != expected:
        raise ValueError("PLAN_CHANGED_OR_DAMAGED")


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    envelope = {"data": value, "sha256": _hash(value)}
    with temporary.open("wb") as stream:
        stream.write(_bytes(envelope))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read(path):
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError("CHECKPOINT_TOO_LARGE")
    envelope = json.loads(path.read_bytes())
    if not isinstance(envelope, dict) or _hash(envelope.get("data")) != envelope.get("sha256"):
        raise ValueError("CHECKPOINT_DAMAGED")
    return envelope["data"]


def save_plan(path, plan):
    _validate_plan(plan)
    path = Path(path)
    if path.exists() and load_plan(path) != plan:
        raise ValueError("EXISTING_PLAN_MUST_NOT_CHANGE")
    _atomic(path, plan)


def load_plan(path):
    plan = _read(path)
    _validate_plan(plan)
    return plan


@contextmanager
def _lock(path):
    """An OS lock is released on process death; the empty lockfile can remain."""
    with Path(path).open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("HISTORY_IMPORT_ALREADY_RUNNING") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class _CaptureTransport:
    def __init__(self, transport):
        self.transport, self.raw = transport, None

    def __call__(self, url, headers, *, timeout):
        self.raw = None
        body, response_headers, status = self.transport(url, headers, timeout=timeout)
        self.raw = (json.dumps(body, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":")).encode("utf-8")
                    if isinstance(body, dict) else body)
        return body, response_headers, status


def _page(payload, page, per_page):
    meta, rows = payload.get("meta"), payload.get("data")
    if (not isinstance(meta, dict) or not isinstance(rows, list)
            or type(meta.get("page")) is not int or meta["page"] != page
            or type(meta.get("per_page")) is not int or meta["per_page"] != per_page
            or type(meta.get("total_pages")) is not int or not 0 <= meta["total_pages"] <= 100_000
            or len(rows) > per_page or (not rows and meta["total_pages"] > 0)
            or (rows and page > meta["total_pages"])):
        raise ValueError("INVALID_PAGINATION_RESPONSE")
    return rows, meta["total_pages"]


def _record(row, query, as_of):
    if not isinstance(row, dict):
        raise ValueError("INVALID_MATCH")  # noqa: TRY004 - recorded row rejection
    if (not _id(row.get("id"), "mt") or not _id(row.get("competition_id"), "comp")
            or not _id(row.get("season_id"), "sn")):
        raise ValueError("INVALID_PROVIDER_ID")
    if (row["competition_id"] != query["competition_id"]
            or query.get("season_id", row["season_id"]) != row["season_id"]):
        raise ValueError("WRONG_COMPETITION_OR_SEASON")
    if row.get("status") != "finished":
        raise ValueError("MATCH_NOT_FINISHED")
    try:
        kickoff = _stamp(row.get("utc_date"))
    except (ValueError, TypeError):
        raise ValueError("INVALID_KICKOFF") from None
    if (not query["date_from"] <= kickoff.date().isoformat() <= query["date_to"]
            or kickoff + timedelta(minutes=90) > as_of):
        raise ValueError("MATCH_OUTSIDE_FROZEN_WINDOW")
    names = []
    for side in ("home_team", "away_team"):
        team = row.get(side)
        if (not isinstance(team, dict) or not _id(team.get("id"), "tm")
                or not isinstance(team.get("name"), str) or not team["name"].strip()):
            raise ValueError("INVALID_TEAM_IDENTITY")
        names.append(team["name"])
    if row["home_team"]["id"] == row["away_team"]["id"]:
        raise ValueError("DUPLICATE_TEAM_IDENTITY")
    score = row.get("score") or {}
    regulation = score.get("regulation") if isinstance(score, dict) else None
    # Never fall back to final_score/after_extra_time/shootout totals.
    home = regulation.get("home") if isinstance(regulation, dict) else None
    away = regulation.get("away") if isinstance(regulation, dict) else None
    if any(type(value) is not int or not 0 <= value <= 100 for value in (home, away)):
        home = away = None
    return {"provider": PROVIDER, "source_record_key": row["id"],
            "league_code": row["competition_id"], "season_code": row["season_id"],
            "match_date": kickoff.date().isoformat(), "home_team": names[0], "away_team": names[1],
            "full_time_home": home, "full_time_away": away,
            "full_time_result": ("H" if home > away else "A" if away > home else "D")
                if home is not None and away is not None else None,
            "kickoff_local_time": None, "raw": row, "odds_snapshot_types": {}}


def _ingest(store, rows, query, plan, source):
    grouped, rejections, seen, conflicts = defaultdict(list), Counter(), {}, set()
    for row in rows:
        try:
            record = _record(row, query, _stamp(plan["as_of"]))
        except ValueError as error:
            rejections[str(error)] += 1
            continue
        key = record["source_record_key"]
        if key in seen and seen[key] != record:
            conflicts.add(key)
        seen[key] = record
    for key, record in seen.items():
        if key in conflicts:
            rejections["CONFLICTING_MATCH_IN_PAGE"] += 1
        else:
            grouped[(record["league_code"], record["season_code"])].append(record)
    counts = Counter()
    for (league, season), records in grouped.items():
        result = store.ingest({"provider": PROVIDER, "league_code": league,
            "season_code": season, "records": records,
            "columns": sorted({key for record in records for key in record["raw"]})},
            source_url=source["source_url"], fetched_at=_stamp(source["observed_at"]),
            content_hash=source["content_hash"], metadata={
                "plan_sha256": plan["plan_sha256"], "query": query,
                "raw_response_sha256": source["content_hash"],
                "availability_time_policy": "ACTUAL_RECEIPT_NOT_HISTORICAL_PUBLICATION",
                "historical_published_at": None, "cross_provider_identity": "UNMAPPED",
                "odds_policy": "FLAGS_ONLY_NO_EXECUTABLE_OR_TIMESTAMPED_ODDS_IMPORTED",
                "xg_policy": "FLAGS_ONLY_NO_XG_STATS_ENDPOINT_REQUESTED",
                "rights": plan["rights"]})
        for key in ("matches_created", "observations_created", "imports_created"):
            counts[key] += result[key]
    return {**counts, "accepted_rows": sum(map(len, grouped.values())),
            "rejected_rows": sum(rejections.values()), "rejection_counts": dict(rejections)}


def run_history(root, plan, *, execute=False, daily_work_complete=False, max_requests=20,
                api_key=None, transport=None, clock=lambda: datetime.now(UTC),
                monthly_reserve=100, stop_at=None, unified_results=False, max_pages=20,
                quota_observer=None, root_lock=None):
    """One bounded batch, resuming immutable cached pages before any new request.

    Callers must confirm the daily jobs are finished before each batch. Live
    calls require execute=True; this function never changes activation settings.
    Quota headers can end a batch early. 403/429 and all other errors stop once.
    An optional aware deadline stops before a request that cannot leave time
    for its network timeout and durable page checkpoint.
    """
    _validate_plan(plan)
    if type(unified_results) is not bool:
        raise ValueError("BOOLEAN_UNIFIED_RESULTS_FLAG_REQUIRED")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("INVALID_HISTORY_PAGE_BUDGET")
    if (type(max_requests) is not int or not 0 <= max_requests <= 20
            or type(monthly_reserve) is not int or not 0 <= monthly_reserve <= 100_000):
        raise ValueError("INVALID_HISTORY_REQUEST_BUDGET")
    now = _stamp(clock())
    deadline = _stamp(stop_at) if stop_at is not None else None
    if _stamp(plan["as_of"]) > now:
        raise ValueError("PLAN_AS_OF_IN_FUTURE")
    summary = {"version": VERSION, "plan_sha256": plan["plan_sha256"], "requests": 0,
               "cached_pages": 0, "processed_pages": 0, "status": "PLAN_ONLY",
               "query_count": len(plan["queries"]), "automatic_model_changes": False}
    if not execute or not daily_work_complete:
        summary["status"] = "PLAN_ONLY" if not execute else "DAILY_WORK_HAS_PRIORITY"
        return summary
    if deadline is not None and now >= deadline:
        summary["status"] = "TIME_WINDOW_EXHAUSTED"
        return summary
    last_seen = now

    def checked_clock():
        nonlocal last_seen
        current = _stamp(clock())
        if current < last_seen:
            raise ValueError("HISTORY_CLOCK_MOVED_BACKWARDS")
        last_seen = current
        return current

    root = Path(root)
    work = root / "data/arthur/the-stats-history" / plan["plan_sha256"]
    work.mkdir(parents=True, exist_ok=True)
    with _lock(work / "batch.lock"):
        return _run(root, work, plan, summary, max_requests, api_key, transport, checked_clock,
                    monthly_reserve, deadline, unified_results, max_pages, quota_observer, root_lock)


def _run(root, work, plan, summary, cap, api_key, transport, clock, reserve, deadline,
         unified_results=False, max_pages=20, quota_observer=None, root_lock=None):
    database = Database(root / "data/goal_analysis.sqlite3")
    database.initialize()
    snapshots = SnapshotStore(database, root / "data/raw")
    archive = SportsArchive(database)
    archive.initialize()
    sink = None
    checkpoint_path = work / "checkpoint.json"
    cursor = _read(checkpoint_path) if checkpoint_path.exists() else {
        "plan_sha256": plan["plan_sha256"], "query_index": 0, "page": 1, "total_pages": None}
    if (cursor.get("plan_sha256") != plan["plan_sha256"]
            or type(cursor.get("query_index")) is not int
            or not 0 <= cursor["query_index"] <= len(plan["queries"])
            or type(cursor.get("page")) is not int or not 1 <= cursor["page"] <= 100_000):
        raise ValueError("CHECKPOINT_DAMAGED")
    capture = _CaptureTransport(transport or _default_transport)
    from goal_analysis.storage.provider_quota import observer_for

    client = TheStatsApiClient(api_key=api_key, transport=capture, max_requests=max(1, cap),
                              timeout=REQUEST_TIMEOUT_SECONDS, clock=clock,
                              quota_observer=quota_observer or observer_for(root,kind='history',clock=clock))
    quota_observer = client.quota_observer
    quota_path = work / "quota-state.json"
    prior = [quota_path, work / "last-batch.json"]
    quota = latest_quota([*(_read(p) for p in prior if p.exists()),
                         quota_observer.legacy_state() if quota_observer is not None else None],
                        now=_stamp(clock()), reserve=reserve)

    def remember_quota():
        nonlocal quota
        response = client.last_response
        if response is not None and response["authenticated"]:
            quota = {"provider": PROVIDER, "observed_at": response["observed_at"],
                     "usage": asdict(client.last_usage), "http_status": response["http_status"]}
            previous = _read(quota_path) if quota_path.exists() else None
            quota = latest_quota([previous, quota], now=_stamp(clock()), reserve=reserve)
            if quota is not None:
                _atomic(quota_path, quota)

    pages = []
    while cursor["query_index"] < len(plan["queries"]):
        if deadline is not None and deadline - _stamp(clock()) <= timedelta(
            seconds=CHECKPOINT_RESERVE_SECONDS
        ):
            summary["status"] = "TIME_WINDOW_EXHAUSTED"
            break
        query = plan["queries"][cursor["query_index"]]
        cache_path = work / f"page-{cursor['query_index']:05d}-{cursor['page']:06d}.json"
        params = {**query, "page": cursor["page"], "per_page": plan["per_page"]}
        cached = cache_path.exists()
        if cached:
            source = _read(cache_path)
            if (source.get("params") != params or source.get("plan_sha256") != plan["plan_sha256"]
                    or _stamp(source.get("observed_at")) > _stamp(clock())):
                raise ValueError("CACHED_PAGE_PROVENANCE_MISMATCH")
            raw = snapshots.read(source["content_hash"])
            if len(raw) > MAX_RESPONSE_BYTES or hashlib.sha256(raw).hexdigest() != source["content_hash"]:
                raise ValueError("CACHED_PAGE_HASH_MISMATCH")
            payload = json.loads(raw)
            summary["cached_pages"] += 1
        else:
            if deadline is not None and deadline - _stamp(clock()) <= timedelta(
                seconds=REQUEST_TIMEOUT_SECONDS + CHECKPOINT_RESERVE_SECONDS
            ):
                summary["status"] = "TIME_WINDOW_EXHAUSTED"
                break
            if client.request_count >= cap:
                summary["status"] = "REQUEST_LIMIT"
                break
            blocked = quota_block(quota, now=_stamp(clock()), reserve=reserve)
            if blocked:
                summary["status"] = blocked
                break
            try:
                client.last_response = None
                payload = client.request(ENDPOINT, params)
            except TheStatsApiError as error:
                remember_quota()
                summary.update(status="PROVIDER_STOPPED", error_code=error.code,
                               http_status=error.status, usage=asdict(error.usage))
                break
            remember_quota()
            raw = capture.raw
            response = client.last_response
            if (not isinstance(raw, bytes)
                    or hashlib.sha256(raw).hexdigest() != response["payload_sha256"]):
                raise ValueError("RESPONSE_PROVENANCE_MISMATCH")
            saved = snapshots.save(PROVIDER, "historical_matches", raw, "application/json",
                                   _stamp(response["observed_at"]), response["source_url"])
            source = {"plan_sha256": plan["plan_sha256"], "params": params,
                      "content_hash": saved.content_hash, "source_url": response["source_url"],
                      "observed_at": response["observed_at"]}
            # Durable page receipt precedes normalization/checkpoint: retrying a
            # crash here reuses the same bytes and observation time without API.
            _atomic(cache_path, source)
        try:
            rows, total = _page(payload, cursor["page"], plan["per_page"])
            if cursor["total_pages"] is not None and cursor["total_pages"] != total:
                raise ValueError("PAGINATION_CHANGED_RESTART_WINDOW_REQUIRED")
        except ValueError as error:
            summary.update(status="INVALID_PAGE", error_code=str(error))
            break
        availability = None
        candidate = None
        if unified_results and root_lock is not None:
            from .historical_page_availability import cached_page_status, preflight_page

            candidate = preflight_page(root, source, plan, root_lock, clock=clock)
            availability = (cached_page_status(root, candidate, root_lock, clock=clock) if cached
                            else {'status': candidate['status']})
        if unified_results and sink is None and rows:
            from goal_analysis.jobs.historical_result_sink import HistoricalResultSink

            sink = HistoricalResultSink(archive, clock=clock)
            archive = sink
        counts = _ingest(archive, rows, query, plan, source)
        if candidate is not None and not cached and candidate['status'] == 'EXACT_PAGE_PREFLIGHT':
            from .historical_page_availability import admit_page

            availability = admit_page(root, candidate, root_lock, clock=clock)
        if availability is not None:
            counts['availability'] = availability
        pages.append({"query_index": cursor["query_index"], "page": cursor["page"],
                      "content_hash": source["content_hash"], **counts})
        if cursor["page"] >= total:
            cursor.update(query_index=cursor["query_index"] + 1, page=1, total_pages=None)
        else:
            cursor.update(page=cursor["page"] + 1, total_pages=total)
        _atomic(checkpoint_path, cursor)
        summary["processed_pages"] += 1
        # Bound cached replays too, so a batch cannot hold up daily collection.
        if summary["processed_pages"] >= max_pages:
            summary["status"] = "PAGE_BATCH_LIMIT"
            break
    if cursor["query_index"] == len(plan["queries"]):
        summary["status"] = "COMPLETE"
    summary.update(requests=client.request_count, usage=asdict(client.last_usage),
                   cursor=cursor, pages=pages, observed_at=_stamp(clock()).isoformat(),
                   authenticated_quota=quota)
    if sink is not None:
        summary["unified_results"] = sink.summary()
    _atomic(work / "last-batch.json", summary)
    return summary
