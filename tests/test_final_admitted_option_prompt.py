"""Saved failure excerpts plus inert host; no external model or source access."""
import json
from copy import deepcopy

import pytest

from goal_analysis.agents.codex_review import CodexReviewer
from goal_analysis.agents.profile_decision import consider_family, set_options, start_sessions
from goal_analysis.agents.profile_decision_runner import profile_response_schema
from goal_analysis.jobs.agent_snapshot import OWNER_IDS, build_snapshot

# Exact contract-relevant saved excerpts; unrelated response rows are omitted.
# This fixture does not claim that a live model obeyed the corrected instruction.
SAVED_FAILURES = json.loads(r'''[
  {
    "reply_excerpt": {
      "agent_id": "kronikas",
      "phase": "FINAL",
      "session_id": "8761f76a3f02ff151f6d2d2a1578b7565eec806258c98c7f6ce5c082ea7f6b4b",
      "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
      "option_ids": [],
      "reason": "Abstain. I retain the supplied own PLAN's individual consideration of every target-day fixture. Its sole hypothesis, kronikas_o1, preserves candidate d223-28a681de59cbb50a214153e0 for Netherlands–Greece FT over2.5. Dutch pressure plus a Greek contribution had descriptive support, but containment and finishing failure left credible 1–1 or 2–0 countercases. Admission rejected it with CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN; no validated own options remain. Neither requested tool supplied option-level validation. I cannot repair this by inventing variants or substituting another owner's decision. Evidence below refers to the explicit own PLAN and supplied results; no fresh raw-source reading or independent gate reexecution is claimed. Research only; no wager, bankroll or READY authorization.",
      "family_uses": [
        {
          "family": "calendar",
          "option_ids": [
            "kronikas_o1"
          ],
          "reason": "Own PLAN records every supplied fixture separately and identifies Netherlands–Greece as scheduled. Completed, live, cancelled and postponed fixtures were excluded from its prospective construction.",
          "risk": "Scheduled status establishes eligibility context, not scoring support.",
          "status": "AVAILABLE_AND_CONSIDERED",
          "value_sha256": "0f3b75b8f81edab1749857e28933363a612e4e7e9bde4d5fcdfb03491b0626d5"
        }
      ],
      "tool_interpretations": []
    },
    "expected_error": "FOREIGN_FAMILY_OPTION",
    "rejected_options": [
      {
        "agent_id": "kronikas",
        "option_id": "kronikas_o1",
        "proposal_index": 0,
        "proposal_sha256": "3f2d34e12de97ad48ade78840f6afea44d90503f3e4d50eb2c254ce1eb380822",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-28a681de59cbb50a214153e0"
        ],
        "source_rows_sha256": "53ac16328d7ccf160718ac59b09efb17f5dd261c9bf25a5b552b2a8f0d1a8594",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      }
    ]
  },
  {
    "reply_excerpt": {
      "agent_id": "ritmusor",
      "phase": "FINAL",
      "session_id": "dbf0c61b67ce77807e4c71e6e88d7a3d45e91bae05686479399acad5b12d7b7e",
      "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
      "option_ids": [],
      "reason": "Tartózkodom. A saját PLAN minden napi fixture-re külön mérlegelést rögzített; ezeket fenntartom. Mindkét saját hipotézis REJECTED_OWN_OPTION, oka CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN; a validált saját opciók halmaza üres. Ez producer által jelentett kapueredmény, nem általam újrafuttatott ellenőrzés. Hollandia–Görögország esetén a kétoldali szezon-xG támogatott egy hipotézist, de a görög történeti ellenbizonyíték és a nem teljesített felülírási küszöb fennmaradt. Moldova–Kazahsztán esetén a kétoldali támadótermelés és védekezési sérülékenység mellett kis minta, szél és bizonytalan XI korlátozza az érvelést. Egyikhez sincs saját validált válaszgól-forgatókönyv vagy történeti mintavizsgálat. Az UNKNOWN nem negatív eredmény. A FINAL a megadott saját PLAN-ra és eredményekre támaszkodik; új nyersforrás-olvasást nem állítok. Nincs fogadási vagy READY felhatalmazás.",
      "family_uses": [
        {
          "family": "odds_and_price_history",
          "option_ids": [
            "ritmusor_btts_netherlands_greece",
            "ritmusor_btts_moldova_kazakhstan"
          ],
          "reason": "Mindkét saját hipotézis PLAN-jában quote_timestamp_status=MISSING és odds_movement=INSUFFICIENT_HISTORY.",
          "risk": "Árfrissesség és piaci megerősítés nem igazolt.",
          "status": "AVAILABLE_AND_CONSIDERED",
          "value_sha256": "d8035b208cce3b6b99c2a66057ea650631e6419895eab17a88b262c53f664baf"
        }
      ],
      "tool_interpretations": []
    },
    "expected_error": "FOREIGN_FAMILY_OPTION",
    "rejected_options": [
      {
        "agent_id": "ritmusor",
        "option_id": "ritmusor_btts_netherlands_greece",
        "proposal_index": 0,
        "proposal_sha256": "51e585dfd3202742b3ecbbcf906b0874f892ceb8113eeb26321b30ecb1fa22f3",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-9331418e06ca389a6464bd94"
        ],
        "source_rows_sha256": "c5d710eda11202bc58fa00d2e710a99cc24d62f43dc7bf7678ab91b06c86fbd0",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      },
      {
        "agent_id": "ritmusor",
        "option_id": "ritmusor_btts_moldova_kazakhstan",
        "proposal_index": 1,
        "proposal_sha256": "adc61a95aa07611aedd76530519aeff1663f360568ed30f36e30d6b22efdc299",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-4e8ed72035bf9d7cc39634ba"
        ],
        "source_rows_sha256": "77230395b451f26e70db5e51370d1dedae6361ba20d4f9e238e81b6abc1ec380",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      }
    ]
  },
  {
    "reply_excerpt": {
      "agent_id": "parharcmester",
      "phase": "FINAL",
      "session_id": "796c112235eb1970f996c67de800046eb7a29d0c09875ee87fbaecbab1d49704",
      "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
      "option_ids": [],
      "reason": "Tartózkodom. A saját PLAN mind a 72 napi mérkőzéshez rögzített mérlegelését fenntartom. Mindhárom saját hipotézist CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN indokkal elutasította a közölt befogadási kapu; nincs validált saját opció. Ez nem bizonyítja a hipotézisek hamisságát, de egyik sem választható. Svájcnál az xG-előnnyel a góltöbblet fenntarthatósága és a keretbizonytalanság, Skóciánál a gyenge helyzetkihasználás és sérülések, Németországnál a gyenge eredményminta és hiányzók állnak szemben. A két saját eszközeredmény nem adott opciószintű validációt. A FINAL értékelése a közölt saját PLAN-ra, befogadási állapotokra és eszközeredményekre támaszkodik; friss nyersforrás-olvasást nem állítok. Nincs ügylet-, bankroll- vagy READY-felhatalmazás.",
      "family_uses": [
        {
          "family": "retained_history",
          "option_ids": [
            "parharcmester_svajc_ft2",
            "parharcmester_skocia_ft1",
            "parharcmester_nemetorszag_ft2"
          ],
          "reason": "A PLAN svájci 4/6, skót 4/8 és német 3/10 oldalspecifikus történeti arányai rendre korlátozott támogatást, gyenge támogatást és ellenbizonyítékot jelentenek.",
          "risk": "Leíró gyakoriságok; historical_feature_availability_certified=false.",
          "status": "AVAILABLE_AND_CONSIDERED",
          "value_sha256": "003466cd34a8db5e60f0bbd1dc40b7abe17ace4d9a13e722e30eade8dc3ac01b"
        }
      ],
      "tool_interpretations": []
    },
    "expected_error": "FOREIGN_FAMILY_OPTION",
    "rejected_options": [
      {
        "agent_id": "parharcmester",
        "option_id": "parharcmester_svajc_ft2",
        "proposal_index": 0,
        "proposal_sha256": "b8c0a145e0c849c55e1b7a11d7d52b93e9c6586f5973e74c36927dc15815864a",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-06bc6d429840ba629d5e843e"
        ],
        "source_rows_sha256": "49f0ab3d16304d04e0991fb2f11d617909e190819eaf53c657fc3bdf366cd355",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      },
      {
        "agent_id": "parharcmester",
        "option_id": "parharcmester_skocia_ft1",
        "proposal_index": 1,
        "proposal_sha256": "9bb2ce99fce4b97d6fbfa6914e9049cc53138c9f78f149fb16663f4dfcefc408",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-12ca38ba012b140f4ee05c4a"
        ],
        "source_rows_sha256": "4be3e3ce309a65e9720b3ea0acb6b488abc5e5821e24e9b4943b8c7b94381aff",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      },
      {
        "agent_id": "parharcmester",
        "option_id": "parharcmester_nemetorszag_ft2",
        "proposal_index": 2,
        "proposal_sha256": "a2fbecb1f005c94c61c7470b36ee7d737dbe2ba84e2fda01564ae979e9d2e4dd",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-21f5e7dd978aa88c726ab2a3"
        ],
        "source_rows_sha256": "de8045f2dbf365fd5f4c4bc2d766dea3e94503c1b5b6d18eca3113e23a1e9a67",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      }
    ]
  },
  {
    "reply_excerpt": {
      "agent_id": "orszem",
      "phase": "FINAL",
      "session_id": "3472420aee229fed2f174ecf063533a2806761beb597b5d4155059066385e225",
      "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
      "option_ids": [],
      "reason": "Tartózkodom. A saját PLAN mind a 72 napi mérkőzéshez külön mérlegelést rögzített; ezt megőrzöm. Mindhárom saját javaslat elutasított: CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN; accepted_option_ids és validated_own_options üres. Ez a közölt producerkapu eredménye, nem általam újrafuttatott validáció. Andorra–Gibraltár gyenge támadási számai mellett a kis minta és a védekezési bizonytalanság fennmarad. Luxemburg–Észtország esetén Luxemburg védekezése és a minimum alatti producer-támogatás ellenérv. Liechtenstein–Azerbajdzsán esetén a vendég egyoldalú góltermelése törheti meg az undert. Egyik hipotézishez sincs saját sikeres forgatókönyv- vagy történeti validáció. A FINAL a mellékelt saját PLAN-t és eredményeket értelmezi; friss nyerssor-olvasást nem állítok. Nincs ügylet-, bankroll- vagy READY-felhatalmazás.",
      "family_uses": [
        {
          "family": "calendar",
          "option_ids": [],
          "reason": "A mellékelt saját PLAN minden napi fixture-t külön mérlegelt; a három hipotézis NS státuszú mérkőzésre vonatkozott.",
          "risk": null,
          "status": "AVAILABLE_AND_CONSIDERED",
          "value_sha256": "0f3b75b8f81edab1749857e28933363a612e4e7e9bde4d5fcdfb03491b0626d5"
        }
      ],
      "tool_interpretations": []
    },
    "expected_error": "VALUE_TO_OPTION_OR_RISK_LINK_REQUIRED",
    "rejected_options": [
      {
        "agent_id": "orszem",
        "option_id": "orszem-under-andorra-gibraltar",
        "proposal_index": 0,
        "proposal_sha256": "8bd46a0fac2cf56ab3ae88aaac980927477cada9d9ab4415eaa4cb9cc0984f98",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-1ba3e6d82cdab52621f41c29"
        ],
        "source_rows_sha256": "f7b1963198dfd0748209fa2f786968a0f4ad6d0a5d5a37623ee07dec4bd4b34b",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      },
      {
        "agent_id": "orszem",
        "option_id": "orszem-under-luxembourg-estonia",
        "proposal_index": 1,
        "proposal_sha256": "095a0573e046539bdff415795355b766c924035d94f5604270d0335b51f72ee3",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-ef3c6799b2f7e574673c7f87"
        ],
        "source_rows_sha256": "545071c62d774389dfd6db56fe0c49a496393ff3b6102f4a879d6617908ab764",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      },
      {
        "agent_id": "orszem",
        "option_id": "orszem-under-liechtenstein-azerbaijan",
        "proposal_index": 2,
        "proposal_sha256": "c8ae32aa0f31be596dff3ba4839e1c812f867795cecfe9c34c5de1625dd960a6",
        "reason": "CAUTIOUS_PRIMARY_ABSENCE_NOT_PROVEN",
        "reason_authority": "PRODUCER_REPORTED_EXISTING_ADMISSION_GATE",
        "snapshot_id": "acc9c9a42b7230660fca1c72120cd79741e427bfc7da6a23d6933be1f235c82d",
        "source_as_of": "2026-11-13T15:23:41.619120+00:00",
        "source_candidate_ids": [
          "d223-d926c935bf81b3f38d1fac0a"
        ],
        "source_rows_sha256": "f63d68d83ca48ba80e32264a4d2db414fdfce3793c2a96d96214dc2a759d389a",
        "status": "REJECTED_OWN_OPTION",
        "version": "own-option-admission-v1"
      }
    ]
  }
]''')


def _snapshot():
    return build_snapshot(
        run_id='TESTONLY-final-admission', target_date='2026-11-13',
        as_of='2026-11-13T12:00:00+00:00', authoritative_fixture_ids=['TESTONLY:1'],
        calendar_receipt={'target_date': '2026-11-13', 'scope': 'FULL_PROVIDER_DAY',
                          'status': 'COMPLETE', 'fixture_ids': ['TESTONLY:1'],
                          'source_refs': ['TESTONLY:calendar']},
        fixtures=[{'fixture_id': 'TESTONLY:1', 'home': 'A', 'away': 'B'}],
        family_values={name: {'status': 'AVAILABLE', 'value': {'fixture': 'TESTONLY:1'},
                             'source_refs': ['TESTONLY:source']}
                       for name in ('retained_history', 'odds_and_price_history')},
        source_manifest={'TESTONLY:calendar': 'a' * 64, 'TESTONLY:source': 'b' * 64})


@pytest.mark.parametrize('saved', SAVED_FAILURES,
                         ids=[row['reply_excerpt']['agent_id'] for row in SAVED_FAILURES])
def test_final_prompt_explains_saved_failure_without_repairing_model_output(saved):
    reply = deepcopy(saved['reply_excerpt'])
    request = {key: reply[key] for key in ('agent_id', 'session_id', 'snapshot_id', 'phase')}
    request['data'] = {'validated_own_options': [], 'own_option_admission': {
        'accepted_option_ids': [], 'rejected_options': deepcopy(saved['rejected_options'])}}
    reviewer = CodexReviewer.__new__(CodexReviewer)
    calls = []

    def inert(body, usage):
        calls.append(body)
        return {'output': [{'content': [{'type': 'output_text', 'text': json.dumps(reply)}]}]}

    reviewer._request = inert
    result = reviewer.request_profile_decision(request, timeout=1)
    assert len(calls) == 1
    instructions = calls[0]['instructions']
    assert ('every family_uses.option_ids may contain ONLY IDs present in data.validated_own_options'
            in instructions)
    assert ('discuss it only in reason or risk prose, never in either option_ids field'
            in instructions)
    assert 'risk must be a nonempty explanation' in instructions
    assert 'reason does not replace risk' in instructions
    assert 'each PLAN option is one complete accumulator ticket' in instructions
    assert 'No later automatic combination' in instructions
    assert 'Stop optional exploration before exhausting a cap.' in instructions
    assert calls[0]['text']['format']['schema'] == profile_response_schema('FINAL')
    assert json.loads(calls[0]['input']) == request
    assert {key: result[key] for key in reply} == reply

    # Rebind only the value hash to this independent pure TESTONLY snapshot.
    # Saved bad IDs/risk/status remain exact and must still fail strict admission.
    snapshot = _snapshot()
    state = next(row for row in start_sessions(
        snapshot, {owner: 'TESTONLY-method' for owner in OWNER_IDS})
        if row.agent_id == reply['agent_id'])
    state = set_options(state, snapshot, [])
    row = deepcopy(reply['family_uses'][0])
    row['value_sha256'] = snapshot.data()['families'][row['family']]['value_sha256']
    with pytest.raises(ValueError, match=saved['expected_error']):
        consider_family(state, snapshot, **row)
    corrected = {**row, 'option_ids': [],
                 'risk': 'TESTONLY: no admitted option; this family cannot support a selected ticket.'}
    updated = consider_family(state, snapshot, **corrected)
    assert json.loads(updated.family_uses_json)[row['family']]['option_ids'] == []
    assert updated.options_json == state.options_json == '[]'
