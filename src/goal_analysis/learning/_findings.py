"""Evidence-linked, non-executing proposals; language models cannot invent metrics."""

from ._metrics import digest

ACTIONS = {
    "WAIT_FOR_SAMPLE": ("MEDIUM", "Gyűjtsünk több megfigyelést",
                        "A kis minta alapján még ne módosítsuk a stratégiát; folytassuk az előre rögzített megfigyelést."),
    "COLLECT_ODDS": ("HIGH", "Pótoljuk a szorzóadatokat",
                     "Az eredményarány önmagában nem mutat nyereségességet. Őrizzük meg a ténylegesen elérhető árakat és forrásukat."),
    "CHECK_SHARED_EXPOSURE": ("MEDIUM", "Vizsgáljuk a közös meccskitettséget",
                              "Az ugyanarra a meccsre épülő választásokat ne tekintsük független bizonyítéknak; a tétet ez a jelentés nem módosítja."),
    "VALIDATE_CALIBRATION": ("MEDIUM", "Ellenőrizzük a modell kalibrációját",
                             "A rögzített modellbecsléseket külön, időrendhelyes mintán kell ellenőrizni. A kiválasztott tippek pontossága nem igazolja az összes előrejelzés kalibrációját."),
    "PRESERVE_VERSION_PROVENANCE": ("MEDIUM", "Őrizzük meg a jövőbeli verzióazonosítókat",
                                    "Az ismeretlen régi verziókat ne találjuk ki utólag. Az új ajánlások eredeti stratégia- és modellverziója maradjon visszakereshető."),
    "AUDIT_DATA_QUALITY": ("HIGH", "Ellenőrizzük a kizárt vagy hiányos adatokat",
                          "Az adatminőségi eltéréseket vizsgáljuk meg a hivatkozott helyi bizonyítékokból; a sérült feljegyzéseket ne javítsuk találgatással."),
    "REVIEW_LOSS_PATTERN": ("MEDIUM", "Vizsgáljuk meg a vesztes kimenetek mintázatát",
                            "A leíró veszteségmintát külön tesztelendő hipotézisként kezeljük, ne automatikus stratégiaváltási utasításként."),
}


def findings_and_proposals(evidence):
    findings, proposals = [], []

    def add(item, code, title, detail, action):
        identity = [code, item["evidence_id"]]
        findings.append({"finding_id": "finding-" + digest(identity)[:20], "code": code,
                         "severity": "WARNING", "title": title, "detail": detail,
                         "evidence_ids": [item["evidence_id"]]})
        priority, proposal_title, proposal_detail = ACTIONS[action]
        proposals.append({
            "recommendation_id": "proposal-" + digest([action, item["evidence_id"]])[:20],
            "action": action, "priority": priority, "title": proposal_title,
            "detail": proposal_detail, "status": "PROPOSAL_ONLY", "source": "deterministic",
            "evidence_ids": [item["evidence_id"]],
        })

    for item in evidence:
        values = item["metrics"]
        if item["kind"] == "SCOPE_SUMMARY" and values["tickets"]["total"]:
            if values["small_sample"]:
                add(item, "SMALL_SAMPLE", "Kicsi az eldöntött szelvények mintája",
                    "Harmincnál kevesebb eldöntött szelvény; stabil teljesítménykülönbség nem állapítható meg.", "WAIT_FOR_SAMPLE")
            if values["missing_odds_legs"]:
                add(item, "MISSING_ODDS", "Hiányos az eredeti szorzólefedettség",
                    "A találati arány mérhető, a szorzó nélküli ajánlások hozama nem számolható.", "COLLECT_ODDS")
            if values["shared_fixture_occurrences"]:
                add(item, "SHARED_FIXTURES", "Vannak közös meccsre épülő választások",
                    "A választások darabszáma nagyobb a különböző meccsek számánál; az eredmények nem független minták.", "CHECK_SHARED_EXPOSURE")
        elif item["kind"] == "CALIBRATION" and values["eligible_selections"]:
            add(item, "UNCALIBRATED_MODELS", "A modellkalibráció nem igazolt",
                "A Brier-mutató kizárólag az eredetileg mentett, kiválasztott tippekre vonatkozó leíró mérés.", "VALIDATE_CALIBRATION")
        elif item["kind"] == "VERSION" and item["label"] == "UNKNOWN_LEGACY":
            add(item, "UNKNOWN_LEGACY_VERSION", "Egyes régi ajánlások verziója ismeretlen",
                "Az eredeti pillanatkép nem rögzít stratégia-verziót; a mai kódból vagy dátumból nem következtetünk rá.", "PRESERVE_VERSION_PROVENANCE")
        elif item["kind"] in {"PROFILE", "MARKET"}:
            counts = values.get("tickets", values.get("legs", {}))
            if counts.get("decided", 0) >= 5 and counts.get("lost", 0) > counts.get("won", 0):
                add(item, "LOSS_PATTERN", "Vesztes kimenetek túlsúlya a csoportban",
                    "Leíró mintázat a bizonyítékban látható darabszámok alapján. Nem ok-okozati magyarázat és szorzók nélkül nem profitmutató.", "REVIEW_LOSS_PATTERN")
        elif item["kind"] == "DATA_QUALITY" and (values.get("invalid_records") or values.get("unverified_actual_prices")):
            add(item, "DATA_QUALITY", "Adatminőségi korlátot találtunk",
                "A hibás forrásokat a mérés kizárta. Ha régi, csak becsült árral rögzített tényleges fogadás van, az összesített nettó és ROI nem igazolható.", "AUDIT_DATA_QUALITY")
    return findings, proposals


def review_packet(report):
    # Full-history measurement above is never truncated; only the LLM briefing is bounded.
    proposals = report["recommendations"][:80]
    ids = {identifier for p in proposals for identifier in p["evidence_ids"]}
    evidence = [{k: v for k, v in item.items() if k != "source_refs"}
                for item in report["evidence"] if item["evidence_id"] in ids]
    return {"as_of": report["as_of"], "coverage": report["coverage"],
            "evidence": evidence, "proposal_options": proposals,
            "briefing_truncated": len(proposals) < len(report["recommendations"]),
            "instructions": "Only prioritize supplied proposal IDs and cite their exact evidence IDs."}


def validate_priorities(payload, packet):
    if not isinstance(payload, dict) or set(payload) != {"priorities"}:
        raise ValueError("Invalid learning response schema")
    rows = payload["priorities"]
    if not isinstance(rows, list) or len(rows) > 8:
        raise ValueError("Invalid proposal count")
    allowed = {p["recommendation_id"]: p for p in packet["proposal_options"]}
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"recommendation_id", "priority", "evidence_ids"}:
            raise ValueError("Unknown learning fields")
        identifier = row["recommendation_id"]
        if identifier not in allowed or identifier in seen or row["priority"] not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("Unknown proposal or priority")
        if row["evidence_ids"] != allowed[identifier]["evidence_ids"]:
            raise ValueError("Unverified evidence reference")
        seen.add(identifier)
        result.append({**allowed[identifier], "source": "codex", "priority": row["priority"]})
    return result
