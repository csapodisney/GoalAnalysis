"""Prepare a root-verified scope report, then record only a verified Git push.

The preparation step does not mark root progress complete. Completion requires
local HEAD == the exact existing branch's remote SHA and accepted source blobs.
All older failed and successful evidence remains immutable.
"""
from datetime import UTC, datetime
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import acceptance_authority as authority
import production_drill_v4 as drill
from goal_analysis.jobs.portfolio import RunLock

BRANCH = 'build/token-efficient-pipeline'


def own_root_gate():
    authority.accepted_validation()
    if not (HERE / 'closure-root-readback.json').exists():
        raise RuntimeError('OWN_INDEPENDENT_ROOT_READBACK_NOT_AVAILABLE')
    proof = drill.read(HERE / 'closure-root-readback.json')
    assert proof['status'] == 'OWN_FULL_CONFIG_JOURNALS_DATA_RUNTIME_AND_CHECKPOINT_PHYSICALLY_ROOT_VERIFIED'
    assert drill.read(HERE / 'independent-operations-v4-outcome.json')['exit_code'] == 0
    authority.current_fingerprints()
    return proof


def counts(raw):
    rows = re.findall(r'(?m)^\s*- \[([ x])\] \*\*(\d+(?:\.\d+)*(?:\.N\d+)?)([^\n]*)', raw)
    assert len(rows) == len({r[1] for r in rows}) == 167
    return rows, {'total': len(rows), 'checked': sum(r[0] == 'x' for r in rows),
                  'open': sum(r[0] == ' ' for r in rows)}


def prepare():
    proof = own_root_gate()
    root = Path(authority.ROOT)
    with RunLock(root / 'data/arthur/run.lock'):
        own_root_gate()
        assert not (HERE / 'root-scope-prepare.json').exists(), 'Already prepared; inspect rather than overwrite'
        road = root / 'docs/ARTHUR_EXECUTION_TASKLIST.md'
        before = road.read_bytes()
        _, old_counts = counts(before.decode('utf-8'))
        assert old_counts == {'total': 167, 'checked': 136, 'open': 31}
        needle = b'- [ ] **11.7 ROLLBACK:**'
        assert before.count(needle) == 1
        with (HERE / 'tasklist-before-root-closure.md').open('xb') as stream:
            stream.write(before)
        note = ('\n  - 11.7 own followup-v2 verified:5776full/75target/15cwdregressions/10adapter/3actualreplay8checks; '
                'real prior/current nine-config restore under one RootRunLock, raw files and committed SQLite data preserved; '
                'existing cc3b dashboard local+Tailscale/DailyON then same-lockOFF next sensitivity_note,legacyOFF. '
                'Original5763PASS/13FAIL evidence retained; no paid generation or math change.\n').encode()
        changed = before.replace(needle, b'- [x] **11.7 ROLLBACK:**', 1) + note
        rows, worklist = counts(changed.decode('utf-8'))
        assert worklist == {'total': 167, 'checked': 137, 'open': 30}
        summary = {
            'at': datetime.now(UTC).isoformat(), 'task': '11.7', 'attempt': 'followup-v2',
            'status': 'OWN_FULL_PRODUCTION_CONFIG_DATA_RUNTIME_AND_ROOT_READBACK_VERIFIED',
            'full_tests_passed': 5776, 'targeted_tests_passed': 75,
            'cwd_regressions_passed': 15, 'adapter_tests_passed': 10,
            'actual_replays_passed': 3, 'checks_per_case': 8,
            'frozen_source_files': 1219, 'prior_ledger_rows_unchanged': 4016,
            'old_archive_files_unchanged': proof['old_archives_exact'],
            'root_readback_sha256': drill.sha(HERE / 'closure-root-readback.json'),
            'actual_replays': proof['actual_replays'],
            'data_preservation': proof['data_preservation_root_current'],
            'existing_immutable_run_id': drill.REFERENCE_RUN,
            'existing_report_sha256': drill.REFERENCE_REPORT_SHA256,
            'new_provider_llm_fit_or_generation_for_operations': 0,
            'daily_on_restored_then_same_rootlock_off_next_source': True,
            'daily_enabled': False, 'legacy_enabled': False,
            'original_5763_pass_13_fail_attempt_preserved': True,
            'production_code_unchanged_in_test_cleanup': True, 'original37asserts_unchanged': True,
            'artifact_only_hash_kind_fix_operation_version': 'operations-v4',
            'actual_physical_inventory_committed_rows': 18148065,
            'complete_proof_method': 'ALL_RAW_SQLITE_MAIN_AND_COMPLETE_WAL_BYTES_EXACT_PLUS_TRANSACTIONAL_SCHEMA_COUNTS',
            'first_operation_stopped_before_config_or_bundle_write': True,
            'no_new_quote_or_bettability_claim': True, 'worklist': worklist,
            'fresh_original215_audit_complete': False, 'project_complete': False,
            'final_report_push_and_remote_sha_required': True}
        folder = root / 'docs/status/2026-10-26-configuration-rollback'
        text = '\n'.join([
            '# 11.7 — konfigurációs rollback saját hatóköre igazolt', '',
            'A saját5776teljes/75célzott/15regresszió/10adapter/3új numerikus replay×8 sikeres. '
            'A tényleges korábbi konfiguráció visszaállítása, majd az aktuális kilenc konfiguráció exact helyreállítása '
            'egy közös RootRunLock alatt történt. Minden korábbi nyersdata/report és committedSQLite-adat megőrzését '
            'előtte/utána/runtime után és külön root-visszaolvasás igazolja.', '',
            '| Státusz | Eredmény vagy valódi maradék |', '|---|---|',
            '| 🟩 | ValódiN6manifest:7azonos configbyte és2korábbi absence→SHADOW→currentACTIVE. '
            'A model/livebeállítások azonosak; nincs kitalált modellváltás. |',
            '| 🟩 | Allowlist/hashbundle/exactbyte+absence/CAS/atomicjournal/crashrecovery/idempotencia; '
            'pendingrestore blokkolja a normáljobot,CLIlockutánreload,dashboardsettingssharedlock. |',
            '| 🟩 | Fizikai configbundle/naplóreceipt/előtte-utána nyershash/SQLite main+teljesWAL exactbytehash és tranzakciós schema/COUNT '
            'és WALcommitted-adat egyezés; SHM/runtime-lock koordinációsbájtok változatlanságát és per-rowhashszámítást nem állítjuk. |',
            '| 🟩 | Dashboard elfogadottkód/exactMEGLÉVŐcc3brun/hash helyi+Tailscale,gateway/learning/team megőrzött. '
            'DailyONigazolt,majd sameRootLockOFFsensitivity_note-hoz;legacyOFF.0újpaidAPI/LLM/generálás/fit a művelethez. |',
            '| 🟩 | Az első5763PASS/13FAIL XML/log/source megmaradt. Csak2TESZTcleanup-sor javult; '
            'mind37assert és a productionimplementáció változatlan. Az új5776sajátelfogadás külön bizonyíték. |',
            '| 🟥 | A teljes projekt és szülőfeladatok nem készültek el. A számozott munkalista137lezárt/30nyitott/167 '
            'fő-/alpont; ez nem az eredeti215követelmény friss teljes auditja. A régi160/55audit nem friss készültség. |',
            '| 🟥 | Következő sensitivity_note/biztonságos publikus fixture és az eredeti lista valódi maradékai. '
            'Időjárás/utazás/rotáció hiányok,1LIVE/0PAPER/19hiány a korábbi cc3bcsomagban,7/30napos későbbi mérés '
            'és történeti adat/PITkorlátok külön nyitottak. Nincs kapulazítás vagy garantált20–30szelvény-állítás. |', '',
            'CsakTheStatsélősport/odds;archívparserek megmaradtak. Eredeti2×2×3/math/rang/súly/küszöb/aranyhash/régiszelvény '
            'változatlan. Quoted_atnull valódi szolgáltatói idő nélkül,saját receipt külön. '
            'Eredeti2026-10-24határidő nemteljesült;2026-10-26 17:00Berlin munkacél,nemgarancia.', '',
            'A végleges forrás- és riportcommit távoliSHA-ját a push után külön visszaellenőrizzük. '
            'A JSON-bizonyíték minden zöld állítást a saját fizikai root-readbackhez köt.', '',
            '```json', json.dumps(summary, ensure_ascii=False, indent=2), '```', ''])
        (folder / 'REPORT.md').write_text(text, encoding='utf-8')
        (folder / 'README.md').write_text(text.split('```json')[0], encoding='utf-8')
        status = ['# Számozott végrehajtási munkalista — 137 lezárt / 30 nyitott / 167', '',
                  'Fő- és alpontok külön számolva; nem az eredeti215követelmény friss teljes auditja.', '',
                  '| ID | Státusz | Munkalistasor |', '|---|---|---|']
        status += [f'| {identifier} | {"🟩" if flag == "x" else "🟥"} | {(identifier + title).replace("**", "").replace("|", "/").strip()} |'
                   for flag, identifier, title in rows]
        (folder / 'STATUS-WORKLIST.md').write_text('\n'.join(status) + '\n', encoding='utf-8')
        (root / 'docs/validation/2026-10-26-configuration-rollback-closed.json').write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for name in drill.read(HERE / 'acceptance-plan.json')['changed_source']:
            target = HERE / 'source-snapshot' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            assert not target.exists()
            shutil.copyfile(root / name, target)
        road.write_bytes(changed)
        drill.save_new(HERE / 'root-scope-prepare.json', summary)
    print(json.dumps({'status': 'ROOT_SCOPE_REPORT_PREPARED_PUSH_REQUIRED', 'worklist': worklist}))


def record_push(remote_sha):
    proof = own_root_gate()
    root = Path(authority.ROOT)
    prepared = drill.read(HERE / 'root-scope-prepare.json')
    with RunLock(root / 'data/arthur/run.lock'):
        own_root_gate()
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=root, text=True).strip()
        remote = subprocess.check_output(['git', 'ls-remote', '--heads', 'origin', BRANCH], cwd=root, text=True).split()[0]
        assert re.fullmatch('[0-9a-f]{40}', remote_sha) and head == remote == remote_sha and branch == BRANCH
        for name in drill.read(HERE / 'acceptance-plan.json')['changed_source']:
            raw = subprocess.check_output(['git', 'cat-file', 'blob', head + ':' + name], cwd=root)
            assert hashlib_sha(raw) == drill.read(HERE / 'source-under-test-active.json')['files'][name]
        execution = root / 'artifacts/roadmap-execution-20261020'
        progress_path = execution / 'progress.json'
        progress = drill.read(progress_path)
        assert progress['active_task'] == '11.7' and '11.7' not in progress['completed_this_run']
        _, worklist = counts((root / 'docs/ARTHUR_EXECUTION_TASKLIST.md').read_text(encoding='utf-8'))
        assert worklist == {'total': 167, 'checked': 137, 'open': 30}
        with (HERE / 'progress-before-root-completion.json').open('xb') as stream:
            stream.write(progress_path.read_bytes())
        now = datetime.now(UTC).isoformat()
        done = {'at': now, 'task': '11.7', 'attempt': 'followup-v2', 'task_state': 'COMPLETE',
                'remote_sha': remote_sha, 'branch': branch, 'worklist': worklist,
                'root_readback_sha256': drill.sha(HERE / 'closure-root-readback.json'),
                'root_scope_prepare_sha256': drill.sha(HERE / 'root-scope-prepare.json'),
                'own_artifact_checkpoint_sha256': drill.sha(HERE / 'protection-after.json'),
                'project_complete': False, 'daily_enabled': False, 'legacy_enabled': False,
                'next_task': 'sensitivity_note'}
        drill.save_new(HERE / 'root-closure-complete.json', done)
        pointer = {'at': now, 'task': '11.7', 'attempt': 'followup-v2',
                   'evidence_root': str(HERE.relative_to(root)),
                   'root_closure_sha256': drill.sha(HERE / 'root-closure-complete.json')}
        drill.save_new(HERE / 'latest-complete-attempt.json', pointer)
        drill.save_new(HERE.parent / 'latest-complete-attempt.json', pointer)
        progress['completed_this_run'].append('11.7')
        progress.update(updated_at=now, active_task='sensitivity_note')
        progress['validated_scopes']['configuration_rollback_11_7'] = done
        progress['active_validation'] = {
            'task': 'sensitivity_note', 'phase': 'NEXT_ORIGINAL_SENSITIVITY_NOTE_AUDIT_NOT_STARTED',
            'source_frozen': False, 'daily_enabled': False, 'legacy_enabled': False,
            'previous_verified_scope': '11.7/followup-v2', 'previous_scope_remote_sha': remote_sha,
            'next_required': 'Audit actual saved sensitivity_note producer/consumer paths under original requirements; no fabricated notes or arbitrary rank/weights. Then safe public fixture and remaining original215 audit.'}
        progress['operational_status'].update(windows_daily_enabled=False, legacy_daily223_enabled=False,
            latest_health_evidence=str(HERE / 'closure-root-readback.json'),
            remaining_issue='Next original sensitivity_note audit; own configuration rollback scope verified and pushed.')
        progress['latest_configuration_rollback_closed_push'] = done
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        cont = execution / 'CONTINUATION.md'
        prefix = (f'## AKTUÁLIS — {now} — 11.7 SAJÁT ROLLBACK SCOPE ROOT/GITHUB LEZÁRVA; AKTÍV sensitivity_note\n\n'
                  f'Saját5776full/75target/15cwdregresszió/10adapter/3ÚJactualreplay×8,prior→currentconfigdrill/'
                  f'raw+committedSQLiteadatmegőrzés/runtime/exactMEGLÉVŐcc3b/local+Tailscale/DailyONthen sameRootLockOFF/legacyOFF '
                  f'ésfüggetlenroot/checkpoint igazolt. Scope/source/aktuális167tételes137lezárt30nyitottlista '
                  f'GitHub{remote_sha}remoteSHAvalzárt. Nemújgenerálás/quote/currentbettableclaim. '
                  'Első5763/13hibás kör és minden régi proof megmaradt;neindítsrégi11.7/Grok/N1/N2taskot. '
                  'Következő sensitivity_note→biztonságospublikusfixture→eredeti215frissteljesaudit/valódi maradék. '
                  'Parents/projectnemkész,régi160/55auditnemfriss;eredetihatáridőnemteljesült,26Oct17Berlinmunkacél.\n\n')
        cont.write_text(prefix + cont.read_text(encoding='utf-8'), encoding='utf-8')
    print(json.dumps(done))


def hashlib_sha(raw):
    return __import__('hashlib').sha256(raw).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true')
    group.add_argument('--record-push')
    options = parser.parse_args()
    prepare() if options.prepare else record_push(options.record_push)
