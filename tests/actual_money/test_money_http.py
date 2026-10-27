import http.client
import json
import socket
import sqlite3
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from .conftest import ROOT, allowed, dashboard, original
from .conftest import ledger as module

TOKEN = 'TESTONLY_LOCAL_TOKEN'
GROSS = 'BOOKMAKER_CONFIRMED_GROSS_SPORTS_RETURN'


@pytest.fixture
def context(tmp_path, monkeypatch):
    allowed(tmp_path)
    original_connect = sqlite3.connect
    def connect(database, *args, **kwargs):
        allowed(database)
        return original_connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    calls = []
    class Tracked(module.PortfolioLedger):
        def confirm_bookmaker_return(self, *args, **kwargs):
            calls.append((args, kwargs))
            return super().confirm_bookmaker_return(*args, **kwargs)
    ledger = Tracked(tmp_path / 'ledger.sqlite3', now=lambda: original.NOW)
    ledger.save_run(original.report(original.ticket()))
    ledger.record_wager('ticket-1', actual_combined_price=12.6)
    original.finish(ledger, {1: original.fixture(1, status='PST'), 2: original.fixture(2), 3: original.fixture(3)})
    ledger.record_void('ticket-1', 'api_football:1', 'TESTONLY bookmaker void')
    handler = dashboard.make_handler(SimpleNamespace(root=tmp_path, ledger=ledger), TOKEN)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    port = server.server_address[1]
    old_socket_connect = socket.socket.connect
    forbidden = []
    def network(sock, address):
        if address != ('127.0.0.1', port):
            forbidden.append('EXTERNAL_NETWORK')
            raise AssertionError('ONLY_OWN_EPHEMERAL_LOOPBACK')
        return old_socket_connect(sock, address)
    def block(*args, **kwargs):
        forbidden.append('PROVIDER_OR_DATABASE_CONSTRUCTOR')
        raise AssertionError(forbidden[-1])
    monkeypatch.setattr(socket.socket, 'connect', network)
    monkeypatch.setattr(urllib.request, 'urlopen', block)
    from goal_analysis.jobs.the_stats_context import Resources
    from goal_analysis.providers.the_stats_primary import TheStatsPrimaryClient
    from goal_analysis.storage import Database
    monkeypatch.setattr(Database, '__init__', block)
    monkeypatch.setattr(TheStatsPrimaryClient, '__init__', block)
    monkeypatch.setattr(Resources, '__init__', block)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(payload, token=TOKEN):
        body = json.dumps(payload).encode()
        client = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            client.request('POST', '/api/bookmaker-return', body, {'Content-Type':'application/json','X-Arthur-Token':token})
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()
    try:
        yield ledger, calls, post
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert forbidden == []


def payload(ledger):
    return {'ticket_id':'ticket-1','payout_eur':1.01,'note':'TESTONLY gross return','expected_settlement_hash':ledger.get_ticket('ticket-1')['settlement']['settlement_hash'],'payout_basis':GROSS}


@pytest.mark.parametrize('change', ['missing_token','malformed_token','missing_basis','net_basis'])
def test_bad_contract_rejected_before_ledger_call(context, change):
    ledger, calls, post = context
    body = payload(ledger)
    if change == 'missing_token':
        body.pop('expected_settlement_hash')
    if change == 'malformed_token':
        body['expected_settlement_hash']='bad'
    if change == 'missing_basis':
        body.pop('payout_basis')
    if change == 'net_basis':
        body['payout_basis']='NET_CASH_RETURN'
    status, _ = post(body)
    assert status == 400 and calls == []
    with ledger._connection() as db:
        assert db.execute('SELECT count(*) FROM portfolio_bookmaker_returns').fetchone()[0] == 0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_bookmaker_return_provenance'").fetchone()


def test_actual_HTTP_positive_gross_cent_return(context):
    ledger, calls, post = context
    status, view = post(payload(ledger))
    assert status == 200 and len(calls) == 1
    assert view['financial']['payout_eur'] == 1.01
    assert view['settlement']['bookmaker_return']['payout_basis'] == GROSS
    assert view['settlement']['bookmaker_return']['contract_version'] == 'bookmaker-gross-return-v1'


def test_actual_HTTP_stale_revision_409_no_money_or_schema(context):
    ledger, calls, post = context
    body = payload(ledger)
    ledger.record_void('ticket-1','api_football:2','TESTONLY later revision')
    status, result = post(body)
    assert status == 409 and 'SETTLEMENT_REVISION_CHANGED' in result['error'] and len(calls) == 1
    with ledger._connection() as db:
        assert db.execute('SELECT count(*) FROM portfolio_bookmaker_returns').fetchone()[0] == 0
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='portfolio_bookmaker_return_provenance'").fetchone()


def test_actual_HTTP_same_revision_repeat_idempotent(context):
    ledger, _calls, post = context
    body = payload(ledger)
    assert post(body)[0] == post(body)[0] == 200
    with ledger._connection() as db:
        assert db.execute('SELECT count(*) FROM portfolio_bookmaker_returns').fetchone()[0] == 1


def test_local_token_guard_before_money_call(context):
    ledger, calls, post = context
    assert post(payload(ledger), token='wrong')[0] == 403 and calls == []


def test_production_path_guard_before_constructor():
    with pytest.raises(AssertionError):
        allowed(ROOT)
        module.PortfolioLedger(ROOT / 'data/arthur/ledger.sqlite3')
