import base64
import hashlib
import json
from datetime import date
from uuid import UUID

import pytest
from app.gsc.consumer import (
    decode_encryption_key,
    decrypt_secret_payload,
    parse_cursor,
    require_secret_bytes,
    secret_aad,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TENANT = UUID("019d0000-0000-7000-8000-000000000011")
CONNECTOR = UUID("019d0000-0000-7000-8000-000000000061")


def test_database_envelope_decrypts_only_with_bound_aad() -> None:
    key = b"k" * 32
    nonce = b"n" * 12
    aad = secret_aad(TENANT, CONNECTOR, "google_search_console", "local-v1")
    plaintext = json.dumps({"access_token": "must-not-be-logged"}).encode()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)

    payload = decrypt_secret_payload(
        tenant_id=TENANT,
        connector_id=CONNECTOR,
        provider="google_search_console",
        key_version="local-v1",
        ciphertext=ciphertext,
        nonce=nonce,
        aad_hash=hashlib.sha256(aad).hexdigest(),
        encryption_key=key,
    )

    assert payload == {"access_token": "must-not-be-logged"}
    with pytest.raises(ValueError, match="connector_secret_aad_mismatch"):
        decrypt_secret_payload(
            tenant_id=TENANT,
            connector_id=UUID("019d0000-0000-7000-8000-000000000062"),
            provider="google_search_console",
            key_version="local-v1",
            ciphertext=ciphertext,
            nonce=nonce,
            aad_hash=hashlib.sha256(aad).hexdigest(),
            encryption_key=key,
        )


def test_key_and_cursor_validation_fail_closed() -> None:
    encoded = base64.urlsafe_b64encode(b"z" * 32).decode()
    assert decode_encryption_key(encoded) == b"z" * 32
    cursor = parse_cursor({"day": "2026-08-01", "start_row": 25_000})
    assert cursor is not None
    assert cursor.day == date(2026, 8, 1)
    encoded_cursor = parse_cursor('{"day":"2026-08-02","start_row":0}')
    assert encoded_cursor is not None
    assert encoded_cursor.day == date(2026, 8, 2)
    with pytest.raises(ValueError, match="invalid_connector_secret_key"):
        decode_encryption_key("bad")
    with pytest.raises(ValueError, match="invalid_sync_cursor"):
        parse_cursor({"day": "not-a-date", "start_row": 0})
    with pytest.raises(ValueError, match="search_query_hash_key_too_short"):
        require_secret_bytes("short", "search_query_hash_key_too_short")
