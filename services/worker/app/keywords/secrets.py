"""Envelope encryption for stored search query terms.

A search query is user-typed data. The keyed HMAC remains the join and dedup
key; the readable term is sealed with AES-256-GCM under an AAD bound to the
tenant, site, and key version, so a row lifted from one site cannot be decrypted
in the context of another.
"""

import hashlib
import secrets
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_TERM_LENGTH = 400


def query_aad(tenant_id: UUID, site_id: UUID, key_version: str) -> bytes:
    return f"{tenant_id}:{site_id}:search_query:{key_version}".encode()


def seal_query(
    key: bytes, tenant_id: UUID, site_id: UUID, key_version: str, term: str
) -> tuple[bytes, bytes, str]:
    if len(key) != 32:
        raise ValueError("invalid_search_query_key")
    nonce = secrets.token_bytes(12)
    aad = query_aad(tenant_id, site_id, key_version)
    ciphertext = AESGCM(key).encrypt(nonce, term.encode("utf-8"), aad)
    return ciphertext, nonce, hashlib.sha256(aad).hexdigest()


def open_query(
    key: bytes,
    tenant_id: UUID,
    site_id: UUID,
    key_version: str,
    nonce: bytes,
    ciphertext: bytes,
    aad_hash: str,
) -> str:
    aad = query_aad(tenant_id, site_id, key_version)
    if not secrets.compare_digest(hashlib.sha256(aad).hexdigest(), aad_hash):
        raise ValueError("search_query_aad_mismatch")
    return AESGCM(key).decrypt(nonce, ciphertext, aad).decode("utf-8")
