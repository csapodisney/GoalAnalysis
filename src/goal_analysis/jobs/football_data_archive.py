"""Polite, cached public season downloads; no ticket/model mutation or paid API."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.storage import CacheStore, Database, SnapshotStore

BASE = "https://football-data.co.uk/mmz4281"
NAMESPACE = "football_data_season_archives_v1"
MAX_ARCHIVE_BYTES = 32_000_000
MAX_EXPANDED_BYTES = 128_000_000


class DownloadFailure(Exception):
    def __init__(self, code, status=None, retry_after=None):
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


class _SameSiteRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {"football-data.co.uk", "www.football-data.co.uk"}:
            raise DownloadFailure("UNEXPECTED_REDIRECT")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Arthur-GoalAnalysis/3.11"})
    try:
        with urllib.request.build_opener(_SameSiteRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(raw) > MAX_ARCHIVE_BYTES:
                raise DownloadFailure("ARCHIVE_TOO_LARGE")
            return raw, {key: response.headers[key] for key in ("Last-Modified", "ETag")
                         if key in response.headers}
    except urllib.error.HTTPError as error:
        retry = error.headers.get("Retry-After")
        raise DownloadFailure("HTTP_ERROR", error.code,
                              retry[:80] if isinstance(retry, str) else None) from None
    except (OSError, urllib.error.URLError):
        raise DownloadFailure("NETWORK_UNAVAILABLE") from None


def _stores(root):
    root = Path(root)
    database = Database(root / "data/goal_analysis.sqlite3")
    database.initialize()
    return database, CacheStore(database), SnapshotStore(database, root / "data/raw")


def season_code(year):
    if type(year) is not int or not 1993 <= year <= 2098:
        raise ValueError("INVALID_SEASON")
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


def archive_members(raw):
    """Read bounded members in memory; never extract archive paths to disk."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if (len(members) > 100 or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES
                    or any(item.file_size > 8_000_000 for item in members)):
                raise ValueError("ARCHIVE_TOO_LARGE")
            seen, output = set(), []
            for item in members:
                match = re.fullmatch(r"([A-Z][A-Z0-9]{0,3})\.csv", item.filename)
                if not match:
                    continue
                code = match[1]
                if code in seen:
                    raise ValueError("DUPLICATE_ARCHIVE_MEMBER")
                seen.add(code)
                output.append((code, archive.read(item)))
            if not output:
                raise ValueError("NO_LEAGUE_FILES")
            return output
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("INVALID_ARCHIVE") from error


def fetch_archives(root, years, *, max_downloads=11, clock=lambda: datetime.now(UTC),
                   transport=download, pause=time.sleep):
    """Most recent seasons first. A cached file never consumes a new request."""
    years = sorted(set(years), reverse=True)
    now = clock()
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
            or type(max_downloads) is not int or not 0 <= max_downloads <= 40):
        raise ValueError("INVALID_DOWNLOAD_CONFIGURATION")
    for year in years:
        season_code(year)
    current = now.year if now.month >= 7 else now.year - 1
    if len(years) > 40 or any(year > current for year in years):
        raise ValueError("INVALID_SEASON_RANGE")
    _, cache, snapshots = _stores(root)
    results, requests = [], 0
    for year in years:
        code = season_code(year)
        url = f"{BASE}/{code}/data.zip"
        saved = cache.get(NAMESPACE, code, now)
        if saved:
            stamp = datetime.fromisoformat(saved["fetched_at"])
            if stamp.tzinfo is None or stamp > now:
                raise ValueError("INVALID_CACHED_TIMESTAMP")
            raw = snapshots.read(saved["content_hash"])
            if hashlib.sha256(raw).hexdigest() != saved["content_hash"]:
                raise ValueError("DAMAGED_CACHED_ARCHIVE")
            results.append({**saved, "status": "CACHED", "season_code": code})
            continue
        if requests >= max_downloads:
            results.append({"season_code": code, "source_url": url, "status": "LOCAL_LIMIT"})
            continue
        if requests:
            pause(1.0)
        requests += 1
        try:
            raw, headers = transport(url)
            if len(raw) > MAX_ARCHIVE_BYTES:
                raise ValueError("ARCHIVE_TOO_LARGE")
            members = archive_members(raw)
            fetched = clock()
            source = snapshots.save("football_data_co_uk", "season_zip", raw,
                                    "application/zip", fetched, url)
            saved = {"season_code": code, "source_url": url, "content_hash": source.content_hash,
                     "fetched_at": fetched.isoformat(), "bytes": len(raw),
                     "leagues": [name for name, _ in members], "headers": headers}
            cache.set(NAMESPACE, code, saved, timedelta(hours=6) if year == current
                      else timedelta(days=30), fetched)
            results.append({**saved, "status": "DOWNLOADED"})
        except DownloadFailure as error:
            results.append({"season_code": code, "source_url": url, "status": "UNAVAILABLE",
                            "error_code": error.code, "http_status": error.status,
                            "retry_after": error.retry_after})
            if error.status != 404:
                break  # Respect rate limits/outages; never retry the failing source.
        except ValueError as error:
            results.append({"season_code": code, "source_url": url, "status": "INVALID_ARCHIVE",
                            "error_code": str(error)})
            break
    return {"requests": requests, "archives": results}


def ingest_archives(root, downloaded, *, as_of=None):
    from goal_analysis.providers.football_data_csv import parse_csv
    from goal_analysis.storage.sports_archive import SportsArchive

    database, _, snapshots = _stores(root)
    store = SportsArchive(database)
    store.initialize()
    results = []
    for source in downloaded["archives"]:
        if source["status"] not in {"CACHED", "DOWNLOADED"}:
            continue
        raw = snapshots.read(source["content_hash"])
        if hashlib.sha256(raw).hexdigest() != source["content_hash"]:
            raise ValueError("DAMAGED_CACHED_ARCHIVE")
        stamp = datetime.fromisoformat(source["fetched_at"])
        for league, content in archive_members(raw):
            url = f"{BASE}/{source['season_code']}/{league}.csv"
            csv_source = snapshots.save("football_data_co_uk", "season_csv", content,
                                        "text/csv", stamp, url)
            try:
                parsed = parse_csv(content, league, source["season_code"], as_of or datetime.now(UTC).date())
                counts = store.ingest(parsed, source_url=url, fetched_at=stamp,
                    content_hash=csv_source.content_hash, metadata={
                        "archive_sha256": source["content_hash"],
                        "archive_url": source["source_url"], "source_headers": source["headers"],
                        "original_columns": parsed.get("original_columns", parsed["columns"]),
                        "ignored_empty_trailing_columns": parsed.get("ignored_empty_trailing_columns", 0),
                        "commercial_usage_rights": "UNREVIEWED",
                        "availability_time_policy": "ACTUAL_RECEIPT_NOT_HISTORICAL_PUBLICATION",
                        "odds_policy": "HISTORICAL_ONLY_NOT_CURRENT_EXECUTABLE_QUOTES"})
                results.append({"league_code": league, "season_code": source["season_code"],
                                "status": "IMPORTED", **counts})
            except ValueError as error:
                results.append({"league_code": league, "season_code": source["season_code"],
                                "status": "REJECTED", "error_code": str(error)[:160]})
    return {"files": results, "summary": store.summary()}


def run(root, years, *, max_downloads=11):
    downloaded = fetch_archives(root, years, max_downloads=max_downloads)
    result = {"observed_at": datetime.now(UTC).isoformat(), "downloads": downloaded,
              "import": ingest_archives(root, downloaded), "automatic_model_changes": False}
    destination = Path(root) / "data/arthur/football-data-import.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result
