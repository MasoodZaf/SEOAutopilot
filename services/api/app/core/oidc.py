"""Verifying an OpenID Connect ID token, and nothing beyond that.

This module answers one question: did the configured identity provider issue
this token, to this application, for this person, and is it still valid? It
returns a subject and an email. It decides nothing about tenants, membership or
roles, because a verified token proves who somebody is and says nothing about
whose data they may see.

The signing keys come from the provider's JWKS. They rotate, so they are fetched
and cached rather than configured, and a token whose `kid` is unknown triggers
exactly one refetch -- enough to follow a rotation, bounded so an attacker
cannot turn unknown key ids into a stream of outbound requests.

Every check that a JWT library makes optional is required here:

* the algorithm comes from an allowlist, never from the token's own header, so
  `none` and HMAC-with-the-public-key are refused before a key is chosen;
* `aud` must contain this application's client id, or another application's
  token for the same provider would be accepted as one of ours;
* `iss` must match exactly;
* `exp` and `iat` are enforced with a small leeway for clock drift, no more;
* the email is only trusted when the provider marked it verified, because an
  unverified address is a claim by the user, and an invitation is matched on it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

# Asymmetric only. A symmetric algorithm here would mean the verifier holds a
# key that can also sign, which is how "verify with the public key as an HMAC
# secret" becomes a forgery.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")

# Clock drift between this host and the provider. Long enough to survive NTP
# jitter, short enough that an expired token is not usefully replayable.
CLOCK_LEEWAY_SECONDS = 30

# One refetch per unknown key id, and not more often than this. A rotation is
# rare; a flood of unknown key ids is somebody trying to make this service
# hammer the provider on demand.
JWKS_MIN_REFRESH_INTERVAL_SECONDS = 60


class OidcConfigurationError(RuntimeError):
    """The verifier cannot run. This is an operator's problem, not a caller's."""


class OidcVerificationError(RuntimeError):
    """The token is not one this application can accept."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """What a valid token establishes. Deliberately small."""

    issuer: str
    subject: str
    email: str
    email_verified: bool
    display_name: str

    @property
    def email_normalized(self) -> str:
        return self.email.strip().lower()


class OidcVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str,
        cache_seconds: int = 600,
    ) -> None:
        if not issuer or not audience or not jwks_uri:
            raise OidcConfigurationError("oidc_verifier_not_configured")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self._jwks = PyJWKClient(
            jwks_uri,
            cache_keys=True,
            lifespan=cache_seconds,
            max_cached_keys=16,
        )
        self._last_refresh = 0.0

    def _signing_key(self, token: str) -> object:
        try:
            return self._jwks.get_signing_key_from_jwt(token).key
        except PyJWKClientError as error:
            # An unknown key id is what a rotation looks like from here. Try
            # once more against a fresh set, rate limited, then give up.
            now = time.monotonic()
            if now - self._last_refresh < JWKS_MIN_REFRESH_INTERVAL_SECONDS:
                raise OidcVerificationError("token_signing_key_unknown") from error
            self._last_refresh = now
            try:
                self._jwks.fetch_data()
                return self._jwks.get_signing_key_from_jwt(token).key
            except (PyJWKClientError, httpx.HTTPError) as retry_error:
                raise OidcVerificationError("token_signing_key_unknown") from retry_error

    def verify(self, token: str) -> VerifiedIdentity:
        if not token or token.count(".") != 2:
            raise OidcVerificationError("token_malformed")
        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token,
                key,  # pyright: ignore[reportArgumentType]
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self.audience,
                issuer=self.issuer,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.ExpiredSignatureError as error:
            raise OidcVerificationError("token_expired") from error
        except jwt.InvalidAudienceError as error:
            raise OidcVerificationError("token_audience_mismatch") from error
        except jwt.InvalidIssuerError as error:
            raise OidcVerificationError("token_issuer_mismatch") from error
        except jwt.InvalidTokenError as error:
            # One code for everything else. Which check failed is useful in a
            # log and dangerous in a response body.
            raise OidcVerificationError("token_invalid") from error

        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not subject:
            raise OidcVerificationError("token_subject_missing")
        if not isinstance(email, str) or "@" not in email:
            # Without an address there is no invitation to match, so a token
            # that carries none cannot become a membership. Say so plainly
            # rather than creating an identity that can never be granted
            # anything.
            raise OidcVerificationError("token_email_missing")
        name = claims.get("name")
        return VerifiedIdentity(
            issuer=self.issuer,
            subject=subject,
            email=email.strip(),
            email_verified=claims.get("email_verified") is True,
            display_name=name.strip()[:200] if isinstance(name, str) else "",
        )


async def discover_jwks_uri(issuer: str, client: httpx.AsyncClient) -> str:
    """Read the provider's own discovery document for its JWKS location.

    Configuring the JWKS URI by hand is one more thing to get wrong and one more
    thing that silently stops matching after a provider migration. The issuer is
    the only thing an operator should have to write down.
    """
    base = issuer.rstrip("/")
    response = await client.get(f"{base}/.well-known/openid-configuration")
    if response.status_code >= 400:
        raise OidcConfigurationError("oidc_discovery_failed")
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcConfigurationError("oidc_discovery_invalid")
    if payload.get("issuer", "").rstrip("/") != base:
        # A discovery document that names a different issuer is either
        # misconfigured or not the provider it was fetched from.
        raise OidcConfigurationError("oidc_discovery_issuer_mismatch")
    jwks_uri = payload.get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
        raise OidcConfigurationError("oidc_discovery_invalid")
    return jwks_uri
