"""One shared Grok review, one independent check, one prospective repair pass.

Only a newly built in-memory package can be repaired here. Existing ledger rows
and frozen reports are never opened for writing. Completed or interrupted checks
are not retried just because a scheduler or browser invokes this code again.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from goal_analysis.engine.publication_window import allows_new_ticket
from goal_analysis.jobs.grok_advisory import analyze

VERSION = "grok-one-batch-concurrence-v1"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def _stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Missing timezone")
    return parsed.astimezone(UTC)


def _path(root):
    return Path(root) / "data/arthur/grok-gate.sqlite3"


def _open(root):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            cache_key TEXT PRIMARY KEY, request_id TEXT NOT NULL, day TEXT NOT NULL,
            run_id TEXT NOT NULL, created_at TEXT NOT NULL, packet_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS results (
            cache_key TEXT PRIMARY KEY REFERENCES requests(cache_key),
            created_at TEXT NOT NULL, result_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS requests_no_update BEFORE UPDATE ON requests
            BEGIN SELECT RAISE(ABORT, 'Immutable request'); END;
        CREATE TRIGGER IF NOT EXISTS requests_no_delete BEFORE DELETE ON requests
            BEGIN SELECT RAISE(ABORT, 'Immutable request'); END;
        CREATE TRIGGER IF NOT EXISTS results_no_update BEFORE UPDATE ON results
            BEGIN SELECT RAISE(ABORT, 'Immutable result'); END;
        CREATE TRIGGER IF NOT EXISTS results_no_delete BEFORE DELETE ON results
            BEGIN SELECT RAISE(ABORT, 'Immutable result'); END;
    """)
    return connection


def _native_mode(root):
    try:
        value=json.loads((Path(root)/'config/grok-native-gate.json').read_text('utf-8-sig'))
        return 'SHADOW' if value=={'enabled':True,'mode':'SHADOW'} else 'DISABLED'
    except (OSError,ValueError,TypeError):
        return 'DISABLED'


def _reviewed_packet(root, request_id):
    """Only the exact persisted request binds an opinion to a native selection."""
    from goal_analysis.agents.grok_research import _packet

    path=(Path(root)/'data/arthur/grok.sqlite3').resolve()
    if not path.exists():return None
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            row=db.execute('SELECT packet_json FROM grok_requests WHERE request_id=? '
                           'AND length(packet_json)<=2000000',(request_id,)).fetchone()
        if row is None:return None
        _,fixtures,recs,cutoff=_packet(json.loads(row[0]))
        return {'recommendations':recs,'fixtures':fixtures,'cutoff':cutoff,
                'packet_sha256':hashlib.sha256(row[0].encode('utf-8')).hexdigest()}
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,AttributeError,OverflowError):
        return None


def _native_bound(card, critique, reviewed):
    from goal_analysis.providers.the_stats_odds import QUOTE_PROVENANCE

    if reviewed is None or any(not isinstance(critique.get(k),str) or not critique[k].strip()
                               or len(critique[k])>240 for k in ('strengths','risks')):return False
    rec=reviewed['recommendations'].get(critique.get('recommendation_id'))
    if rec is None or rec['profile_id']!=card['profile_id'] or len(rec['legs'])!=len(card['legs']):
        return False
    fields=('fixture_id','market_key','selection_key','period','point','decimal_price','bookmaker',
            'quote_provider','quote_source_id','quoted_at',*QUOTE_PROVENANCE)
    def value(row,key):
        v=row.get(key)
        return _stamp(v) if key in {'quoted_at','quote_observed_at'} and v is not None else v
    return all(all(value(old,k)==value(new,k) for k in fields)
               and _stamp(reviewed['fixtures'][old['fixture_id']]['kickoff'])==_stamp(new['kickoff'])
               for old,new in zip(rec['legs'],card['legs'],strict=True))


def _concerns(report, advice, now, *, reviewed=None):
    rows = {r["profile_id"]: r for r in report.get("tickets", [])}
    rows.update({r["profile_id"]: r for r in report.get("recommendations", [])})
    if 'generated_tickets' in report:
        rows={r['profile_id']:r for r in report['generated_tickets']}
    result = advice.get("result") or {}
    claims = {r["claim_id"]: r for r in result.get("claims", [])}
    concerns, seen = [], set()
    for critique in result.get("critiques", []):
        profile = critique.get("profile_id")
        card = rows.get(profile)
        if (profile == "daily223" or not card or critique.get("verdict") != "QUESTIONABLE"
                or critique.get("actionability") is not True
                or critique.get("review_validation") != "COMPLETE"
                or not allows_new_ticket(card, now)):
            continue
        for flag in critique.get("flagged_legs", []):
            index = flag.get("leg_index")
            if type(index) is not int or not 0 <= index < len(card["legs"]):
                continue
            leg = card["legs"][index]
            if flag.get("fixture_id") != leg.get("fixture_id"):
                continue
            if leg.get('quote_provider') == 'the_stats_api' and (
                    not _native_bound(card,critique,reviewed) or reviewed['cutoff']>now):
                continue
            identity = [profile, leg.get("fixture_id"), leg.get("market_key"),
                        leg.get("selection_key"), leg.get("period")]
            cid = hashlib.sha256(_json(identity).encode()).hexdigest()[:24]
            if cid in seen:
                continue
            seen.add(cid)
            packet_leg = {key: deepcopy(leg[key]) for key in (
                "fixture_id", "home_team", "away_team", "kickoff", "market_key",
                "selection_key", "period", "decimal_price", "model_probability",
                "estimated_edge", "model_stability", "confidence_kind", "competition",
                "competition_type", "quoted_at", "quote_provider") if key in leg}
            # These metrics are optional evidence. Unknown is absence, never
            # zero. Keep every non-null value for the strict consumer to check;
            # in particular, do not strip a native quoted_at=None receipt.
            for metric in ('model_stability', 'estimated_edge'):
                if packet_leg.get(metric) is None:
                    packet_leg.pop(metric, None)
            if leg.get('quote_provider')=='the_stats_api':
                packet_leg.update({key:deepcopy(leg[key]) for key in (
                    'quote_source_id','bookmaker','region','currency','point','quote_observed_at',
                    'quote_source_sha256','quote_time_basis','provider_bookmaker','provider_market_id') if key in leg})
            # Existing evidence is supplied once for the exact questioned leg.
            packet_leg["evidence"] = [
                {key: deepcopy(e[key]) for key in ("evidence_id", "category", "statement",
                 "source_id", "observed_at", "claim_type") if key in e}
                for e in leg.get("evidence", [])[:8] if isinstance(e, dict)]
            concerns.append({"concern_id": cid, "profile_id": profile, "leg": packet_leg,
                "reason": flag.get("reason", ""),
                "claims": [claims[c] for c in flag.get("claim_ids", []) if c in claims]})
    return {"date": report["date"], "cutoff": now.isoformat(), "concerns": concerns}


def _independent(root, report, advice, packet, reviewer, clock):
    from goal_analysis.agents.grok_concurrence import ConcurrenceReviewer

    if len(packet["concerns"]) > 40 or len(_json(packet)) > 80000:
        return {"status": "INPUT_LIMIT", "decisions": [], "usage": {"requests": 0}}
    key = hashlib.sha256(_json([VERSION, advice["request_id"],
                               packet["concerns"]]).encode()).hexdigest()
    with closing(_open(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        saved = db.execute("SELECT result_json FROM results WHERE cache_key=?", (key,)).fetchone()
        if saved:
            return {**json.loads(saved[0]), "cached": True}
        pending = db.execute("SELECT 1 FROM requests WHERE cache_key=?", (key,)).fetchone()
        if pending:
            return {"status": "INTERRUPTED", "decisions": [], "usage": {"requests": 0}}
        db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)", (
            key, advice["request_id"], report["date"], report["run_id"],
            clock().isoformat(), _json(packet)))
        db.commit()
    try:
        result = (reviewer or ConcurrenceReviewer()).review(deepcopy(packet))
        if not isinstance(result, dict) or not isinstance(result.get("decisions"), list):
            raise TypeError("Invalid concurrence")
    except Exception:  # noqa: BLE001 - raw provider errors may contain credentials
        result = {"status": "UNAVAILABLE", "decisions": [], "usage": {"requests": 1}}
    with closing(_open(root)) as db:
        db.execute("INSERT INTO results VALUES (?,?,?)", (key, clock().isoformat(), _json(result)))
        db.commit()
    return result


def review_and_repair(root, report, options, *, advisor=None, reviewer=None,
                      clock=lambda: datetime.now(UTC), manual_review=False):
    """Return a new package; failure leaves all original selections in place."""
    from goal_analysis.engine.grok_repair import repair

    output = deepcopy(report)
    summary = {"version": VERSION, "status": "UNAVAILABLE", "repair_rounds": 0,
               "changed_profiles": [], "daily223_protected": True,
               "mode": "ONE_BATCH_CONDITIONAL_REPAIR", "automatic_wager": False,
               "review_contract": {
                   "grok_batches_max": 1, "independent_review_batches_max": 1,
                   "repair_rounds_max": 1, "repair_source": "PREVALIDATED_SAME_RUN_OPTIONS",
                   "requires_concrete_independent_sources": True,
                   "all_eligible_ready_tickets_in_shared_packet": True,
               }}
    try:
        now = clock()
        advice = analyze(root, report, advisor=advisor, now=now, completion_clock=clock,
                         manual_review=manual_review)
        summary.update(status=advice["status"], request_id=advice.get("request_id"),
                       coverage=advice.get("current_coverage", advice.get("coverage", {})),
                       cached=advice.get("status") == "CACHED")
        result = advice.get("result") or {}
        critiques = result.get("critiques", []) if isinstance(result, dict) else []
        summary["reviewed_profiles"] = sorted({row.get("profile_id") for row in critiques
                                                if isinstance(row, dict)
                                                and isinstance(row.get("profile_id"), str)})
        summary["critique_count"] = len(critiques)
        summary["critiques"] = [
            {key: deepcopy(row.get(key)) for key in (
                "profile_id", "strengths", "risks", "verdict", "flagged_legs",
                "review_validation", "actionability", "protected_profile")}
            for row in critiques if isinstance(row, dict)
        ]
        # Old saved research can be displayed but is not a new alteration mandate.
        created = advice.get("created_at")
        if result.get("status") in {"COMPLETE", "PARTIAL"} and created:
            age = now - _stamp(created)
            if not timedelta(0) <= age <= timedelta(minutes=60):
                summary["repair_status"] = "CACHED_REVIEW_TOO_OLD_FOR_REPAIR"
            else:
                has_native=any(leg.get('quote_provider')=='the_stats_api'
                               for group in ('recommendations','tickets') for card in report.get(group,[])
                               for leg in card.get('legs',[]))
                reviewed = _reviewed_packet(root, advice.get('request_id')) if has_native else None
                packet = _concerns(report, advice, now, reviewed=reviewed)
                native_ids={c['concern_id'] for c in packet['concerns']
                            if c['leg'].get('quote_provider')=='the_stats_api'}
                if native_ids:
                    native_mode=_native_mode(root)
                    summary['native_quote_gate']={'mode':native_mode,'live_decision_effect':False,
                        'bound_request_packet_sha256':reviewed['packet_sha256'],
                        'confirmed_shadow_concerns':0,'would_repair_profiles':[]}
                    if native_mode!='SHADOW':
                        packet['concerns']=[c for c in packet['concerns'] if c['concern_id'] not in native_ids]
                summary["questioned_legs"] = len(packet["concerns"])
                if packet["concerns"]:
                    checked = _independent(root, report, advice, packet, reviewer, clock)
                    summary["concurrence"] = checked
                    allowed = {r["concern_id"]: r for r in packet["concerns"]}
                    decisions = checked.get("decisions", [])
                    identifiers = [d.get("concern_id") for d in decisions if isinstance(d, dict)]
                    confirmed = []
                    if checked.get("status") in {"COMPLETE", "PARTIAL"}:
                        for decision in decisions:
                            cid = decision.get("concern_id")
                            if (cid not in allowed or identifiers.count(cid) != 1
                                    or decision.get("agree") is not True
                                    or not decision.get("source_urls")
                                    or decision.get("provenance_status") != "OBSERVED_INDEPENDENT_SOURCE"):
                                continue
                            concern = allowed[cid]
                            if decision.get("fixture_id") != concern["leg"]["fixture_id"]:
                                continue
                            if cid in native_ids:
                                if decision.get('basis') in {'VERIFIED_CONTEXT','MARKET_MISMATCH'}:
                                    shadow=summary['native_quote_gate']
                                    shadow['confirmed_shadow_concerns']+=1
                                    shadow['would_repair_profiles']=sorted(set(shadow['would_repair_profiles'])|
                                                                          {concern['profile_id']})
                                continue  # First native contract rollout is diagnostics only.
                            confirmed.append({"concern_id": cid, "profile_id": concern["profile_id"],
                                "fixture_id": concern["leg"]["fixture_id"],
                                "reason": decision["reason"], "source_urls": decision["source_urls"]})
                    summary["independently_confirmed_concerns"] = len(confirmed)
                    if confirmed:
                        output, audit = repair(output, options, confirmed, clock())
                        summary.update(repair_rounds=1, repair=audit,
                                       changed_profiles=audit.get("changed_profiles", []))
                    else:
                        summary["repair_status"] = ('NATIVE_CONCURRENCE_SHADOW_ONLY'
                            if summary.get('native_quote_gate',{}).get('confirmed_shadow_concerns')
                            else "NO_INDEPENDENTLY_CONFIRMED_CONCERN")
                else:
                    summary["repair_status"] = "NO_ACTIONABLE_CONCERN"
        output["grok_review"] = summary
    except Exception:  # noqa: BLE001 - optional gate cannot destroy a generated package
        output = deepcopy(report)
        output["grok_review"] = {**summary, "repair_status": "UNAVAILABLE_NO_CHANGE",
                                 "changed_profiles": [], "repair_rounds": 0}
    return output
