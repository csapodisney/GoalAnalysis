"""No repeated model loop, no repair from uncorroborated model assertions."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

from goal_analysis.jobs import grok_gate as gate

NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)


def report():
    def card(profile, fid):
        return {"profile_id": profile, "status": "READY", "legs": [{
            "fixture_id": fid, "kickoff": (NOW + timedelta(hours=4)).isoformat(),
            "home_team": "Home", "away_team": "Away", "market_key": "h2h",
            "selection_key": "home", "period": "FULL_TIME", "decimal_price": 2.5}]}
    return {"date": "2026-10-18", "run_id": "test-run", "observed_at": NOW.isoformat(),
            "recommendations": [card("daily223", "protected"), card("gauss", "questioned")],
            "tickets": [], "preview_tickets": [], "profile_proposals": []}


def advice(data, *, old=False):
    return {"request_id": "review-1", "status": "COMPLETE",
            "created_at": (NOW - timedelta(hours=2) if old else NOW).isoformat(),
            "result": {"status": "COMPLETE", "claims": [], "critiques": [
                {"profile_id": c["profile_id"], "verdict": "QUESTIONABLE",
                 "actionability": True, "review_validation": "COMPLETE", "flagged_legs": [
                    {"leg_index": 0, "fixture_id": c["legs"][0]["fixture_id"],
                     "reason": "A konkrét kezdőcsapat ellenőrzést igényel.", "claim_ids": []}]}
                for c in data["recommendations"]]}}


class Reviewer:
    def __init__(self, *, agree=True, source=True):
        self.calls = []
        self.agree, self.source = agree, source

    def review(self, packet):
        self.calls.append(deepcopy(packet))
        return {"status": "COMPLETE", "usage": {"request_count": 1}, "decisions": [
            {"concern_id": c["concern_id"], "fixture_id": c["leg"]["fixture_id"],
             "agree": self.agree, "reason": "Igazolt, a választásra vonatkozó kerethír.",
             "source_urls": ["https://club.example/news"] if self.source else [],
             "provenance_status": "OBSERVED_INDEPENDENT_SOURCE" if self.source else "NOT_CONFIRMED"}
            for c in packet["concerns"]]}


def test_one_batch_concurrence_cached_and_protected_daily_never_sent(tmp_path, monkeypatch):
    from goal_analysis.engine import grok_repair

    original = report()
    unchanged = deepcopy(original)
    analyze_calls, repairs = [], []

    def analyze(*args, **kwargs):
        assert kwargs["completion_clock"]() == kwargs["now"]
        analyze_calls.append(1)
        return advice(original)

    def repair(data, options, confirmed, now):
        repairs.append(confirmed)
        return deepcopy(data), {"changed_profiles": ["gauss"]}

    monkeypatch.setattr(gate, "analyze", analyze)
    monkeypatch.setattr(grok_repair, "repair", repair)
    reviewer = Reviewer()
    first = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    second = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    assert original == unchanged
    assert len(reviewer.calls) == 1
    assert len(reviewer.calls[0]["concerns"]) == 1
    assert reviewer.calls[0]["concerns"][0]["profile_id"] == "gauss"
    assert all(c["profile_id"] != "daily223" for r in repairs for c in r)
    assert first["grok_review"]["repair_rounds"] == 1
    assert first["grok_review"]["review_contract"] == {
        "grok_batches_max": 1, "independent_review_batches_max": 1,
        "repair_rounds_max": 1, "repair_source": "PREVALIDATED_SAME_RUN_OPTIONS",
        "requires_concrete_independent_sources": True,
        "all_eligible_ready_tickets_in_shared_packet": True,
    }
    assert first["grok_review"]["reviewed_profiles"] == ["daily223", "gauss"]
    assert first["grok_review"]["critique_count"] == 2
    assert first["grok_review"]["independently_confirmed_concerns"] == 1
    assert second["grok_review"]["concurrence"]["cached"]
    # One Grok job invocation per pipeline entry, never another after a repair.
    # The job independently deduplicates its identical semantic packet.
    assert len(analyze_calls) == 2


def test_unverified_agreement_cannot_repair(tmp_path, monkeypatch):
    original = report()
    monkeypatch.setattr(gate, "analyze", lambda *a, **k: advice(original))
    result = gate.review_and_repair(tmp_path, original, {}, reviewer=Reviewer(source=False), clock=lambda: NOW)
    assert result["recommendations"] == original["recommendations"]
    assert result["grok_review"]["repair_rounds"] == 0
    assert result["grok_review"]["repair_status"] == "NO_INDEPENDENTLY_CONFIRMED_CONCERN"


def test_old_cached_analysis_never_starts_new_concurrence(tmp_path, monkeypatch):
    original = report()
    monkeypatch.setattr(gate, "analyze", lambda *a, **k: advice(original, old=True))
    reviewer = Reviewer()
    result = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    assert not reviewer.calls
    assert result["grok_review"]["repair_status"] == "CACHED_REVIEW_TOO_OLD_FOR_REPAIR"


def test_partial_opinion_is_visible_but_cannot_authorize_repair(tmp_path, monkeypatch):
    original = report()
    partial = advice(original)
    for critique in partial["result"]["critiques"]:
        critique.update(actionability=False, review_validation="PARTIAL")
    monkeypatch.setattr(gate, "analyze", lambda *a, **k: partial)
    reviewer = Reviewer()
    result = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    assert not reviewer.calls
    assert result["recommendations"] == original["recommendations"]


def test_unknown_failed_concurrence_never_retried(tmp_path, monkeypatch):
    original = report()
    monkeypatch.setattr(gate, "analyze", lambda *a, **k: advice(original))

    class Failing:
        count = 0

        def review(self, packet):
            self.count += 1
            raise RuntimeError("must not escape")

    reviewer = Failing()
    first = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    second = gate.review_and_repair(tmp_path, original, {}, reviewer=reviewer, clock=lambda: NOW)
    assert reviewer.count == 1
    assert first["grok_review"]["concurrence"]["status"] == "UNAVAILABLE"
    assert second["grok_review"]["concurrence"]["cached"]
    assert first["recommendations"] == second["recommendations"] == original["recommendations"]


def test_questioned_leg_is_rechecked_after_independent_latency(tmp_path, monkeypatch):
    from goal_analysis.engine import grok_repair

    original = report()
    monkeypatch.setattr(gate, "analyze", lambda *a, **k: advice(original))
    time = [NOW]
    calls = []

    class Slow(Reviewer):
        def review(self, packet):
            result = super().review(packet)
            time[0] = NOW + timedelta(hours=4)
            return result

    def repair(data, options, confirmed, now):
        calls.append(now)
        assert now == NOW + timedelta(hours=4)
        return data, {"changed_profiles": []}

    monkeypatch.setattr(grok_repair, "repair", repair)
    result = gate.review_and_repair(tmp_path, original, {}, reviewer=Slow(), clock=lambda: time[0])
    assert calls
    assert result["grok_review"]["changed_profiles"] == []
