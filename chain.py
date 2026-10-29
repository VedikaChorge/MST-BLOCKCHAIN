"""MST blockchain access for anchoring evidence digests in the IPEvidenceRegistry contract."""
import os
from dataclasses import dataclass

from web3 import Web3
from web3.exceptions import TimeExhausted
from web3.logs import DISCARD

DEFAULT_NETWORK = "testnet"
NETWORKS = {
    "testnet": {"name": "MST Testnet", "chain_id": 91562037, "rpc_url": "https://testnetrpc.mstblockchain.com",
                "explorer_url": "https://testnet.mstscan.com", "currency": "tMSTC"},
    "coral-testnet": {"name": "MST Coral Testnet", "chain_id": 54635, "rpc_url": "https://coralrpc.mstblockchain.com",
                      "explorer_url": "https://coral.mstscan.com", "currency": "MST"},
    "mainnet": {"name": "MST Mainnet", "chain_id": 4646, "rpc_url": "https://4646.rpc.thirdweb.com",
                "explorer_url": "https://mstscan.com", "currency": "MST"},
}

REGISTRY_ABI = [
    {"type": "function", "name": "registerEvidence", "stateMutability": "nonpayable",
     "inputs": [{"name": "digest", "type": "bytes32"}, {"name": "referenceId", "type": "string"}], "outputs": []},
    {"type": "function", "name": "getEvidence", "stateMutability": "view",
     "inputs": [{"name": "digest", "type": "bytes32"}],
     "outputs": [{"name": "registrant", "type": "address"}, {"name": "recordedAt", "type": "uint64"},
                 {"name": "referenceId", "type": "string"}]},
    {"type": "function", "name": "exists", "stateMutability": "view",
     "inputs": [{"name": "digest", "type": "bytes32"}], "outputs": [{"name": "", "type": "bool"}]},
    {"type": "event", "name": "EvidenceRegistered", "anonymous": False,
     "inputs": [{"name": "digest", "type": "bytes32", "indexed": True},
                {"name": "registrant", "type": "address", "indexed": True},
                {"name": "recordedAt", "type": "uint64", "indexed": False},
                {"name": "referenceId", "type": "string", "indexed": False}]},
]


class ChainError(Exception):
    pass


@dataclass(frozen=True)
class Network:
    key: str
    name: str
    chain_id: int
    rpc_url: str
    explorer_url: str
    currency: str

    @classmethod
    def from_key(cls, key, **overrides):
        if key not in NETWORKS:
            raise ValueError(f"Unknown MST network '{key}'. Use one of: {', '.join(NETWORKS)}")
        settings = {**NETWORKS[key], **{k: v for k, v in overrides.items() if v}}
        return cls(key=key, **settings)

    def tx_url(self, tx_hash):
        return f"{self.explorer_url}/tx/{tx_hash}"

    def address_url(self, address):
        return f"{self.explorer_url}/address/{address}"


@dataclass(frozen=True)
class Anchor:
    tx_hash: str
    status: str
    registrant: str = ""
    recorded_at: int = 0
    block_number: int = 0


@dataclass(frozen=True)
class OnChainRecord:
    registrant: str
    recorded_at: int


@dataclass(frozen=True)
class PaymentCheck:
    tx_hash: str
    status: str
    payer: str = ""
    value_wei: int = 0


class MSTChain:
    def __init__(self, network, contract_address="", private_key="", receipt_timeout=120):
        self.network = network
        self.contract_address = Web3.to_checksum_address(contract_address) if contract_address else ""
        self._private_key = private_key
        self.receipt_timeout = receipt_timeout
        self._w3 = None

    @property
    def configured(self):
        return bool(self.contract_address)

    @property
    def server_signer(self):
        return self.configured and bool(self._private_key)

    def _contract(self, offline=False):
        if offline:
            return Web3().eth.contract(address=self.contract_address or None, abi=REGISTRY_ABI)
        if not self.configured:
            raise ChainError("No registry contract address is configured.")
        return self._connect().eth.contract(address=self.contract_address, abi=REGISTRY_ABI)

    def _connect(self):
        if self._w3 is None:
            w3 = Web3(Web3.HTTPProvider(self.network.rpc_url, request_kwargs={"timeout": 20}))
            actual = w3.eth.chain_id
            if actual != self.network.chain_id:
                raise ChainError(f"RPC returned chain ID {actual}, expected {self.network.chain_id} ({self.network.name}).")
            self._w3 = w3
        return self._w3

    def verify_payment(self, tx_hash, to, min_value_wei, data_hex, timeout=None):
        """Confirm tx_hash is a successful native-token transfer of at least min_value_wei to `to` carrying data_hex."""
        w3 = self._connect()
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout or self.receipt_timeout)
        except TimeExhausted:
            return PaymentCheck(tx_hash, "pending")
        tx = w3.eth.get_transaction(tx_hash)
        if (receipt["status"] != 1 or (tx.get("to") or "").lower() != to.lower() or tx["value"] < min_value_wei
                or bytes(tx["input"]) != bytes.fromhex(data_hex.removeprefix("0x"))):
            return PaymentCheck(tx_hash, "failed")
        return PaymentCheck(tx_hash, "confirmed", tx["from"], tx["value"])

    def calldata(self, digest_hex, reference):
        return self._contract(offline=True).encode_abi("registerEvidence", args=[bytes.fromhex(digest_hex), reference])

    def lookup(self, digest_hex):
        registrant, recorded_at, _ = self._contract().functions.getEvidence(bytes.fromhex(digest_hex)).call()
        return OnChainRecord(registrant, recorded_at) if recorded_at else None

    def register(self, digest_hex, reference):
        if not self.server_signer:
            raise ChainError("Server signing is not configured.")
        contract = self._contract()
        account = self._w3.eth.account.from_key(self._private_key)
        tx = contract.functions.registerEvidence(bytes.fromhex(digest_hex), reference).build_transaction({
            "from": account.address,
            "nonce": self._w3.eth.get_transaction_count(account.address, "pending"),
            "chainId": self.network.chain_id,
        })
        signed = account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction).to_0x_hex()
        return self.verify(tx_hash, digest_hex)

    def verify(self, tx_hash, digest_hex, timeout=None):
        """Confirm that tx_hash is a successful registry call that registered digest_hex."""
        contract = self._contract()
        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout or self.receipt_timeout)
        except TimeExhausted:
            return Anchor(tx_hash, "pending")
        if receipt["status"] != 1 or (receipt.get("to") or "").lower() != self.contract_address.lower():
            return Anchor(tx_hash, "failed")
        digest = bytes.fromhex(digest_hex)
        for event in contract.events.EvidenceRegistered().process_receipt(receipt, errors=DISCARD):
            if event["args"]["digest"] == digest:
                return Anchor(tx_hash, "confirmed", event["args"]["registrant"], event["args"]["recordedAt"],
                              receipt["blockNumber"])
        return Anchor(tx_hash, "failed")


def from_env():
    network = Network.from_key(
        os.getenv("MST_NETWORK", DEFAULT_NETWORK).lower(),
        rpc_url=os.getenv("MST_RPC_URL", ""),
        explorer_url=os.getenv("MST_EXPLORER_URL", "").rstrip("/"),
        currency=os.getenv("MST_CURRENCY_SYMBOL", ""),
    )
    address = os.getenv("CONTRACT_ADDRESS", "").strip()
    return MSTChain(network, address if Web3.is_address(address) else "", os.getenv("MST_SIGNER_PRIVATE_KEY", "").strip())
