"""Which adapter the deploy route will reach, and under what configuration.

The mock adapter records a deployment that never happened. Production runs
`app_env=development` until real identity lands, so a gate that infers "this is
a safe place for a mock" from `app_env` would put the mock one flag flip away
from the live host. It takes its own opt-in instead.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.proposals import deploy_proposal, github_target
from app.api.schemas import DeploymentCreate
from app.core.config import Settings
from app.core.context import Role, TenantContext

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
            )


async def test_github_is_refused_until_a_repository_and_token_are_configured() -> None:
    for overrides in (
        {},
        {"github_repository": "MasoodZaf/mindTools"},
        {"github_token": "t" * 40},
    ):
        with pytest.raises(HTTPException) as raised:
            await deploy(DeploymentCreate(connector_type="github"), settings(**overrides))
        assert raised.value.status_code == 409


async def test_a_configured_github_target_is_parsed_and_reached() -> None:
    config = settings(github_repository="MasoodZaf/mindTools", github_token="t" * 40)
    target = github_target(config)

    assert target is not None
    assert (target.owner, target.repository, target.base_branch) == (
        "MasoodZaf",
        "mindTools",
        "main",
    )

    with patch("app.api.routes.proposals._deploy", new=AsyncMock()) as inner:
        await deploy(DeploymentCreate(connector_type="github"), config)

    assert inner.await_args is not None
    assert inner.await_args.args[-1].connector_type == "github"


async def test_a_malformed_repository_is_not_a_target() -> None:
    for raw in ("", "  ", "mindTools", "/mindTools", "MasoodZaf/", "a/b/c"):
        assert github_target(settings(github_repository=raw)) is None


async def test_a_connector_with_no_adapter_is_refused() -> None:
    with pytest.raises(HTTPException) as raised:
        await deploy(
            DeploymentCreate(connector_type="cms_staging"),
            settings(mock_deployments_enabled=True),
        )
    assert raised.value.status_code == 409
