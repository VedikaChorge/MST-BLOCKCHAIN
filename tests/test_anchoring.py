import hashlib

import pytest

from conftest import approve, evidence_id_from, logout, post, signup_and_login, upload
from fakes import FAKE_REGISTRANT

CONTENT = b"anchored work"
DIGEST = hashlib.sha256(CONTENT).hexdigest()
WALLET_TX = "0x" + "cd" * 32


@pytest.fixture
def evidence(app, client):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client, CONTENT))
    approve(app, eid)
    return eid


@pytest.mark.parametrize("status", ["pending", "under_review", "rejected", "needs_info"])
@pytest.mark.parametrize("route,data", [("server", {}), ("wallet", {"tx_hash": WALLET_TX})])
def test_anchor_evidence_not_approved_is_refused(app, client, chain, evidence, status, route, data):
    with app.db() as c:
        c.execute("UPDATE evidence SET review_status=? WHERE id=?", (status, evidence))
    chain.mine(WALLET_TX, DIGEST)

    page = post(client, f"/evidence/{evidence}/anchor/{route}", data, follow_redirects=True).data

    assert b"only after an admin approves" in page
    assert b"Confirmed on" not in page


def test_server_anchor_signer_configured_records_confirmed_transaction(client, evidence):
    post(client, f"/evidence/{evidence}/anchor/server")

    page = client.get(f"/evidence/{evidence}").data

    assert b"Confirmed on MST Coral Testnet" in page
    assert f"https://coral.mstscan.com/tx/0x{DIGEST}".encode() in page
    assert FAKE_REGISTRANT.encode() in page


def test_server_anchor_without_server_signer_records_nothing(client, chain, evidence):
    chain.server_signer = False

    response = post(client, f"/evidence/{evidence}/anchor/server", follow_redirects=True)

    assert b"Server signing is not configured" in response.data
    assert DIGEST not in chain.records


def test_server_anchor_digest_already_on_chain_is_refused(client, chain, evidence):
    chain.mine("0x" + "ee" * 32, DIGEST, registrant="0x" + "22" * 20)

    response = post(client, f"/evidence/{evidence}/anchor/server", follow_redirects=True)

    assert b"already registered on-chain" in response.data
    assert b"Not anchored" in response.data


def test_server_anchor_other_users_evidence_is_forbidden(client, evidence):
    logout(client)
    signup_and_login(client, "mallory")

    assert post(client, f"/evidence/{evidence}/anchor/server").status_code == 403


@pytest.mark.parametrize("tx_hash", ["", "0x1234", "not-a-hash", "0x" + "zz" * 32])
def test_wallet_anchor_malformed_tx_hash_is_rejected(client, evidence, tx_hash):
    response = post(client, f"/evidence/{evidence}/anchor/wallet", {"tx_hash": tx_hash}, follow_redirects=True)

    assert b"Invalid transaction hash" in response.data


def test_wallet_anchor_tx_registering_this_digest_is_confirmed(client, chain, evidence):
    chain.mine(WALLET_TX, DIGEST)

    page = post(client, f"/evidence/{evidence}/anchor/wallet", {"tx_hash": WALLET_TX}, follow_redirects=True).data

    assert b"Confirmed on MST Coral Testnet" in page


def test_wallet_anchor_tx_for_different_digest_is_marked_failed(client, chain, evidence):
    chain.mine(WALLET_TX, "ff" * 32)

    page = post(client, f"/evidence/{evidence}/anchor/wallet", {"tx_hash": WALLET_TX}, follow_redirects=True).data

    assert b"did not register this evidence" in page
    assert b"Confirmed on" not in page


def test_refresh_pending_anchor_after_mining_becomes_confirmed(client, chain, evidence):
    chain.pending.add(WALLET_TX)
    pending_page = post(client, f"/evidence/{evidence}/anchor/wallet", {"tx_hash": WALLET_TX}, follow_redirects=True).data
    chain.mine(WALLET_TX, DIGEST)

    refreshed_page = post(client, f"/evidence/{evidence}/anchor/refresh", follow_redirects=True).data

    assert b"Pending confirmation" in pending_page
    assert b"Confirmed on MST Coral Testnet" in refreshed_page


def test_public_verify_anchored_evidence_shows_explorer_link_and_on_chain_time(client, evidence):
    post(client, f"/evidence/{evidence}/anchor/server")
    logout(client)

    page = client.get(f"/public/verify/{evidence}").data

    assert f"https://coral.mstscan.com/tx/0x{DIGEST}".encode() in page
    assert b"2026-01-01T00:00:00+00:00" in page
