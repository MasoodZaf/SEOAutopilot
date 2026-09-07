"""Which adapter the deploy route will reach, and under what configuration.

The mock adapter records a deployment that never happened. The live host stays
on `app_env=development` until an operator configures the identity provider and
moves it, so a gate that infers "this is a safe place for a mock" from `app_env`
would put the mock one flag flip away from production. It takes its own opt-in
instead.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.proposals import deploy_proposal
from app.api.schemas import DeploymentCreate
from app.core.config import Settings
from app.core.context import Role, TenantContext
from app.domain.github_adapter import GitHubTarget
from app.services.github_connector import GitHubCredential

pytestmark = pytest.mark.asyncio

KEY = "deploy-route-gate-key"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "development",
        "cursor_signing_key": "x" * 32,
        "deployments_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def context() -> TenantContext:
    return TenantContext(tenant_id=uuid4(), actor_id=uuid4(), role=Role.OWNER, trace_id="t")


async def deploy(command: DeploymentCreate, config: Settings) -> None:
    await deploy_proposal(uuid4(), command, context(), AsyncMock(), KEY, config)


async def test_the_mock_adapter_is_refused_without_its_own_opt_in() -> None:
    # app_env is "development" here, which is exactly the production value.
    with pytest.raises(HTTPException) as raised:
        await deploy(DeploymentCreate(connector_type="mock"), settings())

    assert raised.value.status_code == 409
    assert raised.value.detail == "deployment_connector_not_configured"


async def test_the_mock_adapter_is_reached_only_when_opted_into() -> None:
    with patch("app.api.routes.proposals._deploy", new=AsyncMock()) as inner:
        await deploy(
            DeploymentCreate(connector_type="mock"),
            settings(mock_deployments_enabled=True),
        )

    assert inner.await_count == 1
    assert inner.await_args is not None
    assert inner.await_args.args[-1].connector_type == "mock"


async def test_mock_deployments_cannot_be_opted_into_outside_development() -> None:
    for environment in ("staging", "production"):
        with pytest.raises(ValidationError, match="Mock deployments are development and test only"):
            settings(
                app_env=environment,
                mock_deployments_enabled=True,
                local_pilot_auth_enabled=False,
                # Silence the unrelated production guards so this asserts the
                # mock rule and not whichever validator happens to run first.
                google_connectors_enabled=False,
                dns_provider_connectors_enabled=False,
                notifications_enabled=False,
                oidc_issuer_url="https://issuer.example.com",
                oidc_audience="seo-autopilot",
            )


CREDENTIAL = GitHubCredential(
    target=GitHubTarget(owner="MasoodZaf", repository="mindTools", base_branch="main"),
    token="resolved-for-this-site",
    path_template="{path}.html",
    connector_id=UUID("019d0000-0000-7000-8000-0000000000c1"),
    source="github_app",
)


async def test_github_is_refused_when_the_site_has_no_credential() -> None:
    # The route no longer reads an install-wide token, so "not configured" is
    # now a fact about this proposal's site rather than about the process.
    refusal = HTTPException(status_code=409, detail="deployment_connector_not_configured")
    with patch(
        "app.api.routes.proposals.credential_for_proposal",
        new=AsyncMock(side_effect=refusal),
    ), pytest.raises(HTTPException) as raised:
        await deploy(DeploymentCreate(connector_type="github"), settings())

    assert raised.value.status_code == 409
    assert raised.value.detail == "deployment_connector_not_configured"


async def test_the_adapter_carries_the_credential_resolved_for_that_proposal() -> None:
    """The token reaching GitHub must be the one this site is entitled to.

    An install-wide token would make this assertion vacuous: every proposal
    would produce the same adapter. It is the whole point of the connector that
    the proposal id decides which credential is used.
    """
    proposal_id = uuid4()
    resolver = AsyncMock(return_value=CREDENTIAL)
    with (
        patch("app.api.routes.proposals.credential_for_proposal", new=resolver),
        patch("app.api.routes.proposals._deploy", new=AsyncMock()) as inner,
    ):
        await deploy_proposal(
            proposal_id,
            DeploymentCreate(connector_type="github"),
            context(),
            AsyncMock(),
            KEY,
            settings(),
        )

    assert resolver.await_args is not None
    assert resolver.await_args.args[2] == proposal_id
    assert inner.await_args is not None
    adapter = inner.await_args.args[-1]
    assert adapter.connector_type == "github"
    assert adapter._target is CREDENTIAL.target
    assert adapter._headers["Authorization"] == "Bearer resolved-for-this-site"


async def test_a_connector_with_no_adapter_is_refused() -> None:
    with pytest.raises(HTTPException) as raised:
        await deploy(
            DeploymentCreate(connector_type="cms_staging"),
            settings(mock_deployments_enabled=True),
        )
    assert raised.value.status_code == 409
