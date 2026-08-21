import base64
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.db.models import ConnectorSecret
from app.services.connector_secrets import DatabaseEnvelopeSecretStore, decode_encryption_key

TENANT_ID = UUID("019d0000-0000-7000-8000-000000000011")
CONNECTOR_ID = UUID("019d0000-0000-7000-8000-000000000061")
KEY = b"k" * 32


def test_key_decoder_requires_exactly_32_urlsafe_base64_bytes() -> None:
    assert decode_encryption_key(base64.urlsafe_b64encode(KEY).decode()) == KEY
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        decode_encryption_key(base64.urlsafe_b64encode(b"short").decode())


@pytest.mark.asyncio
async def test_database_envelope_ciphertext_hides_token_and_round_trips() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    stored: list[ConnectorSecret] = []

    def add(value: ConnectorSecret) -> None:
        value.id = uuid4()
        stored.append(value)

    session.add = MagicMock(side_effect=add)
    store = DatabaseEnvelopeSecretStore(session, KEY, "test-v1")
    payload: dict[str, object] = {
        "access_token": "top-secret-access",
        "refresh_token": "top-secret-refresh",
    }
    secret_ref = await store.store(
        TENANT_ID, CONNECTOR_ID, "google_search_console", payload
    )

    assert secret_ref.startswith("db-envelope://")
    assert len(stored) == 1
    assert b"top-secret-access" not in stored[0].ciphertext
    assert b"top-secret-refresh" not in stored[0].ciphertext
    session.scalar = AsyncMock(return_value=stored[0])
    assert await store.load(TENANT_ID, CONNECTOR_ID, secret_ref) == payload
