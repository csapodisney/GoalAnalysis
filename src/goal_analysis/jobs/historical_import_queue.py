"""Immutable, newest-first five-year import queue; no active-plan replacement.

A small recent gap runs before the retained plan's durable cursor. Each call
shares one request/page budget and carries known quota fences across plans.
Only explicitly authorized execution after daily work can mutate/import data.
"""
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import the_stats_history as history
from .history_quota import latest_quota, quota_block

VERSION = "five-year-import-queue-v1"


def prepare_queue(main_plan, *, as_of):
    history._validate_plan(main_plan)
    now = history._stamp(as_of)
    if main_plan["years"] != 5 or history._stamp(main_plan["as_of"]) > now:
        raise ValueError("RETAINED_PAST_FIVE_YEAR_PLAN_REQUIRED")
    complete_main = history.prepare_plan(main_plan["competition_ids"], as_of=main_plan["as_of"],
        years=5, season_ids=main_plan["season_ids"])
    if any(main_plan[key] != complete_main[key] for key in ("date_from", "date_to")):
        raise ValueError("COMPLETE_FIVE_YEAR_MAIN_WINDOW_REQUIRED")
    current = history.prepare_plan(main_plan["competition_ids"], as_of=now, years=5)
    if main_plan["date_to"] < current["date_from"]:
        raise ValueError("RETAINED_PLAN_DOES_NOT_OVERLAP_CURRENT_HORIZON")
    first = (date.fromisoformat(main_plan["date_to"]) + timedelta(days=1)).isoformat()
    delta = history.prepare_plan(main_plan["competition_ids"], as_of=now, years=5,
        date_from=first, date_to=current["date_to"], season_ids=main_plan["season_ids"]
    ) if first <= current["date_to"] else None
    queue = {"version": VERSION, "as_of": now.isoformat(),
        "horizon_start": current["date_from"], "horizon_end": current["date_to"],
        "retained_main_plan": main_plan, "recent_delta_plan": delta,
        "retained_older_records": "PRESERVED_NOT_DELETED", "order": "RECENT_DELTA_THEN_RETAINED_CURSOR"}
    # JSON round-trip prevents caller-owned nested plans changing the hash.
    import json
    queue = json.loads(history._bytes(queue))
    return {**queue, "queue_sha256": history._hash(queue)}


def validate_queue(queue):
    if (not isinstance(queue, dict) or queue != prepare_queue(
            queue.get("retained_main_plan"), as_of=queue.get("as_of"))):
        raise ValueError("QUEUE_CHANGED_OR_DAMAGED")


def save_queue(path, queue):
    validate_queue(queue)
    path = Path(path)
    if path.exists() and history._read(path) != queue:
        raise ValueError("EXISTING_QUEUE_MUST_NOT_CHANGE")
    history._atomic(path, queue)


def run_queue(root, queue, *, execute=False, daily_work_complete=False, max_requests=20,
              max_pages=20, monthly_reserve=100, api_key=None, transport=None,
              clock=lambda: datetime.now(UTC), stop_at=None, quota_observer=None, root_lock=None):
    validate_queue(queue)
    if (type(execute) is not bool or type(daily_work_complete) is not bool
            or type(max_requests) is not int or not 0 <= max_requests <= 20
            or type(max_pages) is not int or not 1 <= max_pages <= 20
            or type(monthly_reserve) is not int or not 0 <= monthly_reserve <= 100_000):
        raise ValueError("INVALID_QUEUE_EXECUTION_BUDGET")
    now = history._stamp(clock())
    if history._stamp(queue["as_of"]) > now:
        raise ValueError("FUTURE_QUEUE")
    summary = {"version": VERSION, "queue_sha256": queue["queue_sha256"],
        "mode": "SHADOW", "automatic_model_changes": False, "requests": 0,
        "processed_pages": 0, "plans": [], "status": "PLAN_ONLY"}
    if not execute or not daily_work_complete:
        summary["status"] = "PLAN_ONLY" if not execute else "DAILY_WORK_HAS_PRIORITY"
        return summary
    if stop_at is not None and history._stamp(stop_at) <= now:
        summary["status"] = "TIME_WINDOW_EXHAUSTED"
        return summary
    last_seen, original_clock = now, clock

    def checked_clock():
        nonlocal last_seen
        current = history._stamp(original_clock())
        if current < last_seen:
            raise ValueError("QUEUE_CLOCK_MOVED_BACKWARDS")
        last_seen = current
        return current

    clock = checked_clock
    root = Path(root)
    if quota_observer is None:
        from goal_analysis.storage.provider_quota import observer_for

        quota_observer = observer_for(root,kind='history',clock=clock)
    work = root / "data/arthur/historical-import-queue"
    work.mkdir(parents=True, exist_ok=True)
    # One queue consumer at a time, including different immutable queue versions.
    with history._lock(work / "queue.lock"):
        from .historical_result_sync import sync_stats_results

        summary["legacy_sync"] = sync_stats_results(root, max_imports=20, clock=clock)
        if summary["legacy_sync"]["status"] != "COMPLETE":
            summary["status"] = "LEGACY_SYNC_PENDING"
            return summary
        quota_path = work / "quota-state.json"
        state = history._read(quota_path) if quota_path.exists() else None
        plans = [p for p in (queue["recent_delta_plan"], queue["retained_main_plan"]) if p]
        sources = [state, quota_observer.legacy_state() if quota_observer is not None else None]
        for plan in plans:
            folder = root / "data/arthur/the-stats-history" / plan["plan_sha256"]
            for name in ("quota-state.json", "last-batch.json"):
                path = folder / name
                if path.exists():
                    sources.append(history._read(path))
        state = latest_quota(sources, now=history._stamp(clock()), reserve=monthly_reserve)
        for plan in plans:
            reason = quota_block(state, now=history._stamp(clock()), reserve=monthly_reserve)
            cap = 0 if reason else max_requests - summary["requests"]
            remaining_pages = max_pages - summary["processed_pages"]
            if remaining_pages <= 0:
                summary["status"] = "PAGE_BATCH_LIMIT"
                break
            result = history.run_history(root, plan, execute=True, daily_work_complete=True,
                max_requests=cap, max_pages=remaining_pages, monthly_reserve=monthly_reserve,
                api_key=api_key, transport=transport, clock=clock, stop_at=stop_at, unified_results=True,
                **({'quota_observer':quota_observer} if quota_observer is not None else {}),
                **({'root_lock':root_lock} if root_lock is not None else {}))
            summary["plans"].append(result)
            summary["requests"] += result["requests"]
            summary["processed_pages"] += result["processed_pages"]
            if result["requests"]:
                persisted = root / "data/arthur/the-stats-history" / plan["plan_sha256"] / "quota-state.json"
                state = latest_quota([state, history._read(persisted) if persisted.exists() else result],
                    now=history._stamp(clock()), reserve=monthly_reserve)
                if state is not None:
                    history._atomic(quota_path, state)
            if result["status"] != "COMPLETE":
                summary["status"] = reason if reason and result["status"] == "REQUEST_LIMIT" else result["status"]
                break
        else:
            summary["status"] = "COMPLETE"
        summary["observed_at"] = history._stamp(clock()).isoformat()
        history._atomic(work / (queue["queue_sha256"] + "-last-batch.json"), summary)
    return summary
