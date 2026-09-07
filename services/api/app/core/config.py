import hmac
from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_CURSOR_KEY = "local-development-cursor-signing-key-change-me"
LOCAL_PILOT_TENANT_ID = UUID("019d0000-0000-7000-8000-00000000c001")
LOCAL_PILOT_ACTOR_ID = UUID("019d0000-0000-7000-8000-00000000c002")
# A second named operator, so an author-then-approve cycle can involve two
# people during the pilot. This is not identity: it is two bearer tokens
# held by two humans, and it retires with the rest of the local pilot when
# OIDC lands. It exists because separation of duties was enforceable but
# never exercisable — one actor could be refused, two could not proceed.
LOCAL_PILOT_REVIEWER_ID = UUID("019d0000-0000-7000-8000-00000000c003")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")
    app_env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = (
        "postgresql+asyncpg://seo_autopilot:seo_autopilot@localhost:5432/seo_autopilot"
    )
    redis_url: str = "redis://localhost:6379/0"
    app_base_url: str = "http://localhost:3000"
    deployments_enabled: bool = False
    autopilot_enabled: bool = False
    oidc_issuer_url: str | None = None
    llm_provider: str = "openai"
    llm_model: str | None = None
    cursor_signing_key: str = LOCAL_CURSOR_KEY
    google_connectors_enabled: bool = False
    dns_provider_connectors_enabled: bool = False
    routines_enabled: bool = False
    notifications_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/v1/connectors/oauth/callback"
    search_query_hash_key: SecretStr | None = None
    connector_secret_backend: Literal["disabled", "database_envelope", "managed"] = "disabled"
    connector_secret_encryption_key: SecretStr | None = None
    connector_secret_key_version: str = "local-v1"
    # The GitHub App the tenants install. The private key is the app's, not a
    # tenant's: it signs a JWT that is exchanged for an installation token
    # scoped to whatever repositories that tenant granted. No tenant credential
    # is ever stored, and revoking access is an uninstall.
    github_connectors_enabled: bool = False
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_private_key: SecretStr | None = None
    github_app_callback_url: str = "http://localhost:8000/v1/connectors/github/callback"
    # The install-wide deployment target that predates per-site connectors. One
    # repository and one token for every tenant, which is why using it now takes
    # an explicit opt-in and is refused outside development: it is the pilot's
    # bridge onto a connector, not a supported configuration.
    github_legacy_token_enabled: bool = False
    github_repository: str | None = None
    github_base_branch: str = "main"
    github_token: SecretStr | None = None
    # Repository path for a crawled URL path, e.g. "CalcHive/{path}.html".
    # The site's URL layout is not derivable from the repository layout.
    github_path_template: str = "{path}.html"
    # The mock adapter fabricates a successful deployment. It must be opted
    # into explicitly and never inferred from app_env, because a deployment
    # that reports success without touching a provider is the exact failure
    # the safety checkpoint exists to prevent.
    mock_deployments_enabled: bool = False
    local_pilot_auth_enabled: bool = False
    local_pilot_auth_token: SecretStr | None = None
    local_pilot_tenant_id: UUID = LOCAL_PILOT_TENANT_ID
    local_pilot_actor_id: UUID = LOCAL_PILOT_ACTOR_ID
    local_pilot_reviewer_token: SecretStr | None = None
    local_pilot_reviewer_id: UUID = LOCAL_PILOT_REVIEWER_ID

    @model_validator(mode="after")
    def require_production_cursor_key(self) -> "Settings":
        if (
            self.app_env in {"staging", "production"}
            and self.cursor_signing_key == LOCAL_CURSOR_KEY
        ):
            raise ValueError("CURSOR_SIGNING_KEY must be configured outside development")
        if len(self.cursor_signing_key) < 32:
            raise ValueError("CURSOR_SIGNING_KEY must contain at least 32 characters")
        if self.google_connectors_enabled:
            if not self.google_client_id or not self.google_client_secret:
                raise ValueError("Google connector credentials must be configured when enabled")
            if (
                not self.search_query_hash_key
                or len(self.search_query_hash_key.get_secret_value()) < 32
            ):
                raise ValueError("SEARCH_QUERY_HASH_KEY must contain at least 32 characters")
        app_parts = (self.github_app_id, self.github_app_slug, self.github_app_private_key)
        if any(app_parts) and not all(app_parts):
            # A half-configured app is worse than none: the install endpoint
            # would offer a flow that cannot complete, and the tenant would
            # find out after granting access to their repository.
            raise ValueError(
                "GITHUB_APP_ID, GITHUB_APP_SLUG and GITHUB_APP_PRIVATE_KEY must be "
                "configured together or not at all"
            )
        if self.github_app_private_key is not None:
            from app.services.github_app import load_private_key

            load_private_key(self.github_app_private_key.get_secret_value())
        if self.github_legacy_token_enabled:
            # A token shared by every tenant cannot be made safe by scoping it
            # better, so it is bounded by environment instead.
            if self.app_env != "development":
                raise ValueError(
                    "GITHUB_LEGACY_TOKEN_ENABLED is development-only; connect a per-site "
                    "GitHub connector instead"
                )
            if not self.github_repository or not self.github_token:
                raise ValueError(
                    "GITHUB_REPOSITORY and GITHUB_TOKEN are required when the legacy "
                    "install-wide token is enabled"
                )
        if (
            self.google_connectors_enabled
            or self.dns_provider_connectors_enabled
            or self.github_connectors_enabled
            or self.notifications_enabled
        ):
            if self.connector_secret_backend == "disabled":
                raise ValueError(
                    "A connector secret backend is required when a connector or notifications "
                    "are enabled"
                )
            if self.connector_secret_backend == "managed":
                # This used to be *required* outside development, which made
                # production unreachable rather than safe: nothing implements a
                # managed backend, so the only configuration the validator
                # permitted was one that cannot start. Selecting it now fails
                # here, loudly, instead of at the first tenant who tries to
                # connect something.
                raise ValueError(
                    "The managed connector secret backend is not implemented; use "
                    "database_envelope and hold CONNECTOR_SECRET_ENCRYPTION_KEY outside "
                    "the database"
                )
            if self.connector_secret_backend == "database_envelope":
                if not self.connector_secret_encryption_key:
                    raise ValueError("CONNECTOR_SECRET_ENCRYPTION_KEY is required")
                from app.services.connector_secrets import decode_encryption_key

                decode_encryption_key(self.connector_secret_encryption_key.get_secret_value())
        if self.local_pilot_auth_enabled:
            if self.app_env != "development":
                raise ValueError("Local pilot authentication is development-only")
            if (
                not self.local_pilot_auth_token
                or len(self.local_pilot_auth_token.get_secret_value()) < 32
            ):
                raise ValueError("LOCAL_PILOT_AUTH_TOKEN must contain at least 32 characters")
            if self.local_pilot_reviewer_token is not None:
                reviewer = self.local_pilot_reviewer_token.get_secret_value()
                if len(reviewer) < 32:
                    raise ValueError(
                        "LOCAL_PILOT_REVIEWER_TOKEN must contain at least 32 characters"
                    )
                if hmac.compare_digest(reviewer, self.local_pilot_auth_token.get_secret_value()):
                    # One token behind two actor ids would let a single holder
                    # satisfy a two-approver rule, which is the whole point of
                    # the rule.
                    raise ValueError("The reviewer token must differ from the operator token")
                if self.local_pilot_reviewer_id == self.local_pilot_actor_id:
                    raise ValueError("The reviewer must be a different actor from the operator")
        if self.mock_deployments_enabled and self.app_env not in {"development", "test"}:
            raise ValueError("Mock deployments are development and test only")
        return self

    @property
    def github_app_configured(self) -> bool:
        """All three halves of the app credential, or none of them.

        An app id without a private key is not a partially working connector;
        it is one that fails at the moment a tenant tries to install it.
        """
        return bool(
            self.github_app_id and self.github_app_slug and self.github_app_private_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
