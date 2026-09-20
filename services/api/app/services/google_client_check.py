"""Asking Google whether a tenant's OAuth client will actually work.

A client id can be perfectly well-formed and still be unusable, because the
one thing that decides it lives in somebody else's Google Cloud project: the
list of authorised redirect URIs on that client. If ours is not on it, Google
refuses the authorisation request outright.

That refusal is invisible to us. Google never calls our callback -- it
redirects the browser to its own error page -- so there is no request, no log
and no failed connector to look at afterwards. The first we hear of it is a
person telling us. That is the whole reason this module exists: the check has
to happen before anyone is sent to Google, because afterwards is too late to
observe anything at all.

The probe is the authorisation request itself, made with no secret and
followed nowhere. Google decides `redirect_uri_mismatch` before it decides
anything about consent, scopes or verification, so a single unauthenticated
GET is enough to tell a working client from a broken one. A client that is
fine answers with a redirect to a sign-in page; a broken one answers with a
redirect to `/signin/oauth/error` carrying a base64 blob that names the fault.

We read that blob by looking for the error code inside it rather than parsing
it properly. It is an undocumented protobuf and it is not a contract, so the
only safe reading is a conservative one: a code we recognise is a definite
answer, and anything else is `UNDETERMINED`, which never blocks a person from
saving their credential. Being unable to reach Google is not evidence that a
tenant's client is wrong.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlparse

import httpx

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"

# Any scope will do -- the redirect URI is checked before the scopes are -- so
# this is the narrowest one the product ever asks for.
PROBE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

ERROR_PATH = "/signin/oauth/error"


class ClientCheck(str, Enum):
    """What Google said about a client, in the only three useful shapes."""

    OK = "ok"
    REDIRECT_URI_MISMATCH = "redirect_uri_mismatch"
    CLIENT_UNKNOWN = "client_unknown"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class ClientCheckResult:
    status: ClientCheck
    redirect_uri: str

    @property
    def blocking(self) -> bool:
        """Whether this is a definite "this cannot work", worth refusing on.

        `UNDETERMINED` deliberately is not. It means we could not get an
        answer, which is a statement about us and Google, not about the
        tenant's client.
        """
        return self.status in {ClientCheck.REDIRECT_URI_MISMATCH, ClientCheck.CLIENT_UNKNOWN}


# Given a client id, what Google says about it. Injected wherever a consent
# is about to start, so the services stay free of HTTP and a deployment that
# cannot reach Google is not one where nobody can connect anything.
GoogleClientProbe = Callable[[str], Awaitable[ClientCheckResult]]

# The refusal each blocking verdict becomes. Lives here rather than beside
# either caller because both the save path and the connect path have to name
# the same fault the same way -- the UI turns these into one guide.
REFUSAL_DETAIL = {
    ClientCheck.REDIRECT_URI_MISMATCH: "google_client_redirect_uri_not_registered",
    ClientCheck.CLIENT_UNKNOWN: "google_client_unknown",
}


def _decode_auth_error(value: str) -> str:
    """The plaintext inside Google's `authError`, or "" if it is unreadable."""
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
    except (ValueError, binascii.Error):
        return ""


def _read(location: str) -> ClientCheck:
    parsed = urlparse(location)
    if not parsed.path.endswith(ERROR_PATH):
        # Anything that is not the error page means the request was accepted
        # and the browser is on its way to sign in or consent.
        return ClientCheck.OK
    blob = parse_qs(parsed.query).get("authError", [""])[0]
    text = _decode_auth_error(blob)
    if "redirect_uri_mismatch" in text:
        return ClientCheck.REDIRECT_URI_MISMATCH
    if "invalid_client" in text or "deleted_client" in text:
        return ClientCheck.CLIENT_UNKNOWN
    return ClientCheck.UNDETERMINED


async def check_google_client(
    client: httpx.AsyncClient, *, client_id: str, redirect_uri: str
) -> ClientCheckResult:
    """Ask Google whether this client will accept this redirect URI.

    Takes no client secret and completes no flow: nothing is authorised, no
    token is issued and no consent is recorded against the tenant's account.
    """
    try:
        response = await client.get(
            AUTHORIZE_ENDPOINT,
            params={
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": PROBE_SCOPE,
                "access_type": "offline",
                "state": "preflight",
            },
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return ClientCheckResult(ClientCheck.UNDETERMINED, redirect_uri)

    location = response.headers.get("location", "")
    if response.is_redirect and location:
        return ClientCheckResult(_read(location), redirect_uri)
    if response.status_code < 400:
        # A page rather than a redirect is Google getting on with sign-in.
        return ClientCheckResult(ClientCheck.OK, redirect_uri)
    return ClientCheckResult(ClientCheck.UNDETERMINED, redirect_uri)
