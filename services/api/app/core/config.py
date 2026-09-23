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
    # The identity migration 0027 created for sweeps that look for work across
    # tenants, before any tenant scope exists. The API needs it for exactly one
    # thing -- finding unfinished rollbacks -- and falls back to the application
    # role, where that sweep simply finds nothing, rather than refusing to start.
    relay_database_url: str | None = None
    app_base_url: str = "http://localhost:3000"
    deployments_enabled: bool = False
    autopilot_enabled: bool = False
    # Real identity. `oidc_issuer_url` used to select an error string and
    # nothing else; it now names the provider that issues the tokens this API
    # accepts. `oidc_audience` is this application's client id, checked against
    # the token's `aud` so another application's token from the same provider is
    # not accepted as one of ours. The JWKS location is discovered from the
    # issuer unless an operator pins it.
    oidc_issuer_url: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_jwks_cache_seconds: int = 600
    llm_provider: str = "openai"
    llm_model: str | None = None
    cursor_signing_key: str = LOCAL_CURSOR_KEY
    google_connectors_enabled: bool = False
    dns_provider_connectors_enabled: bool = False
    routines_enabled: bool = False
    # Polling GitHub for what happened to revert pull requests. A revert is
    # merged by a person, so nothing pushes that fact here; without this a
    # rollback stays `rollback_pending` for ever.
    # Prometheus scrape credential. Unset means the metrics endpoint is
    # development-only, so a deployment that forgets it publishes nothing
    # rather than publishing its operational posture to the internet.
    metrics_scrape_token: SecretStr | None = None
    # The interactive docs and the OpenAPI schema. Off by default and keyed on
    # its own flag rather than on `app_env`, because the live deployment runs as
    # `development` until the identity cutover -- an environment check would
    # exempt the one place that matters.
    api_docs_enabled: bool = False
    rollback_reconcile_enabled: bool = False
    rollback_reconcile_interval_seconds: int = 300
    # Drafting a deterministic repair as the system rather than as whoever
    # clicked. Off by default: a tenant that has not asked for this should not
    # find proposals it did not make.
    proposal_drafting_enabled: bool = False
    proposal_drafting_interval_seconds: int = 900
    proposal_drafting_batch: int = 20
    # Whether a deployed change is actually on the live page. Off by default
    # like the others, and read-only: it fetches the tenant's own pages and
    # records what it saw. Without it no measurement can ever be computed,
    # because the measurement refuses without a verification.
    deployment_verification_enabled: bool = False
    deployment_verification_interval_seconds: int = 1800
    deployment_verification_batch: int = 20
    notifications_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/v1/connectors/oauth/callback"
    # How long Google lets a connector grant live before a person must consent
    # again, when it imposes a limit at all. An External OAuth app still in
    # "Testing" gets refresh tokens that die after 7 days; a published one gets
    # tokens with no fixed expiry. Nothing in the token response says which, so
    # the operator states it: set 7 while the consent screen is in Testing, and
    # unset it after publishing. Only ever used to show a date to a person.
    google_grant_lifetime_days: int | None = None
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
    # The same app's OAuth half. It identifies the person connecting, so the
    # repositories offered to them are the ones GitHub says they can push to,
    # rather than whatever an installation id in a query string reaches.
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
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
        if bool(self.github_app_client_id) != bool(self.github_app_client_secret):
            raise ValueError(
                "GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET must be configured together"
            )
        if self.github_app_client_id and not self.github_app_configured:
            raise ValueError(
                "GITHUB_APP_CLIENT_ID needs GITHUB_APP_ID, GITHUB_APP_SLUG and "
                "GITHUB_APP_PRIVATE_KEY: the person signs in to the app it identifies"
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
        if self.oidc_issuer_url is not None:
            if not self.oidc_issuer_url.startswith("https://"):
                # An http issuer means the tokens, and the keys used to check
                # them, cross the network in the clear.
                raise ValueError("OIDC_ISSUER_URL must be an https URL")
            if not self.oidc_audience:
                raise ValueError("OIDC_AUDIENCE is required when an OIDC issuer is configured")
        if self.app_env in {"staging", "production"} and self.oidc_issuer_url is None:
            # The condition that made production unreachable is gone, so this is
            # what stops it becoming reachable *and* unauthenticated: outside
            # development the only credential is a verified token, and there is
            # no provider to verify one against.
            raise ValueError("OIDC_ISSUER_URL must be configured outside development")
        if (
            self.metrics_scrape_token is not None
            and len(self.metrics_scrape_token.get_secret_value()) < 32
        ):
            raise ValueError("METRICS_SCRAPE_TOKEN must contain at least 32 characters")
        if self.proposal_drafting_enabled:
            if self.proposal_drafting_interval_seconds < 60:
                # Each tick reads a repository file per candidate. A tighter
                # loop is a rate limit against an API whose answers change only
                # when a person edits something.
                raise ValueError("PROPOSAL_DRAFTING_INTERVAL_SECONDS must be at least 60")
            if not 1 <= self.proposal_drafting_batch <= 100:
                raise ValueError("PROPOSAL_DRAFTING_BATCH must be between 1 and 100")
        if self.deployment_verification_enabled:
            if self.deployment_verification_interval_seconds < 300:
                # Each tick fetches a live page per candidate, and a page that
                # is not merged yet answers the same way every time. Asking a
                # tenant's own site more often than this is traffic, not news.
                raise ValueError("DEPLOYMENT_VERIFICATION_INTERVAL_SECONDS must be at least 300")
            if not 1 <= self.deployment_verification_batch <= 100:
                raise ValueError("DEPLOYMENT_VERIFICATION_BATCH must be between 1 and 100")
        if self.rollback_reconcile_enabled and self.rollback_reconcile_interval_seconds < 60:
            # A tighter loop is a rate limit waiting to happen against an API
            # that changes state only when a human acts.
            raise ValueError("ROLLBACK_RECONCILE_INTERVAL_SECONDS must be at least 60")
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
