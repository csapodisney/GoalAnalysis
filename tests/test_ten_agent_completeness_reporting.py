"""Pure output-status regression: card quantity does not hide an interrupted owner."""

import pytest
import test_agent_portfolio as product_fixture

from goal_analysis.jobs.agent_portfolio import publish_agent_decisions


@pytest.mark.parametrize('interrupted', [True, False])
def test_daily_card_target_does_not_override_incomplete_owner_or_run(interrupted):
    report, _, _, _, snapshot, builder, proposals = product_fixture.inputs()
    options = {owner: [builder(owner, row, snapshot) for row in rows]
               for owner, rows in proposals.items()}
    report['real_wager_placed'] = False  # Explicit TESTONLY no-wager input.
    result = product_fixture.decisions(snapshot, options)
    result['status'] = 'PARTIAL'
    if interrupted:
        result['owners'][-1].update(status='INCOMPLETE_NO_RETRY', reason='TESTONLY interrupted owner')
        result['owners'][-1].pop('decision')
    publish_agent_decisions(report, result, report['settings'], product_fixture.NOW)
    assert len(report['generated_tickets']) == (18 if interrupted else 20)
    assert report['generation_capacity']['shortfall'] == 0
    assert report['construction_status'] == 'PARTIAL'
    assert report['ten_agent_decisions']['status'] == 'PARTIAL'
    assert report['tickets'] == [] and report['real_wager_placed'] is False
