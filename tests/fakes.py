from chain import Anchor, Network, OnChainRecord, PaymentCheck

FAKE_REGISTRANT = "0x" + "11" * 20
FAKE_RECORDED_AT = 1767225600


class FakeChain:
    """In-memory stand-in for MSTChain that mimics the registry contract's behavior."""

    def __init__(self, server_signer=True):
        self.network = Network.from_key("coral-testnet")
        self.contract_address = "0x" + "ab" * 20
        self.configured = True
        self.server_signer = server_signer
        self.records = {}
        self.mined = {}
        self.pending = set()
        self.transfers = {}

    def calldata(self, digest_hex, reference):
        return "0xdeadbeef"

    def lookup(self, digest_hex):
        return self.records.get(digest_hex)

    def mine(self, tx_hash, digest_hex, registrant=FAKE_REGISTRANT):
        self.pending.discard(tx_hash)
        self.mined[tx_hash] = digest_hex
        self.records[digest_hex] = OnChainRecord(registrant, FAKE_RECORDED_AT)

    def register(self, digest_hex, reference):
        tx_hash = "0x" + digest_hex
        self.mine(tx_hash, digest_hex)
        return self.verify(tx_hash, digest_hex)

    def verify(self, tx_hash, digest_hex, timeout=None):
        if tx_hash in self.pending:
            return Anchor(tx_hash, "pending")
        if self.mined.get(tx_hash) != digest_hex:
            return Anchor(tx_hash, "failed")
        record = self.records[digest_hex]
        return Anchor(tx_hash, "confirmed", record.registrant, record.recorded_at, 42)

    def transfer(self, tx_hash, to, value_wei, data_hex, payer=FAKE_REGISTRANT):
        self.pending.discard(tx_hash)
        self.transfers[tx_hash] = (to.lower(), value_wei, data_hex.lower(), payer)

    def verify_payment(self, tx_hash, to, min_value_wei, data_hex, timeout=None):
        if tx_hash in self.pending:
            return PaymentCheck(tx_hash, "pending")
        sent = self.transfers.get(tx_hash)
        if not sent or sent[0] != to.lower() or sent[1] < min_value_wei or sent[2] != data_hex.lower():
            return PaymentCheck(tx_hash, "failed")
        return PaymentCheck(tx_hash, "confirmed", sent[3], sent[1])
