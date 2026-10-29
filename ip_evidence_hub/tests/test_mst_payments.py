import pytest
from web3 import Web3

from chain import Network
from conftest import ADMIN_PASSWORD, login, logout, post, signup_and_login

PROJECT_WALLET = "0x" + "77" * 20
EVIDENCE_PRICE_WEI = Web3.to_wei(1, "ether")
TX = "0x" + "a1" * 32


@pytest.fixture
def mst(app, chain, monkeypatch):
    chain.network = Network.from_key("testnet")
    monkeypatch.setattr(app, "MST_PAYMENT_ADDRESS", PROJECT_WALLET)
    monkeypatch.setitem(app.MST_PRICES, "evidence", EVIDENCE_PRICE_WEI)
    return chain


def start(client, kind="evidence"):
    response = post(client, "/billing/checkout/mst", {"kind": kind})
    return response.headers["Location"].rstrip("/").split("/")[-1]


def reference_data(reference):
    return "0x" + reference.encode().hex()


def submit(client, reference, tx_hash=TX, **kw):
    return post(client, f"/billing/checkout/{reference}/mst", {"tx_hash": tx_hash}, **kw)


def credits(app, username="alice"):
    with app.db() as c:
        return c.execute("SELECT evidence_credits FROM users WHERE username=?", (username,)).fetchone()["evidence_credits"]


def test_billing_mst_enabled_offers_metamask_payment_in_tmstc(client, mst):
    signup_and_login(client, "alice")

    page = client.get("/billing").data

    assert b"Pay with MetaMask" in page
    assert b"1 tMSTC" in page


@pytest.mark.parametrize("network,address", [("mainnet", PROJECT_WALLET), ("coral-testnet", PROJECT_WALLET), ("testnet", "")])
def test_mst_payments_outside_testnet_or_without_wallet_are_unavailable(app, client, chain, monkeypatch, network, address):
    chain.network = Network.from_key(network)
    monkeypatch.setattr(app, "MST_PAYMENT_ADDRESS", address)
    signup_and_login(client, "alice")

    page = client.get("/billing").data
    response = post(client, "/billing/checkout/mst", {"kind": "evidence"})

    assert b"Pay with MetaMask" not in page
    assert response.status_code == 400


def test_mst_checkout_page_prepares_metamask_transfer_with_reference(client, mst):
    signup_and_login(client, "alice")
    reference = start(client)

    page = client.get(f"/billing/checkout/{reference}").data

    assert f'data-to="{PROJECT_WALLET}"'.encode() in page
    assert f'data-value="{hex(EVIDENCE_PRICE_WEI)}"'.encode() in page
    assert f'data-data="{reference_data(reference)}"'.encode() in page
    assert b'data-chain-id="91562037"' in page


def test_mst_payment_valid_transfer_marks_paid_and_grants_credit(app, client, mst):
    signup_and_login(client, "alice")
    reference = start(client)
    mst.transfer(TX, PROJECT_WALLET, EVIDENCE_PRICE_WEI, reference_data(reference))

    page = submit(client, reference, follow_redirects=True).data

    assert credits(app) == 1
    assert b"pay-paid" in page
    assert f"https://testnet.mstscan.com/tx/{TX}".encode() in page


@pytest.mark.parametrize("to,value,data", [
    ("0x" + "88" * 20, EVIDENCE_PRICE_WEI, None),
    (PROJECT_WALLET, EVIDENCE_PRICE_WEI - 1, None),
    (PROJECT_WALLET, EVIDENCE_PRICE_WEI, "0x" + b"pay_someone_else".hex()),
])
def test_mst_payment_wrong_recipient_amount_or_reference_grants_nothing(app, client, mst, to, value, data):
    signup_and_login(client, "alice")
    reference = start(client)
    mst.transfer(TX, to, value, data or reference_data(reference))

    page = submit(client, reference, follow_redirects=True).data

    assert b"does not match this payment" in page
    assert credits(app) == 0


@pytest.mark.parametrize("tx_hash", ["", "0x1234", "not-a-hash"])
def test_mst_payment_malformed_tx_hash_is_rejected(app, client, mst, tx_hash):
    signup_and_login(client, "alice")
    reference = start(client)

    page = submit(client, reference, tx_hash, follow_redirects=True).data

    assert b"Invalid transaction hash" in page
    assert credits(app) == 0


def test_mst_payment_pending_transaction_is_credited_after_refresh(app, client, mst):
    signup_and_login(client, "alice")
    reference = start(client)
    mst.pending.add(TX)

    pending_page = submit(client, reference, follow_redirects=True).data
    mst.transfer(TX, PROJECT_WALLET, EVIDENCE_PRICE_WEI, reference_data(reference))
    post(client, f"/billing/checkout/{reference}/mst/refresh")

    assert b"waiting for confirmation" in pending_page
    assert credits(app) == 1


def test_mst_payment_transaction_already_used_for_another_payment_is_refused(app, client, mst):
    signup_and_login(client, "alice")
    first = start(client)
    mst.transfer(TX, PROJECT_WALLET, EVIDENCE_PRICE_WEI, reference_data(first))
    submit(client, first)
    logout(client)
    signup_and_login(client, "mallory")
    second = start(client)

    page = submit(client, second, follow_redirects=True).data

    assert b"already been used" in page
    assert credits(app, "mallory") == 0


def test_admin_payments_lists_mst_payment_with_token_amount(app, client, mst):
    signup_and_login(client, "alice")
    reference = start(client)
    mst.transfer(TX, PROJECT_WALLET, EVIDENCE_PRICE_WEI, reference_data(reference))
    submit(client, reference)
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)

    page = client.get("/admin/payments").data

    assert b"Received on MST Testnet: 1 tMSTC" in page
    assert f"https://testnet.mstscan.com/tx/{TX}".encode() in page
