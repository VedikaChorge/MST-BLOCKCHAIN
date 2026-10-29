import hashlib
import io

from conftest import evidence_id_from, logout, post, signup_and_login, upload

CONTENT = b"published artwork"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def test_lookup_known_hash_shows_earliest_local_record_and_on_chain_record(client, chain):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client, CONTENT, title="Artwork"))
    chain.mine("0x" + "ab" * 32, DIGEST)
    logout(client)

    page = client.get(f"/lookup?sha256={DIGEST.upper()}").data

    assert f'href="/public/verify/{eid}"'.encode() in page
    assert b"Registered on MST Coral Testnet" in page


def test_lookup_uploaded_file_hashes_file_before_searching(client):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client, CONTENT))
    logout(client)

    page = post(client, "/lookup", {"file": (io.BytesIO(CONTENT), "copy.txt")}, content_type="multipart/form-data").data

    assert DIGEST.encode() in page
    assert f'href="/public/verify/{eid}"'.encode() in page


def test_lookup_unknown_hash_reports_no_records(client):
    page = client.get(f"/lookup?sha256={'0' * 64}").data

    assert b"No evidence record" in page
    assert b"Not registered on" in page


def test_lookup_malformed_hash_is_rejected(client):
    page = client.get("/lookup?sha256=abc").data

    assert b"64 hexadecimal characters" in page
