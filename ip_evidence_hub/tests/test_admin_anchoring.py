import hashlib

import pytest

from conftest import ADMIN_PASSWORD, approve, evidence_id_from, login, logout, post, signup_and_login, upload

CONTENT = b"admin anchored work"
DIGEST = hashlib.sha256(CONTENT).hexdigest()
WALLET_TX = "0x" + "cd" * 32


@pytest.fixture
def evidence(client):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client, CONTENT))
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)
    return eid


def test_admin_server_anchor_other_users_approved_evidence_is_confirmed(app, client, evidence):
    approve(app, evidence)
    detail = client.get(f"/evidence/{evidence}").data

    page = post(client, f"/evidence/{evidence}/anchor/server", follow_redirects=True).data

    assert b"Anchor with server wallet" in detail and b"Anchor with MetaMask" in detail
    assert b"Confirmed on MST Coral Testnet" in page


def test_admin_wallet_anchor_other_users_approved_evidence_is_confirmed(app, client, chain, evidence):
    approve(app, evidence)
    chain.mine(WALLET_TX, DIGEST)

    page = post(client, f"/evidence/{evidence}/anchor/wallet", {"tx_hash": WALLET_TX}, follow_redirects=True).data

    assert b"Confirmed on MST Coral Testnet" in page


def test_review_approval_with_server_signer_anchors_automatically(client, evidence):
    page = post(client, f"/admin/evidence/{evidence}/review", {"status": "approved"}, follow_redirects=True).data

    assert b"Confirmed on MST Coral Testnet" in page


@pytest.mark.parametrize("auto_anchor,server_signer", [(False, True), (True, False)])
def test_review_approval_without_auto_anchor_or_signer_leaves_evidence_unanchored(app, client, chain, evidence, monkeypatch, auto_anchor, server_signer):
    monkeypatch.setattr(app, "AUTO_ANCHOR_ON_APPROVAL", auto_anchor)
    chain.server_signer = server_signer

    page = post(client, f"/admin/evidence/{evidence}/review", {"status": "approved"}, follow_redirects=True).data

    assert b"Not anchored" in page
    assert DIGEST not in chain.records
