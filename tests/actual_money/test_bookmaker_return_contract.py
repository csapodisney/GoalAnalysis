"""Genuine candidate-ledger SQLite controls, no money/production writes."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from goal_analysis.storage.portfolio_ledger import PortfolioLedger

from . import _money_fixtures as original
from .conftest import ROOT, allowed_root

GROSS = 'BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN'
NOTE = 'TESTONLY bookmaker gross return confirmation'


@pytest.fixture
def ledger(tmp_path):
    allowed_root(tmp_path)
    return PortfolioLedger(tmp_path / 'ledger.sqlite3', now=lambda: original.NOW)


def partial(ledger):
    ledger.save_run(original.report(original.ticket()))
    ledger.record_wager('ticket-1', actual_combined_price=12.6)
    original.finish(ledger, {1: original.fixture(1, status='PST'), 2: original.fixture(2), 3: original.fixture(3)})
    view = ledger.record_void('ticket-1', 'api_football:1', 'TESTONLY bookmaker void')
    assert view['outcome'] == 'REVIEW'
    return view['settlement']['settlement_hash']


def confirm(ledger, amount, token=None, note=NOTE):
    token = token or ledger.get_ticket('ticket-1')['settlement']['settlement_hash']
    return ledger.confirm_bookmaker_return('ticket-1', amount, note, expected_settlement_hash=token, payout_basis=GROSS)


def counts(ledger):
    with ledger._connection() as db:
        return (db.execute('SELECT count(*) FROM portfolio_bookmaker_returns').fetchone()[0],
                db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='portfolio_bookmaker_return_provenance'").fetchone()[0])


@pytest.mark.parametrize('amount', [1, 1.01, 4.99, 5.00, 30])
def test_stored_one_euro_gross_and_cent_exact_return(ledger, amount):
    token = partial(ledger)
    view = confirm(ledger, amount, token)
    assert view['financial']['payout_eur'] == amount
    assert view['financial']['net_eur'] == pytest.approx(amount - 1)
    assert view['settlement']['bookmaker_return']['settlement_hash'] == token
    assert view['settlement']['bookmaker_return']['payout_basis'] == GROSS
    assert view['settlement']['bookmaker_return']['fee_tax_inclusion'] == 'EXCLUDED'
    assert view['outcome'] == ('VOID' if amount == 1 else 'WON')


@pytest.mark.parametrize('amount', [.99, 0, -1, True, float('nan'), float('inf'), 1.001, 1.005, 1.009])
def test_invalid_or_substake_amount_no_money_or_lazy_schema(ledger, amount):
    partial(ledger)
    before = counts(ledger)
    with pytest.raises(ValueError):
        confirm(ledger, amount)
    assert counts(ledger) == before == (0, 0)


def test_original_thirty_euro_regression_body_and_assertions_unchanged(ledger):
    original.test_postponed_fixture_requires_bookmaker_void_confirmation(ledger)


def test_legacy_python_atomic_latest_keeps_basis_unspecified_no_lazy_DDL(ledger):
    partial(ledger)
    view = ledger.confirm_bookmaker_return('ticket-1', 30, NOTE)
    assert view['financial']['payout_eur'] == 30 and view['financial']['net_eur'] == 29
    assert view['settlement']['bookmaker_return']['payout_basis'] == 'LEGACY_REPORTED_RETURN_BASIS_UNSPECIFIED'
    assert counts(ledger) == (1, 0)


def test_constructor_and_GET_never_create_new_provenance_schema(ledger):
    partial(ledger)
    for _ in range(2):
        assert ledger.get_ticket('ticket-1')['bookmaker_return_contract']['minimum_gross_return_eur'] == 1
        PortfolioLedger(ledger.path).get_ticket('ticket-1')
    assert counts(ledger) == (0, 0)


def test_historical_five_euro_fixture_uses_stored_stake_not_current_one(ledger):
    partial(ledger)
    # Explicit TESTONLY historical seed; current record_wager(5) stays forbidden.
    with ledger._connection() as db:
        db.execute("UPDATE portfolio_wagers SET stake_eur='5' WHERE ticket_id='ticket-1'")
    with pytest.raises(ValueError, match='actual stored stake'):
        confirm(ledger, 4.99)
    assert counts(ledger) == (0, 0)
    view = confirm(ledger, 5)
    assert view['wager']['stake_eur'] == 5
    assert view['financial']['net_eur'] == 0


def test_stale_revision_zero_insert_and_zero_schema_creation(ledger):
    previous = partial(ledger)
    ledger.record_void('ticket-1', 'api_football:2', 'TESTONLY second void')
    current = ledger.get_ticket('ticket-1')['settlement']['settlement_hash']
    assert current != previous
    with pytest.raises(RuntimeError, match='SETTLEMENT_REVISION_CHANGED'):
        confirm(ledger, 2, previous)
    assert counts(ledger) == (0, 0)
    assert ledger.get_ticket('ticket-1')['settlement']['settlement_hash'] == current
    assert confirm(ledger, 2, current)['financial']['payout_eur'] == 2


def test_corrected_revision_does_not_reuse_previous_return(ledger):
    first = partial(ledger)
    confirm(ledger, 2, first)
    ledger.record_void('ticket-1', 'api_football:2', 'TESTONLY second void')
    changed = ledger.get_ticket('ticket-1')
    assert changed['settlement']['settlement_hash'] != first
    assert 'bookmaker_return' not in changed['settlement']
    assert changed['outcome'] == 'REVIEW'
    confirm(ledger, 3, changed['settlement']['settlement_hash'])
    assert counts(ledger) == (2, 1)
    with ledger._connection() as db:
        assert db.execute('SELECT payout_eur FROM portfolio_bookmaker_returns WHERE settlement_hash=?', (first,)).fetchone()[0] == '2'


def test_identical_and_concurrent_confirmations_are_idempotent(ledger):
    token = partial(ledger)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: confirm(ledger, 2, token), range(2)))
    assert rows[0]['financial'] == rows[1]['financial']
    assert counts(ledger) == (1, 1)


@pytest.mark.parametrize('amount,note', [(3, NOTE), (2, 'Different confirmation note')])
def test_conflicting_repeat_is_immutable(ledger, amount, note):
    partial(ledger)
    confirm(ledger, 2)
    with pytest.raises(ValueError, match='immutable'):
        confirm(ledger, amount, note=note)
    assert counts(ledger) == (1, 1)


@pytest.mark.parametrize('token,basis', [('bad', GROSS), ('f'*64, 'NET_CASH_RETURN'), (None, GROSS), ('a'*64, None)])
def test_typed_contract_requires_valid_revision_and_gross_basis(ledger, token, basis):
    partial(ledger)
    with pytest.raises(ValueError):
        ledger.confirm_bookmaker_return('ticket-1', 2, NOTE, expected_settlement_hash=token, payout_basis=basis)
    assert counts(ledger) == (0, 0)


def test_legacy_row_never_backfilled_by_GET_or_typed_reconfirmation(ledger):
    partial(ledger)
    ledger.confirm_bookmaker_return('ticket-1', 2, NOTE)
    with pytest.raises(ValueError, match='no automatic backfill'):
        confirm(ledger, 2)
    assert counts(ledger) == (1, 0)


def test_corrupt_existing_provenance_not_healthy_missing(ledger):
    partial(ledger)
    ledger.confirm_bookmaker_return('ticket-1', 2, NOTE)
    with ledger._connection() as db:
        db.execute('CREATE TABLE portfolio_bookmaker_return_provenance (wrong TEXT)')
    view = ledger.get_ticket('ticket-1')
    assert view['settlement']['bookmaker_return']['provenance_status'] == 'INVALID'
    assert view['settlement']['bookmaker_return']['payout_basis'] == 'INVALID_PROVENANCE'


def test_non_partial_void_and_no_actual_wager_rejected_without_newDDL(ledger):
    ledger.save_run(original.report(original.ticket()))
    original.finish(ledger)
    with pytest.raises(ValueError):
        confirm(ledger, 2)
    assert counts(ledger) == (0, 0)


def test_original_snapshot_report_and_ticket_JSON_immutable(ledger):
    partial(ledger)
    with ledger._connection() as db:
        before = [tuple(row) for row in db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_runs')]
        ticket = [tuple(row) for row in db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_tickets')]
        settlements = [tuple(row) for row in db.execute('SELECT settlement_hash,settlement_json FROM portfolio_settlements')]
    confirm(ledger, 2)
    with ledger._connection() as db:
        assert before == [tuple(row) for row in db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_runs')]
        assert ticket == [tuple(row) for row in db.execute('SELECT snapshot_json,snapshot_hash FROM portfolio_tickets')]
        assert settlements == [tuple(row) for row in db.execute('SELECT settlement_hash,settlement_json FROM portfolio_settlements')]


def test_production_root_rejected_before_constructor(isolated_io):
    attempted = []
    with pytest.raises(AssertionError):
        allowed_root(ROOT)
        attempted.append(PortfolioLedger(ROOT / 'data/arthur/ledger.sqlite3'))
    assert attempted == []


def test_provenance_update_delete_forbidden(ledger):
    partial(ledger)
    confirm(ledger, 2)
    for statement in ('UPDATE portfolio_bookmaker_return_provenance SET payout_basis=\'NET\'', 'DELETE FROM portfolio_bookmaker_return_provenance'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'), ledger._connection() as db:
            db.execute(statement)
