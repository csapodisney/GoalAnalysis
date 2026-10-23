"""Bounded public GitHub metadata discovery, never code installation/execution.

Source discovery does not certify a model, license, security or profitability.
Only a separately reviewed, locally implemented adapter can enter paper audition.
GitHub API: https://docs.github.com/en/rest/git/refs#get-a-reference
Seeds verified from their maintainers' public repositories; no README instructions
or remote code are imported. Responses and network errors are untrusted data.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from goal_analysis.config.portfolio import PROFILE_IDS

BERLIN = ZoneInfo("Europe/Berlin")
API = "https://api.github.com"
REQUEST_LIMIT = 9
MAX_BYTES = 256_000
TIMEOUT = 8
SEARCH_LIMIT = 2
SEARCH_URL = API + "/search/repositories?" + urlencode({
    "q": "football prediction language:Python archived:false stars:>10",
    "sort": "stars", "order": "desc", "per_page": SEARCH_LIMIT, "page": 1,
})
SEEDS = (
    ("martineastwood/penaltyblog", "EXISTING_INTEGRATION",
     "Football score models; existing local GAUSS/KEPLER integrations are separately versioned."),
    ("lucasmaystre/choix", "SOURCE_REVIEW",
     "Bradley-Terry/choice inference library; a football draw-aware adapter is not implemented."),
)
PIPELINE = ["SOURCE_REVIEW", "ADAPTER_REQUIRED", "PAPER_AUDITION", "ELIGIBLE_CERTIFIED_ADAPTER"]
LIMITS = [
    "A repository is a source candidate, not a running agent or a qualified strategy.",
    "License identifiers are GitHub metadata, not a legal or security certification.",
    "An upstream commit pin is not the revision installed by an existing local adapter.",
    "No source download, dependency installation, arbitrary command, AI call or subscription.",
    "Search is bounded and incomplete by design; stars do not demonstrate forecast quality.",
    "A reserved weekly attempt is never automatically repeated after an uncertain interruption.",
]
_OWNER = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}"
_REPO = r"[A-Za-z0-9_.-]{1,100}"
_NAME = re.compile(rf"{_OWNER}/{_REPO}\Z")
_BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}\Z")
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")


def _stamp(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Scouting timestamps must include a time zone")
    return stamp.astimezone(UTC)


def _period(stamp):
    day = stamp.astimezone(BERLIN).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _valid_name(value):
    return isinstance(value, str) and bool(_NAME.fullmatch(value)) and value.split("/")[1] not in {".", ".."}


def _valid_branch(value):
    return (isinstance(value, str) and bool(_BRANCH.fullmatch(value))
            and ".." not in value and not value.endswith(("/", ".", ".lock"))
            and all(part not in {"", ".", ".."} for part in value.split("/")))


def allowed_url(url):
    """Exact HTTPS origin, fixed search query, and two read-only endpoint shapes."""
    if not isinstance(url, str) or len(url) > 600 or any(ord(c) < 33 for c in url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.fragment:
        return False
    if url == SEARCH_URL:
        return True
    if parsed.query or not parsed.path.startswith("/repos/"):
        return False
    parts = parsed.path[len("/repos/"):].split("/", 2)
    if len(parts) < 2 or not _valid_name("/".join(parts[:2])):
        return False
    return len(parts) == 2 or (parts[2].startswith("git/ref/heads/")
                              and _valid_branch(parts[2][len("git/ref/heads/"):]))


class ScoutError(Exception):
    """Static public code only; no raw upstream errors or response bodies."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ScoutError("REDIRECT_BLOCKED")


def github_transport(url, *, timeout=TIMEOUT, max_bytes=MAX_BYTES):
    if not allowed_url(url):
        raise ScoutError("URL_BLOCKED")
    # No inherited proxy credentials, authentication, GitHub token, cookies or redirects.
    request = Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Arthur-Source-Scout/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }, method="GET")
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
            if response.geturl() != url:
                raise ScoutError("REDIRECT_BLOCKED")
            if response.status != 200:
                raise ScoutError("HTTP_UNAVAILABLE")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise ScoutError("RESPONSE_TOO_LARGE")
            result = json.loads(payload.decode("utf-8"))
            if not isinstance(result, dict):
                raise ScoutError("INVALID_RESPONSE")
            return result
    except HTTPError as error:
        raise ScoutError("RATE_LIMIT_OR_FORBIDDEN" if error.code in {403, 429}
                         else "NOT_FOUND" if error.code == 404 else "HTTP_UNAVAILABLE") from None
    except (URLError, TimeoutError, OSError):
        raise ScoutError("NETWORK_UNAVAILABLE") from None
    except (ValueError, UnicodeError):
        raise ScoutError("INVALID_RESPONSE") from None


def _candidate(name, origin, stage="SOURCE_REVIEW", description=None):
    owner, repository = name.split("/")
    return {
        "candidate_id": "github:" + name.lower(), "discovery_origin": origin,
        "onboarding_stage": stage,
        "adapter_status": "EXISTING_APPROVED" if stage == "EXISTING_INTEGRATION" else "NOT_IMPLEMENTED",
        "runnable": False, "security_certified": False, "metadata_status": "UNAVAILABLE",
        "repository": {
            "name": repository, "owner": owner, "url": "https://github.com/" + name,
            # Raw third-party descriptions/readmes are deliberately not persisted or prompted.
            "description": description or "Unreviewed source discovered by the bounded GitHub search.",
            "description_source": "LOCAL_SUMMARY",
            "license": {"spdx_id": None, "name": None, "verified": False,
                        "verification_basis": "GITHUB_METADATA_ONLY", "pinned_commit_license_audited": False},
            "default_branch": None, "pinned_commit": None, "archived": None,
            "disabled": None, "stars": None, "fetched_at": None,
            "commit_fetched_at": None,
            "provenance": {"provider": "github_api", "metadata_url": API + "/repos/" + name,
                           "commit_url": None},
        },
    }


def _metadata(candidate, payload, fetched_at):
    repository = candidate["repository"]
    name = repository["owner"] + "/" + repository["name"]
    full_name = payload.get("full_name")
    if (not _valid_name(full_name) or full_name.lower() != name.lower()
            or type(payload.get("archived")) is not bool or type(payload.get("disabled")) is not bool
            or payload.get("private") is not False or not _valid_branch(payload.get("default_branch"))):
        raise ScoutError("INVALID_REPOSITORY_METADATA")
    stars = payload.get("stargazers_count")
    if type(stars) is not int or stars < 0:
        raise ScoutError("INVALID_REPOSITORY_METADATA")
    license_data = payload.get("license")
    if license_data is not None and not isinstance(license_data, dict):
        raise ScoutError("INVALID_LICENSE_METADATA")
    identifier = (license_data or {}).get("spdx_id")
    if (isinstance(identifier, str) and identifier != "NOASSERTION"
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{1,79}", identifier)):
        repository["license"].update(spdx_id=identifier, name=identifier, verified=True)
    repository.update(default_branch=payload["default_branch"], archived=payload["archived"],
                      disabled=payload["disabled"], stars=stars, fetched_at=fetched_at)
    repository["provenance"]["commit_url"] = API + "/repos/" + name + "/git/ref/heads/" + payload["default_branch"]
    candidate["metadata_status"] = "PARTIAL"


class Scout:
    """One bounded metadata attempt per current Berlin week; snapshot() is read-only."""

    def __init__(self, root, *, transport=None, clock=None):
        self.root = Path(root).resolve()
        self.path = self.root / "data/team/scouting.sqlite3"
        self.transport = transport or github_transport
        self.clock = clock or (lambda: datetime.now(UTC))

    @contextmanager
    def _connection(self, initialize=False):
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=15)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scout_attempts (
                    period TEXT PRIMARY KEY, claimed_at TEXT NOT NULL,
                    initial_json TEXT NOT NULL, initial_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scout_requests (
                    period TEXT NOT NULL, ordinal INTEGER NOT NULL, requested_at TEXT NOT NULL,
                    url TEXT NOT NULL, PRIMARY KEY(period,ordinal));
                CREATE TABLE IF NOT EXISTS scout_snapshots (
                    period TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS scout_snapshots_no_update BEFORE UPDATE ON scout_snapshots
                    BEGIN SELECT RAISE(ABORT,'immutable scouting snapshot'); END;
                CREATE TRIGGER IF NOT EXISTS scout_snapshots_no_delete BEFORE DELETE ON scout_snapshots
                    BEGIN SELECT RAISE(ABORT,'immutable scouting snapshot'); END;
                CREATE TRIGGER IF NOT EXISTS scout_attempts_no_update BEFORE UPDATE ON scout_attempts
                    BEGIN SELECT RAISE(ABORT,'immutable scouting reservation'); END;
                CREATE TRIGGER IF NOT EXISTS scout_attempts_no_delete BEFORE DELETE ON scout_attempts
                    BEGIN SELECT RAISE(ABORT,'immutable scouting reservation'); END;
            """)
        else:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
            db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _base(as_of, now, status="NO_SNAPSHOT"):
        period = _period(as_of)
        return {
            "schema_version": 1, "scout_id": "github-scout-" + period,
            "period_start": period, "as_of": as_of.isoformat(), "created_at": now.isoformat(),
            "status": status, "request_count": 0, "request_limit": REQUEST_LIMIT,
            "retry_this_period": False, "candidates": [], "diagnostics": [],
            "integrated_profiles": [{"profile_id": profile, "catalogue_status": "LOCAL_PROFILE",
                                     "qualification": "NOT_ASSESSED_BY_SCOUT"} for profile in PROFILE_IDS],
            "onboarding_stages": list(PIPELINE), "automatic_code_execution": False,
            "automatic_installation": False, "automatic_promotion": False,
            "next_period_start": (as_of.astimezone(BERLIN).date()
                                  + timedelta(days=7 - as_of.astimezone(BERLIN).weekday())).isoformat(),
            "metadata_time_policy": "LIVE_AT_FETCH_TIME_NOT_HISTORICAL_FORECAST_EVIDENCE",
            "limits": list(LIMITS),
        }

    @staticmethod
    def _verify(row):
        try:
            payload = json.loads(row["snapshot_json"])
            recorded = payload.pop("snapshot_hash")
            if recorded != row["snapshot_hash"] or _hash(payload) != recorded:
                raise ValueError
            if (payload["schema_version"] != 1 or type(payload["request_count"]) is not int
                    or not 0 <= payload["request_count"] <= REQUEST_LIMIT
                    or len(payload["candidates"]) > len(SEEDS) + SEARCH_LIMIT):
                raise ValueError
            payload["snapshot_hash"] = recorded
            return payload
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("Scouting cache validation failed") from None

    def snapshot(self):
        if not self.path.is_file():
            now = _stamp(self.clock())
            return self._base(now, now)
        with self._connection() as db:
            row = db.execute("SELECT * FROM scout_snapshots ORDER BY period DESC LIMIT 1").fetchone()
        if row:
            return self._verify(row)
        now = _stamp(self.clock())
        return self._base(now, now)

    def _save(self, db, report):
        report["snapshot_hash"] = _hash(report)
        db.execute("INSERT OR IGNORE INTO scout_snapshots VALUES (?,?,?)", (
            report["period_start"], _json(report), report["snapshot_hash"],
        ))
        return self._verify(db.execute("SELECT * FROM scout_snapshots WHERE period=?",
                                       (report["period_start"],)).fetchone())

    def _request(self, period, url):
        if not allowed_url(url):
            raise ScoutError("URL_BLOCKED")
        # Persist the debit BEFORE network I/O. An uncertain request is never replayed.
        with self._connection(initialize=True) as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM scout_requests WHERE period=?", (period,)).fetchone()[0]
            if count >= REQUEST_LIMIT:
                raise ScoutError("REQUEST_LIMIT")
            db.execute("INSERT INTO scout_requests VALUES (?,?,?,?)", (
                period, count + 1, _stamp(self.clock()).isoformat(), url,
            ))
        result = self.transport(url, timeout=TIMEOUT, max_bytes=MAX_BYTES)
        if not isinstance(result, dict) or len(_json(result).encode("utf-8")) > MAX_BYTES:
            raise ScoutError("INVALID_RESPONSE")
        return result

    def run(self, as_of):
        as_of, now = _stamp(as_of), _stamp(self.clock())
        if as_of > now or _period(as_of) != _period(now):
            raise ValueError("Scouting runs only for the current Berlin week, never historical backfill")
        period = _period(as_of)
        with self._connection(initialize=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM scout_snapshots WHERE period=?", (period,)).fetchone()
            if row:
                return self._verify(row)
            claim = db.execute("SELECT * FROM scout_attempts WHERE period=?", (period,)).fetchone()
            if claim:
                report = json.loads(claim["initial_json"])
                if _hash(report) != claim["initial_hash"]:
                    raise ValueError("Scouting reservation validation failed")
                report["request_count"] = db.execute("SELECT count(*) FROM scout_requests WHERE period=?", (period,)).fetchone()[0]
                report["status"] = "IN_PROGRESS" if now - _stamp(claim["claimed_at"]) < timedelta(minutes=3) else "INTERRUPTED"
                report["diagnostics"] = [{"code": "RESERVED_ATTEMPT_NOT_REPEATED"}]
                return report if report["status"] == "IN_PROGRESS" else self._save(db, report)
            report = self._base(as_of, now)
            db.execute("INSERT INTO scout_attempts VALUES (?,?,?,?)", (
                period, now.isoformat(), _json(report), _hash(report),
            ))
        candidates = [_candidate(name, "VERIFIED_MAINTAINER_SEED", stage, description)
                      for name, stage, description in SEEDS]
        diagnostics = []
        fatal = False

        def request(url, candidate_id=None):
            nonlocal fatal
            if fatal:
                return None
            try:
                return self._request(period, url)
            except Exception as error:  # noqa: BLE001 - never expose arbitrary transport/body errors
                code = str(error) if isinstance(error, ScoutError) and str(error) in {
                    "URL_BLOCKED", "REDIRECT_BLOCKED", "RATE_LIMIT_OR_FORBIDDEN", "NOT_FOUND",
                    "HTTP_UNAVAILABLE", "NETWORK_UNAVAILABLE", "RESPONSE_TOO_LARGE",
                    "INVALID_RESPONSE", "REQUEST_LIMIT",
                } else "UNAVAILABLE"
                diagnostics.append({"code": code, "candidate_id": candidate_id})
                fatal = code in {"RATE_LIMIT_OR_FORBIDDEN", "REQUEST_LIMIT"}
                return None

        search = request(SEARCH_URL)
        if search is not None:
            items = search.get("items")
            if not isinstance(items, list):
                diagnostics.append({"code": "INVALID_SEARCH_RESPONSE"})
            else:
                known = {c["candidate_id"] for c in candidates}
                for item in items[:SEARCH_LIMIT]:
                    name = item.get("full_name") if isinstance(item, dict) else None
                    if not _valid_name(name):
                        diagnostics.append({"code": "INVALID_SEARCH_ITEM"})
                    elif "github:" + name.lower() not in known:
                        candidates.append(_candidate(name, "BOUNDED_GITHUB_SEARCH"))
                        known.add("github:" + name.lower())
                if search.get("incomplete_results") is True:
                    diagnostics.append({"code": "INCOMPLETE_GITHUB_SEARCH"})
        for candidate in candidates:
            repository = candidate["repository"]
            metadata = request(repository["provenance"]["metadata_url"], candidate["candidate_id"])
            if metadata is None:
                continue
            try:
                _metadata(candidate, metadata, _stamp(self.clock()).isoformat())
            except ScoutError as error:
                diagnostics.append({"code": str(error), "candidate_id": candidate["candidate_id"]})
                continue
            ref = request(repository["provenance"]["commit_url"], candidate["candidate_id"])
            if ref is not None:
                commit = ref.get("object")
                if (isinstance(commit, dict) and commit.get("type") == "commit"
                        and isinstance(commit.get("sha"), str) and _SHA.fullmatch(commit["sha"])
                        and ref.get("ref") == "refs/heads/" + repository["default_branch"]):
                    repository.update(pinned_commit=commit["sha"].lower(), commit_fetched_at=_stamp(self.clock()).isoformat())
                    candidate["metadata_status"] = "COMPLETE"
                else:
                    diagnostics.append({"code": "INVALID_COMMIT_REFERENCE", "candidate_id": candidate["candidate_id"]})
        report.update(candidates=candidates, diagnostics=diagnostics)
        available = any(c["metadata_status"] != "UNAVAILABLE" for c in candidates)
        report["status"] = "COMPLETE" if not diagnostics else "PARTIAL" if available else "UNAVAILABLE"
        with self._connection(initialize=True) as db:
            db.execute("BEGIN IMMEDIATE")
            report["request_count"] = db.execute("SELECT count(*) FROM scout_requests WHERE period=?", (period,)).fetchone()[0]
            return self._save(db, report)
