"""Own artifact checkpoint only; root independently closes task and GitHub later."""
from datetime import UTC, datetime
from pathlib import Path

import production_drill_v4 as drill


def verified(authority, output):
    authority.accepted_validation()
    authority.current_fingerprints()
    output = Path(output)
    accepted_hash = drill.sha(output / 'active-final-acceptance.json')
    proof = drill.read(output / 'production-drill-proof.json')
    runtime = drill.read(output / 'runtime-proof.json')
    hold = drill.read(output / 'next-step-scheduler-hold.json')
    data = drill.read(output / 'runtime-data-preservation.json')
    assert proof['acceptance_sha256'] == accepted_hash
    assert runtime['own_acceptance_sha256'] == accepted_hash
    assert runtime['own_drill_proof_sha256'] == drill.sha(output / 'production-drill-proof.json')
    assert runtime['status'] == 'OWN_VALIDATED_CONFIGURATION_DRILL_AND_EXISTING_REPORT_RUNTIME_RESTORED'
    assert runtime['serving_run_id'] == drill.REFERENCE_RUN
    assert runtime['serving_report_sha256'] == drill.REFERENCE_REPORT_SHA256
    assert runtime['daily_enabled'] and runtime['legacy_enabled'] is False
    assert runtime['source_and_4016_prior_ledger_rows_unchanged']
    assert runtime['learning_team_databases_unchanged'] and runtime['gateway_identity_preserved']
    assert runtime['both_drill_restores_and_runtime_same_root_runlock']
    assert hold['runtime_proof_sha256'] == drill.sha(output / 'runtime-proof.json')
    assert hold['daily_enabled'] is False and hold['legacy_enabled'] is False
    assert hold['same_root_runlock_as_drill_and_runtime']
    assert data['status'] == 'ALL_RAW_FILES_AND_COMMITTED_SQLITE_ROWS_UNCHANGED_AFTER_RUNTIME'
    assert data['data_after_runtime_sha256'] == drill.sha(output / 'production-data-after-runtime.json')
    for name in ('local', 'tailscale'):
        assert runtime['serving'][name]['run_id'] == drill.REFERENCE_RUN
        assert runtime['serving'][name]['report_sha256'] == drill.REFERENCE_REPORT_SHA256
        assert runtime['serving'][name]['http_status'] == 200
    return proof, runtime, hold, data


def write_locked(authority, lock, output):
    output, root = Path(output), Path(authority.ROOT)
    drill.require_lock(root, lock)
    proof, runtime, hold, data = verified(authority, output)
    facts = {
        'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'attempt': 'followup-v2',
        'task_state': 'OWN_SCOPE_VALIDATED_INDEPENDENT_ROOT_AND_GITHUB_PENDING',
        'full_tests_passed': 5776, 'targeted_tests_passed': 75,
        'original_cwd_regressions_passed': 15, 'adapter_tests_passed': 10,
        'actual_replays_passed': 3, 'checks_per_case': 8,
        'source_files': 1219, 'prior_ledger_rows_unchanged': 4016,
        'preservation_method': 'ALL_RAW_SQLITE_MAIN_AND_COMPLETE_WAL_BYTES_EXACT_PLUS_TRANSACTIONAL_SCHEMA_COUNTS',
        'operation_attempt': 'operations-v4',
        'initial_failed_full_preserved': {'passed': 5763, 'failed': 13},
        'test_only_cleanup_original37asserts_unchanged': True,
        'production_prior_current_restore': proof['restores'],
        'data_preservation': proof['data_preservation'],
        'data_preservation_after_runtime': data['comparison'],
        'existing_immutable_run_id': drill.REFERENCE_RUN,
        'existing_report_sha256': drill.REFERENCE_REPORT_SHA256,
        'new_generation_or_provider_or_llm_or_model_fit_for_operations': 0,
        'no_new_price_or_current_bettability_claim': True,
        'daily_on_restored_then_same_rootlock_off_for_next_source': True,
        'daily_enabled': False, 'legacy_enabled': False,
        'project_complete': False, 'root_and_github_verification_pending': True,
        'next_task_after_root_and_github': 'sensitivity_note',
        'evidence_sha256': {n: drill.sha(output / n) for n in (
            'active-final-acceptance.json', 'acceptance-independent-readback.json',
            'production-drill-proof.json', 'runtime-proof.json',
            'next-step-scheduler-hold.json', 'runtime-data-preservation.json')}}
    text = '\n'.join([
        '# 11.7 — saját konfigurációs rollback, üzemi adatmegőrzés és runtime', '',
        'Saját5776teljes/75célzott/15sorrendi regresszió/10adapter/3új tényleges numerikus replay×8 PASS. '
        'Az első5763PASS/13FAIL kör és eredeti XML/log/hash megmaradt. Két teszt cleanup-sora javítva; '
        'a termelési implementáció és mind37eredeti assert változatlan.', '',
        '| Státusz | Bizonyított eredmény vagy valódi maradék |', '|---|---|',
        '| 🟩 | A prior7.4.N6első7configbyte egyezését és2absence-t a valódi korábbi manifest bizonyítja. '
        'A prior/current modell- és livebeállítás azonos; nincs kitalált modellváltás. |',
        '| 🟩 | Egy RootRunLock alatt tényleges priorabsence→SHADOW és exactcurrentACTIVErestore. '
        'Szűk9configallowlist/hashbundle/CAS/atomicjournal/recovery/idempotencia. |',
        '| 🟩 | Minden felsorolt nyersdata/reportfájl exactSHA és minden SQLite main és teljesWAL byteSHA,committedschema/COUNT '
        'helyreállítás előtt/után, majd runtime után egyezik. Minden committedadatot a main+teljesWAL exactbytehash és tranzakciós leltár fed le; '
        'SHM/WAL/runtime-lock byte-immutabilitást nem állítunk. A tényleges olvasási korlátok és leltár a proofban. |',
        '| 🟩 | Dashboard saját elfogadott forrás/exact meglévőcc3brun/hash helyi+Tailscale, gateway/learning/team '
        'megőrzött; normálDailyONigazolt, majd sameRootLockOFFkövetkezőforrásponthoz,legacyOFF. |',
        '| 🟩 | Művelethez0sportsAPI/LLM/generálás/újfit. A korábbiREADY DAILY és tényleges GrokCOMPLETE '
        'nem újragenerált, árát/időpontját/metadataját nem írtuk át. |',
        '| 🟥 | Független root-visszaolvasás, tasklistcheckpoint és végleges GitHubpush/remoteSHA még kell. '
        'Az eredeti215követelmény friss teljes auditja és a valódi maradékok külön nyitottak. |', '',
        'CsakTheStatsélősport/odds; archívparserek megmaradtak. Eredeti2×2×3/math/rang/súly/küszöb/aranyhash/régiszelvény '
        'változatlan. Quoted_atnull valódi szolgáltatói quoteidő nélkül; saját receipt külön. '
        'Nincs új20–30szelvény-, adatelérhetőségi vagy prediktív nyereségállítás. '
        'Eredeti2026-10-24határidő nemteljesült;2026-10-26 17:00Berlin munkacél,nemgarancia.', '',
        '```json', __import__('json').dumps(facts, ensure_ascii=False, indent=2), '```', ''])
    with (output / 'REPORT.md').open('x', encoding='utf-8') as stream:
        stream.write(text)
    facts['report_sha256'] = drill.sha(output / 'REPORT.md')
    drill.save_new(output / 'scope-evidence-summary.json', facts)
    drill.save_new(output / 'protection-after.json', facts)
    drill.save_new(output / 'closure-complete.json', {
        'at': facts['at'], 'task': '11.7', 'attempt': 'followup-v2',
        'task_state': facts['task_state'],
        'checkpoint_sha256': drill.sha(output / 'protection-after.json'),
        'root_and_github_verification_pending': True, 'project_complete': False})
    return facts
