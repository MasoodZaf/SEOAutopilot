"""The estate that predates per-tenant credentials keeps working, untouched.

Moving Google and GitHub credentials into per-tenant rows is the kind of change
that silently strands whoever was already using the old arrangement. The tenants
on this deployment were set up against the deployment's own OAuth client and
GitHub App, their Search Console and Analytics grants were issued by that
client, and their repository connectors were installed under that app.

None of that is re-consented, re-installed or migrated. It resolves to the
platform credential because no tenant credential exists, and these cases hold
that fallback in place -- deliberately, because it is the property that is easy
to delete by accident later and expensive to discover missing.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.core.config import Settings
from app.services.tenant_credentials import github_app_credential, google_oauth_client

TENANT_ID = UUID("019d0000-0000-7000-8000-0000000000a1")

# Generated per run rather than pasted, the way test_github_app.py does it: a
# literal PEM in a test file is one transcription error away from failing for a
# reason that has nothing to do with what is under test.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_KEY = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "app_env": "test",
        "cursor_signing_key": "c" * 32,
        "google_connectors_enabled": True,
        "google_client_id": "platform-client",
        "google_client_secret": SecretStr("platform-secret"),
        "search_query_hash_key": SecretStr("q" * 32),
        "connector_secret_backend": "database_envelope",
        "connector_secret_encryption_key": SecretStr(
            "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="
        ),
    }
    base.update(overrides)
    return Settings(**base)  # pyright: ignore[reportCallIssue]


def _session_without_credential() -> MagicMock:
    """A session whose `tenant_credential` lookup finds nothing."""
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
async def test_a_tenant_with_no_stored_client_still_uses_the_deployments() -> None:
    """The existing estate's Search Console and GA4 connectors keep working.

    Their grants were issued by the deployment's client and are redeemable only
    by it, so resolving to anything else here would break every one of them at
    the next token renewal -- an hour later, silently, as a consent prompt.
    """
    client = await google_oauth_client(
        _session_without_credential(), _settings(), TENANT_ID
    )

    assert client is not None
    assert client.client_id == "platform-client"
    assert client.client_secret == "platform-secret"
    # Surfaced in the UI so a workspace can tell whose Google project it is on.
    assert client.source == "platform"


@pytest.mark.asyncio
async def test_a_stored_client_takes_precedence_over_the_deployments() -> None:
    session = MagicMock()
    credential = MagicMock()
    credential.config_json = {"client_id": "tenant-client"}
    credential.tenant_id = TENANT_ID
    credential.provider = "google_oauth_client"
    credential.key_version = "local-v1"
    session.scalar = AsyncMock(return_value=credential)

    # Seal a real payload so the store opens it the way production does, rather
    # than mocking past the decryption this test exists to exercise.
    import hashlib
    import json
    import secrets as secrets_module

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.services.connector_secrets import decode_encryption_key
    from app.services.tenant_credentials import TenantCredentialStore, credential_aad

    key = decode_encryption_key("eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg=")
    aad = credential_aad(TENANT_ID, "google_oauth_client", "local-v1")
    nonce = secrets_module.token_bytes(12)
    credential.nonce = nonce
    credential.ciphertext = AESGCM(key).encrypt(
        nonce, json.dumps({"client_secret": "tenant-secret"}).encode(), aad
    )
    credential.aad_hash = hashlib.sha256(aad).hexdigest()

    store = TenantCredentialStore(session, key, "local-v1")
    assert await store.get(TENANT_ID, "google_oauth_client") == {
        "client_secret": "tenant-secret"
    }

    client = await google_oauth_client(session, _settings(), TENANT_ID)
    assert client is not None
    assert (client.client_id, client.client_secret) == ("tenant-client", "tenant-secret")
    assert client.source == "tenant"


@pytest.mark.asyncio
async def test_a_deployment_with_no_google_client_at_all_resolves_to_nothing() -> None:
    """Refused here, not at Google.

    A new workspace that has stored nothing, on a deployment that configures
    nothing, gets a refusal naming the missing credential rather than being
    sent to a consent screen that answers `invalid_client`.
    """
    client = await google_oauth_client(
        _session_without_credential(),
        _settings(google_connectors_enabled=False, google_client_id=None, google_client_secret=None),
        TENANT_ID,
    )
    assert client is None


@pytest.mark.asyncio
async def test_a_tenant_with_no_stored_app_still_uses_the_deployments_github_app() -> None:
    """Existing repository connectors keep deploying.

    An installation belongs to the app it was created under. Resolving to a
    different app would make every stored `installation_id` unmintable, so the
    deploy button would start failing on connectors nobody had touched.
    """
    settings = _settings(
        github_connectors_enabled=True,
        github_app_id="123456",
        github_app_slug="platform-app",
        github_app_private_key=SecretStr(PRIVATE_KEY),
    )
    app = await github_app_credential(_session_without_credential(), settings, TENANT_ID)

    assert app is not None
    assert (app.app_id, app.app_slug) == ("123456", "platform-app")
    assert app.source == "platform"
