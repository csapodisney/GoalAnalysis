import json
from copy import deepcopy

from test_tipster_personas import NOW, enriched, model_results

from goal_analysis.jobs import nemotron_tickets


def configured(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "codex-advisory.json").write_text(
        '{"schema_version":1,"enabled":true}', encoding="utf-8")


def options_and_cards():
    options = {}
    profiles = list(nemotron_tickets.PROFILES)
    cards = __import__(
        "goal_analysis.tipsters", fromlist=["build_tipster_recommendations"]
    ).build_tipster_recommendations(
        enriched(), model_results(), {"enabled_profiles": profiles}, NOW,
        diversity_options=options,
    )
    return options, cards


def test_broader_local_search_does_not_expand_ai_request_menus():
    options, cards = options_and_cards()
    expanded = {}
    for profile, pool in options.items():
        if not pool:
            continue
        expanded[profile] = []
        for index in range(512):
            option = deepcopy(pool[0])
            for leg_index, leg in enumerate(option["recommendation"]["legs"]):
                leg["fixture_id"] = f"api_football:{9000000 + index * 10 + leg_index}"
            expanded[profile].append(option)
    before = deepcopy(expanded)
    menus = nemotron_tickets._menus(expanded, cards)
    tasks = nemotron_tickets._tasks(menus, "2026-10-11")
    assert len(tasks) == len(expanded)
    for task in tasks:
        packet = json.loads(task["prompt"].split("INPUT:\n", 1)[1])
        allowed = packet["allowed_options"]
        assert len(allowed) == 8
        assert len({item["option_id"] for item in allowed}) == 8
    assert expanded == before


def test_nemotron_can_only_choose_an_exact_prevalidated_option(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setattr(nemotron_tickets.NemotronAdvisor, "configured", staticmethod(lambda: True))
    options, cards = options_and_cards()

    def runner(path):
        tasks = json.loads(path.read_text("utf-8"))
        results = []
        for task in tasks:
            packet = json.loads(task["prompt"].split("INPUT:\n", 1)[1])
            results.append({"Name": task["name"], "Success": True,
                            "Answer": json.dumps({"option_id": packet["allowed_options"][0]["option_id"]})})
        return {"Results": results, "KnownPromptTokens": 12, "KnownCompletionTokens": 3,
                "UsageComplete": True}

    result = nemotron_tickets.curate(
        tmp_path, options, cards, "2026-10-11", NOW, runner=runner)
    assert result["status"] == "COMPLETE"
    assert set(result["selected"]) == {profile for profile, pool in options.items() if pool}
    styled = nemotron_tickets.apply(result, options, cards)
    for card in styled:
        if card["profile_id"] not in result["selected"]:
            assert card["status"] == "ABSTAINED"
            continue
        assert card["codex_style_selection"]["bounded_to_prevalidated_options"] is True
        assert len(options[card["profile_id"]]) == 1
        assert all(leg["estimated_edge"] >= 0 for leg in card["legs"])


def test_unknown_option_id_cannot_create_a_ticket(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setattr(nemotron_tickets.NemotronAdvisor, "configured", staticmethod(lambda: True))
    options, cards = options_and_cards()

    def runner(path):
        tasks = json.loads(path.read_text("utf-8"))
        return {"Results": [{"Name": task["name"], "Success": True,
                             "Answer": '{"option_id":"invented"}'} for task in tasks],
                "KnownPromptTokens": 1, "KnownCompletionTokens": 1, "UsageComplete": True}

    result = nemotron_tickets.curate(
        tmp_path, options, cards, "2026-10-11", NOW, runner=runner)
    assert result["status"] == "PARTIAL" and not result["selected"]
    assert nemotron_tickets.apply(result, options, cards) == cards


def test_transient_failure_is_not_cached_as_a_permanent_result(tmp_path, monkeypatch):
    configured(tmp_path)
    monkeypatch.setattr(nemotron_tickets.NemotronAdvisor, "configured", staticmethod(lambda: True))
    options, cards = options_and_cards()
    calls = []

    def runner(path):
        tasks = json.loads(path.read_text("utf-8"))
        calls.append(tasks)
        if len(calls) == 1:
            return {"Results": [{"Name": task["name"], "Success": False,
                                  "Answer": None} for task in tasks],
                    "KnownPromptTokens": 0, "KnownCompletionTokens": 0,
                    "UsageComplete": False}
        results = []
        for task in tasks:
            packet = json.loads(task["prompt"].split("INPUT:\n", 1)[1])
            results.append({"Name": task["name"], "Success": True,
                            "Answer": json.dumps({"option_id": packet["allowed_options"][0]["option_id"]})})
        return {"Results": results, "KnownPromptTokens": 12,
                "KnownCompletionTokens": 3, "UsageComplete": True}

    first = nemotron_tickets.curate(tmp_path, options, cards, "2026-10-11", NOW, runner=runner)
    second = nemotron_tickets.curate(tmp_path, options, cards, "2026-10-11", NOW, runner=runner)
    third = nemotron_tickets.curate(tmp_path, options, cards, "2026-10-11", NOW, runner=runner)
    assert first["status"] == "PARTIAL"
    assert second["status"] == third["status"] == "COMPLETE"
    assert len(calls) == 2
