"""Read-only N2 scope cardinality; never grants production publication rights."""
from collections import Counter

STATUS = 'NEW_PUBLICATION_SCOPED_DAILY_AND_ALL_AVAILABLE_PROFILE_SOURCE_USE_VERIFIED'

def verify(report, profiles, coverage):
    daily=[t for t in report['tickets'] if t.get('profile_id')=='daily223']
    assert len(daily)==1 and daily[0]['status']=='READY'
    assert len(daily[0]['legs'])==3
    assert all(t.get('status')=='READY' for t in report['tickets'])
    assert profiles['all_available_final_profile_legs_checked'] is True
    assert profiles['pending_or_abstained_cards_disclosed'] is True
    assert coverage['explicit_accepted_active_policy_verified'] is True
    assert coverage['no_trigger_is_not_new_capacity'] is True
    assert not coverage['observed_primary_capacity_gain'] or coverage['observed_primary_added_fixture_ids']
    records={tuple(c['location']):c for c in profiles['cards']}
    empty=[];available=[]
    for group in ('tickets','recommendations','profile_proposals','preview_tickets'):
        for i,c in enumerate(report.get(group,[])):
            if c.get('profile_id')=='daily223':continue
            row=records[(group,i)]
            assert row['profile_id']==c['profile_id'] and row['card_status']==c['status']
            assert len(row['legs'])==len(c.get('legs',[]))
            if c.get('legs'):
                bound=all(l['status']=='SOURCE_BOUND' for l in row['legs'])
                assert bound==row['all_final_legs_source_bound']
                if c['status'] in {'READY','MODEL_READY','REVIEWED'} or c.get('playable'):
                    assert bound
                available.append({'location':[group,i],'profile_id':c['profile_id'],'status':c['status'],'all_legs_bound':bound})
            else:
                assert c['status'] not in {'READY','MODEL_READY','REVIEWED'} and not c.get('playable')
                assert c.get('abstention_reason') or c.get('quality_warnings')
                if group=='recommendations':empty.append({'profile_id':c['profile_id'],'status':c['status'],'reason_code':c.get('reason_code'),'abstention_reason':c.get('abstention_reason'),'quality_warnings':c.get('quality_warnings')})
    return {'contract':'n2-source-use-with-explicit-honest-abstention-v1',
        'ready_daily_required':True,'ready_daily_legs':3,'all_available_profile_publication_guards_retained':True,
        'available_profile_cards':available,'empty_recommendations':empty,
        'derived_nonempty_output_required':False,
        'observed_derived_profiles':profiles['derived_final_source_bound_profiles'],
        'derived_profile_new_positive_use_proven':bool(profiles['derived_final_source_bound_profiles']),
        'fresh_primary_capacity_gain':coverage['observed_primary_capacity_gain'],
        'report_construction_status':report['construction_status'],
        'original_n2_no_trigger_clause_applies':not coverage['observed_primary_capacity_gain'],
        'production_changes':0,'new_generation_or_api_llm_calls':0}
