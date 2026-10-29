"""TESTONLY dashboard projections; no provider, model, worker or production state."""

import json
import re
import shutil
import subprocess
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from goal_analysis import portfolio_dashboard as dashboard
from goal_analysis.config.portfolio import PROFILE_IDS, validate_settings

DAY = "2026-10-28"
OBSERVED = datetime(2026, 10, 28, 8, tzinfo=UTC)
SOURCE_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = Path("C:/AI-Work/GoalAnalysis").resolve()


@pytest.fixture(autouse=True)
def testonly_root(tmp_path):
    root = tmp_path.resolve()
    assert not root.is_relative_to(SOURCE_ROOT)
    assert not root.is_relative_to(PRODUCTION_ROOT)
    (root / "TESTONLY-GENERATED-DASHBOARD.json").write_text(json.dumps({
        "TESTONLY": True, "scope": "generated-ticket-dashboard",
    }), encoding="utf-8")


def generated_cards(count=30):
    return [{
        "ticket_id": f"TESTONLY-ticket-{index}", "profile_id": profile,
        "profile_name": f"TESTONLY profile {index}", "date": DAY,
        "created_at": OBSERVED.isoformat(), "combined_price": 16.0,
        "status": "READY" if index < 6 else "DRAFT",
        "publication_scope": "LIVE" if index < 6 else "PAPER_AUDITION",
        "playable": index < 6, "stake_eur": 1.0 if index < 6 else None,
        "quality_warnings": [] if index < 6 else ["Optional review unavailable"],
        "legs": [{
            "fixture_id": f"TESTONLY-fixture-{index}-{leg}",
            "home_team": f"TESTONLY Home {index}-{leg}",
            "away_team": f"TESTONLY Away {index}-{leg}",
            "kickoff": (OBSERVED + timedelta(hours=5)).isoformat(),
            "market_key": "totals_2_5", "selection_key": "over",
            "period": "FULL_TIME", "decimal_price": price,
            "quoted_at": OBSERVED.isoformat(),
        } for leg, price in enumerate((2.0, 2.0, 4.0))],
    } for index, profile in enumerate(PROFILE_IDS[:count])]


def generated_report(count=30):
    cards = generated_cards(count)
    return {
        "run_id": "TESTONLY-current", "date": DAY,
        "observed_at": OBSERVED.isoformat(),
        "settings": {"operating_contract_version": 2},
        "generated_tickets": cards,
        "tickets": [deepcopy(card) for card in cards if card["publication_scope"] == "LIVE"],
        "recommendations": [deepcopy(card) for card in cards[:2]],
        "generation_capacity": {
            "generated_count": count, "minimum": 15, "maximum": 30,
            "shortfall": max(0, 15 - count), "status": "MET" if count >= 15 else "SHORTFALL",
        },
    }


def projected_snapshot(report):
    # A deliberately stale ledger/recommendation view must never shrink, append
    # to, or replace the current canonical generated collection.
    stale = deepcopy(generated_cards(1)[0])
    stale.update(ticket_id="TESTONLY-stale-ticket", profile_name="TESTONLY stale card")
    stale["legs"][0]["home_team"] = "TESTONLY stale Home"
    result = {
        "selected_date": DAY, "latest": report, "history": [report],
        "tickets": report.get("tickets", []), "recommendations": [stale],
        "recommendation_archive": [stale], "freezes": [],
    }
    if "generated_tickets" in report:
        result["generated_tickets"] = report["generated_tickets"]
    if "generation_capacity" in report:
        result["generation_capacity"] = report["generation_capacity"]
    return result


@pytest.fixture
def snapshot_state(tmp_path, monkeypatch):
    from goal_analysis.agents import expert_registry
    from goal_analysis.jobs import expert_advisory, odds_monitor

    monkeypatch.setattr(expert_registry, "network_registry", lambda *args: [])
    monkeypatch.setattr(expert_advisory, "snapshot", lambda *args: {})
    monkeypatch.setattr(odds_monitor, "monitor_status", lambda *args: {})
    monkeypatch.setattr(dashboard, "automation_snapshot", lambda *args: {})
    monkeypatch.setattr(dashboard, "model_environment", dict)
    monkeypatch.setattr(dashboard, "codex_installed", lambda: False)
    state = dashboard.ArthurState.__new__(dashboard.ArthurState)
    state.root = tmp_path
    state.config_path = tmp_path / "absent-config.json"
    state.settings_path = tmp_path / "absent-settings.json"
    state.lock = threading.Lock()
    state.runtime = {"running": False, "last_started_at": None}
    state.team_snapshot = lambda *args: {}
    state.tipster_snapshot = lambda *args: {}
    state.learning_snapshot = lambda: {"learning": None, "learning_history": []}
    state.source_health = lambda *args: {}
    return state


@pytest.mark.parametrize("count", [0, 14, 30])
def test_status_uses_exact_current_generated_cards_and_capacity(snapshot_state, count):
    report = generated_report(count)
    original = deepcopy(report)
    ledger_rows = deepcopy(generated_cards(30))
    for card in ledger_rows:
        card.update(run_id="TESTONLY-older-run", active=True, played=False)
    snapshot_state.ledger = SimpleNamespace(
        list_runs=lambda **kwargs: [report],
        list_tickets=lambda **kwargs: deepcopy(ledger_rows),
        list_recommendations=lambda **kwargs: deepcopy(report["recommendations"]),
        list_freezes=lambda **kwargs: [],
    )

    for full_export in (False, True):
        view = snapshot_state.snapshot(DAY, full_export=full_export)
        assert view["generated_tickets"] == original["generated_tickets"]
        assert view["generation_capacity"] == original["generation_capacity"]
        assert view["latest"]["generated_tickets"] == original["generated_tickets"]
        assert all(ticket.get("publication_scope") == "LIVE" for ticket in view["tickets"])
        assert report == original


def test_old_two_card_report_does_not_acquire_current_generated_success(snapshot_state):
    cards = generated_cards(2)
    report = {
        "run_id": "TESTONLY-legacy", "date": DAY,
        "observed_at": OBSERVED.isoformat(), "tickets": cards,
        "settings": {"operating_contract_version": 1},
    }
    snapshot_state.ledger = SimpleNamespace(
        list_runs=lambda **kwargs: [report], list_tickets=lambda **kwargs: deepcopy(cards),
        list_recommendations=lambda **kwargs: [], list_freezes=lambda **kwargs: [],
    )
    view = snapshot_state.snapshot(DAY, full_export=False)
    assert "generated_tickets" not in view
    assert view.get("generation_capacity") is None
    assert len(view["tickets"]) == 2
    text = dashboard.german_ticket_text(view)
    assert text.count("TESTONLY Home 0-0") == 1
    assert text.count("TESTONLY Home 1-0") == 1


def test_german_text_uses_only_canonical_cards_once_with_scope_and_status():
    source = projected_snapshot(generated_report())
    original = deepcopy(source)
    text = dashboard.german_ticket_text(source)
    for card in source["generated_tickets"]:
        for leg in card["legs"]:
            assert text.count(leg["home_team"]) == 1
    assert "TESTONLY stale Home" not in text
    assert "DRAFT" in text and "READY" in text
    assert "PAPER" in text and "LIVE" in text
    assert source == original


def test_empty_canonical_collection_does_not_fall_back_to_stale_recommendations():
    source = projected_snapshot(generated_report(0))
    assert source["recommendations"]
    assert dashboard.german_ticket_text(source) == ""


def test_status_json_export_and_german_route_share_identical_card_set():
    source = projected_snapshot(generated_report())
    calls = []

    def snapshot(day, *, full_export=True):
        calls.append((day, full_export))
        return source if full_export else dashboard.compact_status_snapshot(source)

    state = SimpleNamespace(snapshot=snapshot, ledger=SimpleNamespace(feedback_summary=dict))
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(state, "TESTONLY-session"))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        prefix = f"http://127.0.0.1:{server.server_port}"
        with urlopen(prefix + f"/api/status?date={DAY}", timeout=5) as response:
            status = json.load(response)
        with urlopen(prefix + f"/api/export?date={DAY}", timeout=5) as response:
            exported = json.load(response)
        with urlopen(prefix + f"/api/tickets.txt?date={DAY}", timeout=5) as response:
            text = response.read().decode("utf-8")
            assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
        expected_ids = [card["ticket_id"] for card in source["generated_tickets"]]
        assert [card["ticket_id"] for card in status["generated_tickets"]] == expected_ids
        assert [card["ticket_id"] for card in exported["generated_tickets"]] == expected_ids
        assert exported["generation_capacity"] == source["generation_capacity"]
        assert exported["reports"][0]["generated_tickets"] == source["generated_tickets"]
        assert exported["tickets"] == source["tickets"]
        assert text == dashboard.german_ticket_text(source)
        assert calls == [(DAY, False), (DAY, True), (DAY, False)]
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.fixture
def start_state(tmp_path, monkeypatch):
    state = dashboard.ArthurState.__new__(dashboard.ArthurState)
    state.root = tmp_path
    state.config_path = tmp_path / "TESTONLY-config.json"
    state.settings_path = tmp_path / "TESTONLY-settings.json"
    state.lock = threading.Lock()
    state.runtime = {"running": False}
    dispatched = []

    class RecordedThread:
        def __init__(self, *, target, args, name, daemon):
            assert target == state._execute
            assert daemon is True
            self.command = args[0]

        def start(self):
            dispatched.append(self.command)

    monkeypatch.setattr(dashboard.threading, "Thread", RecordedThread)
    monkeypatch.setattr(dashboard, "validate_target_day", lambda *args: None)
    monkeypatch.setattr(dashboard, "load_settings", lambda path: validate_settings({}))
    return state, dispatched


@pytest.mark.parametrize("manual", [None, False])
@pytest.mark.parametrize("with_ids", [False, True])
def test_normal_current_run_ignores_implicit_legacy_league_selection(start_state, manual, with_ids):
    state, dispatched = start_state
    payload = {"date": DAY}
    if with_ids:
        payload["competition_ids"] = [39, 78, 135]
    if manual is not None:
        payload["manual_research"] = manual
    assert state.start("run", payload)["started"] is True
    assert len(dispatched) == 1
    assert "--competition-ids" not in dispatched[0]
    assert dispatched[0][dispatched[0].index("--ticket-count") + 1] == "15"


def test_explicit_manual_research_forwards_only_selected_competitions(start_state):
    state, dispatched = start_state
    state.start("run", {"date": DAY, "manual_research": True, "competition_ids": [39, 78]})
    command = dispatched[0]
    assert command[command.index("--competition-ids") + 1] == "39,78"


@pytest.mark.parametrize("manual", [None, 1, "true", [], {}])
def test_manual_research_flag_requires_real_boolean(start_state, manual):
    state, dispatched = start_state
    with pytest.raises(ValueError):
        state.start("run", {"date": DAY, "manual_research": manual, "competition_ids": [39]})
    assert dispatched == [] and not state.runtime["running"]


@pytest.mark.parametrize("selected", [None, [], [39, 39], [True], [0], [-1], ["39"], list(range(1, 42))])
def test_manual_research_requires_bounded_explicit_competition_ids(start_state, selected):
    state, dispatched = start_state
    with pytest.raises(ValueError):
        state.start("run", {"date": DAY, "manual_research": True, "competition_ids": selected})
    assert dispatched == [] and not state.runtime["running"]


def test_explicit_legacy_run_keeps_its_competition_scope(start_state, monkeypatch):
    state, dispatched = start_state
    monkeypatch.setattr(dashboard, "load_settings", lambda path: validate_settings({
        "operating_contract_version": 1,
    }))
    state.start("run", {"date": DAY, "competition_ids": [39, 78]})
    command = dispatched[0]
    assert command[command.index("--competition-ids") + 1] == "39,78"
    assert command[command.index("--ticket-count") + 1] == "2"


def run_pure_browser_functions(tmp_path, names, body, payload):
    """Run only named pure functions from the shipped JS, without a DOM or browser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Existing Node executable is unavailable")
    source = (SOURCE_ROOT / "src/goal_analysis/web/app.js").read_text(encoding="utf-8")
    declarations = []
    for name in names:
        match = re.search(
            rf"(?ms)^  function {re.escape(name)}\([^\n]*\) \{{.*?^  \}}", source
        )
        assert match is not None, f"Missing pure browser function: {name}"
        declarations.append(match.group(0))
    script = "\n".join(declarations) + "\n" + (
        'const input = JSON.parse(require("fs").readFileSync(0, "utf8"));\n' + body
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(payload), text=True,
        encoding="utf-8", capture_output=True, timeout=10, cwd=tmp_path, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_browser_canonical_selection_keeps_all_cards_and_protected_fields(tmp_path):
    source = projected_snapshot(generated_report())
    first = source["generated_tickets"][0]
    source["generated_ticket_states"] = [
        {"ticket_id": first["ticket_id"], "outcome": "LOST", "financial": {"net_eur": -1},
         "status": "FORGED", "publication_scope": "PAPER", "playable": False, "legs": []},
        {"ticket_id": "TESTONLY-unrelated", "outcome": "WON"},
    ]
    result = run_pure_browser_functions(tmp_path, ["canonicalTicketCards"], """
const before = JSON.stringify(input);
const cards = canonicalTicketCards(input, input.selected_date);
const empty = canonicalTicketCards({...input, generated_tickets: [],
  latest: {...input.latest, generated_tickets: []}}, input.selected_date);
const legacy = canonicalTicketCards({latest: {date: input.selected_date, tickets: input.tickets}},
  input.selected_date);
const stale = canonicalTicketCards(input, "2026-10-27");
console.log(JSON.stringify({cards, empty, legacy, stale, unchanged: before === JSON.stringify(input)}));
""", source)
    assert len(result["cards"]) == 30
    assert [card["ticket_id"] for card in result["cards"]] == [
        card["ticket_id"] for card in source["generated_tickets"]
    ]
    actual = result["cards"][0]
    assert actual["outcome"] == "LOST" and actual["financial"] == {"net_eur": -1}
    assert actual["status"] == first["status"]
    assert actual["publication_scope"] == first["publication_scope"]
    assert actual["playable"] == first["playable"] and actual["legs"] == first["legs"]
    assert result["empty"] == [] and result["legacy"] is None and result["stale"] is None
    assert result["unchanged"]


def test_browser_wager_admission_uses_stored_scope_status_and_playability(tmp_path):
    result = run_pure_browser_functions(tmp_path, ["wagerEligible"], """
const yes = wagerEligible(input.live, true);
const paper = wagerEligible({...input.live, publication_scope: "PAPER_AUDITION", playable: false}, true);
const draft = wagerEligible({...input.live, status: "DRAFT", playable: false}, true);
const missing = wagerEligible({ticket_id: "legacy", status: "READY"}, true);
const legacy = wagerEligible({ticket_id: "legacy", status: "DRAFT"}, false);
console.log(JSON.stringify({yes, paper, draft, missing, legacy}));
""", {"live": generated_cards(1)[0]})
    assert result == {"yes": True, "paper": False, "draft": False, "missing": False, "legacy": True}


def test_browser_normal_scope_avoids_implicit_selected_leagues(tmp_path):
    result = run_pure_browser_functions(tmp_path, ["runScopePayload"], """
const original = [...input.selected];
const normal = runScopePayload(input.current, false, input.selected);
const research = runScopePayload(input.current, true, input.selected);
const legacy = runScopePayload({operating_contract_version: 1}, false, input.selected);
console.log(JSON.stringify({normal, research, legacy, selected: input.selected, original}));
""", {"current": {"operating_contract_version": 2}, "selected": [39, 78]})
    assert result["normal"] == {}
    assert result["research"] == {"manual_research": True, "competition_ids": [39, 78]}
    assert result["legacy"]["competition_ids"] == [39, 78]
    assert result["selected"] == result["original"] == [39, 78]


def test_browser_generation_target_uses_verified_fifteen_card_minimum(tmp_path):
    result = run_pure_browser_functions(tmp_path, ["generationTargetNote"], """
const fourteen = generationTargetNote(input.short, 14);
const fifteen = generationTargetNote(input.met, 15);
const absent = generationTargetNote(null, 14);
const mismatched = generationTargetNote(input.short, 13);
const dishonest = generationTargetNote({...input.short, shortfall: 0}, 14);
console.log(JSON.stringify({fourteen, fifteen, absent, mismatched, dishonest}));
""", {
        "short": {"generated_count": 14, "minimum": 15, "maximum": 30,
                  "shortfall": 1, "status": "SHORTFALL"},
        "met": {"generated_count": 15, "minimum": 15, "maximum": 30,
                "shortfall": 0, "status": "MET"},
    })
    assert "nem teljesült" in result["fourteen"]
    assert "teljesült" in result["fifteen"] and "nem teljesült" not in result["fifteen"]
    for key in ("absent", "mismatched", "dishonest"):
        assert "nem ellenőrizhető" in result[key]


@pytest.mark.parametrize('count', [14, 15])
def test_browser_v3_objectives_use_generated_range_and_count_not_live_target(tmp_path, count):
    result = run_pure_browser_functions(tmp_path, ['generationTargetNote', 'capacityScopeCard'], """
function el(tag, css, text) { return {text: text || '', children: []}; }
function append(parent, ...children) { parent.children.push(...children); return parent; }
function badge(status, text) { return {text, children: []}; }
function number(value) { return String(value); }
function contents(node) { return [node.text, ...node.children.map(contents)].join(' '); }
const before = JSON.stringify(input);
const objectiveOnly = contents(capacityScopeCard(input));
const canonical = contents(capacityScopeCard(input, {
  generated_count: input.generated_count, minimum: 15, maximum: 30,
  shortfall: input.capacity_shortfall}, Array(input.generated_count).fill({status: 'DRAFT'})));
console.log(JSON.stringify({objectiveOnly, canonical, unchanged: JSON.stringify(input) === before}));
""", {'version': 'arthur-portfolio-objectives-v3-generated-canonical',
      'generated_count': count, 'ready_live_recommendations': 0, 'ready_paper_recommendations': 0,
      'target_generated_range': {'minimum': 15, 'maximum': 30},
      'capacity_shortfall': max(0, 15 - count)})
    for text in (result['objectiveOnly'], result['canonical']):
        assert f'{count} teljes szelvény' in text
        assert 'Napi generálási cél' in text and 'generálási cél 15–30' in text
        assert 'LIVE cél' not in text and 'LIVE ajánlat hiányzik' not in text
        assert ('nem teljesült' in text) is (count == 14)
    assert result['unchanged']
