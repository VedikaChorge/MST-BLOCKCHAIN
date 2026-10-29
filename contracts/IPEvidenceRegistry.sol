// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IP Evidence Registry
/// @notice Anchors a SHA-256 digest and minimal metadata on an EVM-compatible chain.
/// Never store confidential files or personal data on-chain.
contract IPEvidenceRegistry {
    struct EvidenceRecord {
        address registrant;
        bytes32 digest;
        uint64 recordedAt;
        string referenceId;
    }

    mapping(bytes32 => EvidenceRecord) private records;
    event EvidenceRegistered(bytes32 indexed digest, address indexed registrant, uint64 recordedAt, string referenceId);

    error AlreadyRegistered();
    error EmptyDigest();
    error EmptyReference();

    function registerEvidence(bytes32 digest, string calldata referenceId) external {
        if (digest == bytes32(0)) revert EmptyDigest();
        if (bytes(referenceId).length == 0) revert EmptyReference();
        if (records[digest].recordedAt != 0) revert AlreadyRegistered();
        uint64 ts = uint64(block.timestamp);
        records[digest] = EvidenceRecord(msg.sender, digest, ts, referenceId);
        emit EvidenceRegistered(digest, msg.sender, ts, referenceId);
    }

    function getEvidence(bytes32 digest) external view returns (address registrant, uint64 recordedAt, string memory referenceId) {
        EvidenceRecord memory r = records[digest];
        return (r.registrant, r.recordedAt, r.referenceId);
    }

    function exists(bytes32 digest) external view returns (bool) {
        return records[digest].recordedAt != 0;
    }
}
