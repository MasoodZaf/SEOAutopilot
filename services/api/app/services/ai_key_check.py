"""Asking a provider, with a workspace's stored key, which drafting models it may use.

A key that is well-formed can still be unable to draft on the model this
deployment asks for: OpenAI gates gpt-5 behind organization verification and
per-project model limits, and answers 404 for a key that may not use it. The
draft then steps down to the fallback model, which works but says nothing
about why. The person who owns the key cannot test it either without having
the key to hand, and it is stored here precisely so they need not.

The probe is the provider's own "describe this model" call: it costs nothing,
generates no text, and fails the same way a draft would. Nothing the provider
returns is kept beyond the status it maps to; the key is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import httpx

OPENAI_MODELS_URL = "https://api.openai.com/v1/models/"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models/"
ANTHROPIC_VERSION = "2023-06-01"


class KeyCheckStatus(StrEnum):
    AVAILABLE = "available"
    MODEL_UNAVAILABLE = "model_unavailable"  # the key works; this model is not open to it
    KEY_REJECTED = "key_rejected"  # revoked, mistyped, or from a deleted project
    NOT_PERMITTED = "not_permitted"  # a restricted key, or a project that blocks the call
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class ModelCheck:
    model: str
    status: KeyCheckStatus


def _status(code: int) -> KeyCheckStatus:
    if code == 200:
        return KeyCheckStatus.AVAILABLE
    if code == 404:
        return KeyCheckStatus.MODEL_UNAVAILABLE
    if code == 401:
        return KeyCheckStatus.KEY_REJECTED
    if code == 403:
        return KeyCheckStatus.NOT_PERMITTED
    if code == 429:
        return KeyCheckStatus.RATE_LIMITED
    return KeyCheckStatus.UNREACHABLE


async def check_model(
    client: httpx.AsyncClient, *, provider: str, api_key: str, model: str
) -> ModelCheck:
    """Whether `api_key` may use `model` right now, as the provider answers it."""
    if provider == "openai":
        url = OPENAI_MODELS_URL + model
        headers = {"Authorization": f"Bearer {api_key}"}
    elif provider == "anthropic":
        url = ANTHROPIC_MODELS_URL + model
        headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    else:
        raise ValueError("unsupported_provider")
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return ModelCheck(model, KeyCheckStatus.UNREACHABLE)
    return ModelCheck(model, _status(response.status_code))
