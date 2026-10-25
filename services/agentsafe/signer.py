"""Signers and verifiers behind one tiny interface.

KmsSigner / KmsVerifier  — production (ECC_NIST_P256, ECDSA_SHA_256). The executor
                            verifies through KMS too, so it needs no crypto library.
LocalSigner / LocalVerifier — offline demo / tests without KMS (needs `cryptography`).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

from ..common.config import repo_root, settings
from .jws import der_to_raw, raw_to_der

ALG = "ES256"
KMS_ALG = "ECDSA_SHA_256"


class Signer(Protocol):
    kid: str

    def sign(self, data: bytes) -> bytes: ...


class Verifier(Protocol):
    kid: str

    def verify(self, data: bytes, raw_sig: bytes) -> bool: ...


class KmsSigner:
    def __init__(self, key_id: str, kms=None):
        from ..common.aws import client

        self.kid = key_id
        self._kms = kms or client("kms")

    def sign(self, data: bytes) -> bytes:
        der = self._kms.sign(KeyId=self.kid, Message=data, MessageType="RAW", SigningAlgorithm=KMS_ALG)["Signature"]
        return der_to_raw(der)


class KmsVerifier:
    def __init__(self, key_id: str, kms=None):
        from ..common.aws import client

        self.kid = key_id
        self._kms = kms or client("kms")

    def verify(self, data: bytes, raw_sig: bytes) -> bool:
        try:
            der = raw_to_der(raw_sig)
        except ValueError:
            return False
        try:
            return bool(
                self._kms.verify(KeyId=self.kid, Message=data, MessageType="RAW", Signature=der,
                                 SigningAlgorithm=KMS_ALG)["SignatureValid"]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] in {"KMSInvalidSignatureException", "InvalidSignatureException"}:
                return False
            raise


class LocalSigner:
    """EC P-256 key on disk. Only for offline runs; never deployed."""

    def __init__(self, pem_path: str | Path | None = None):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        self._ec, self._hashes, self._ser = ec, hashes, serialization
        path = Path(pem_path) if pem_path else repo_root() / "demo" / "state" / "signing_key.pem"
        if path.exists():
            self._key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            )
        self.public_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
        self.kid = "local-" + hashlib.sha256(self.public_pem.encode()).hexdigest()[:16]

    def sign(self, data: bytes) -> bytes:
        der = self._key.sign(data, self._ec.ECDSA(self._hashes.SHA256()))
        return der_to_raw(der)

    def verifier(self) -> "LocalVerifier":
        return LocalVerifier(self.public_pem, self.kid)


class LocalVerifier:
    def __init__(self, public_pem: str, kid: str):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        self._ec, self._hashes = ec, hashes
        self._pub = serialization.load_pem_public_key(public_pem.encode())
        self.kid = kid

    def verify(self, data: bytes, raw_sig: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature

        try:
            self._pub.verify(raw_to_der(raw_sig), data, self._ec.ECDSA(self._hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            return False


_local: LocalSigner | None = None


def get_signer() -> Signer:
    if settings.kms_key_id:
        return KmsSigner(settings.kms_key_id)
    global _local
    if _local is None:
        _local = LocalSigner()
    return _local


def get_verifier() -> Verifier:
    if settings.kms_key_id:
        return KmsVerifier(settings.kms_key_id)
    global _local
    if _local is None:
        _local = LocalSigner()
    return _local.verifier()
