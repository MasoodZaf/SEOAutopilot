from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_CURSOR_KEY = "local-development-cursor-signing-key-change-me"
LOCAL_PILOT_TENANT_ID = UUID("019d0000-0000-7000-8000-00000000c001")
LOCAL_PILOT_ACTOR_ID = UUID("019d0000-0000-7000-8000-00000000c002")


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
    # GitHub deployment target. Absent by default, so the route keeps failing
    # closed until a repository and token are configured deliberately.
    github_repository: str | None = None
    github_base_branch: str = "main"
    github_token: SecretStr | None = None
    local_pilot_auth_enabled: bool = False
    local_pilot_auth_token: SecretStr | None = None
    local_pilot_tenant_id: UUID = LOCAL_PILOT_TENANT_ID
    local_pilot_actor_id: UUID = LOCAL_PILOT_ACTOR_ID

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
        if (
            self.google_connectors_enabled
            or self.dns_provider_connectors_enabled
            or self.notifications_enabled
        ):
            if self.connector_secret_backend == "disabled":
                raise ValueError(
                    "A connector secret backend is required when a connector or notifications "
                    "are enabled"
                )
            if (
                self.app_env in {"staging", "production"}
                and self.connector_secret_backend != "managed"
            ):
                raise ValueError(
                    "Staging and production require a managed connector secret backend"
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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
