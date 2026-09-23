"""The call to the model, and nothing else.

Two providers, one contract. A workspace brings its own key for either, and
the draft records which wrote it. Blog drafting is the only thing in this
product that calls a model.

Claude: streamed, because a full post is long enough output that a non-streaming call
risks the HTTP timeout. The answer is constrained to `DRAFT_SCHEMA` with
structured outputs, so it arrives as JSON rather than prose to be parsed.

`fallbacks="default"` is on: if the model's safety classifier declines a
request, the API re-runs it on the model Anthropic recommends for that case
instead of returning a refusal. A draft about loan interest or exam revision
should not fail on a false positive. If the whole chain still refuses, the
draft fails with `model_refused` and nobody is charged for output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
import httpx

from app.content_drafts.prompt import DRAFT_SCHEMA

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Integer micro-dollars per million tokens (input, output), from list prices.
# An estimate for the monthly budget, not an invoice. A model not listed is
# charged at the most expensive rate here, so an unrecognised model -- a
# fallback, or a newer OpenAI snapshot -- never makes spend look smaller.
PRICES_PER_MILLION: dict[str, tuple[int, int]] = {
    "claude-opus-5": (5_000_000, 25_000_000),
    "claude-opus-4-8": (5_000_000, 25_000_000),
    "claude-sonnet-5": (2_000_000, 10_000_000),
    "claude-fable-5-1": (10_000_000, 50_000_000),
    "gpt-5": (1_250_000, 10_000_000),
}
MOST_EXPENSIVE = (10_000_000, 50_000_000)


def price_for(model: str) -> tuple[int, int]:
    if model in PRICES_PER_MILLION:
        return PRICES_PER_MILLION[model]
    # Dated snapshots ("gpt-5-2026-01-01") price as their family.
    for name, price in PRICES_PER_MILLION.items():
        if model.startswith(f"{name}-"):
            return price
    return MOST_EXPENSIVE


class DraftModelError(Exception):
    """A failure with a stable, loggable code. Never carries provider text."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    answer: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_micros(self) -> int:
        price_in, price_out = price_for(self.model)
        return -(-(self.input_tokens * price_in + self.output_tokens * price_out) // 1_000_000)


class DraftModel(Protocol):
    async def draft(self, *, api_key: str, model: str, system: str, user: str) -> ModelAnswer: ...


class AnthropicDraftModel:
    """Claude through the official SDK. One client per call: keys are per tenant."""

    def __init__(self, max_tokens: int = 32_000, timeout_seconds: float = 300.0) -> None:
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    async def draft(self, *, api_key: str, model: str, system: str, user: str) -> ModelAnswer:
        client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=self.timeout_seconds, max_retries=2
        )
        try:
            async with client.beta.messages.stream(
                model=model,
                max_tokens=self.max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": DRAFT_SCHEMA},
                },
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = await stream.get_final_message()
        except anthropic.AuthenticationError as error:
            raise DraftModelError("anthropic_key_rejected") from error
        except anthropic.PermissionDeniedError as error:
            raise DraftModelError("anthropic_key_forbidden") from error
        except anthropic.BadRequestError as error:
            raise DraftModelError("provider_request_rejected") from error
        except anthropic.RateLimitError as error:
            raise DraftModelError("provider_rate_limited", retryable=True) from error
        except anthropic.APIStatusError as error:
            raise DraftModelError("provider_unavailable", retryable=error.status_code >= 500) from error
        except anthropic.APIConnectionError as error:
            raise DraftModelError("provider_unreachable", retryable=True) from error
        finally:
            await client.close()

        if message.stop_reason == "refusal":
            raise DraftModelError("model_refused")
        if message.stop_reason == "max_tokens":
            raise DraftModelError("draft_too_long")
        text = next((block.text for block in message.content if block.type == "text"), None)
        answer = _parse_answer(text)
        usage = message.usage
        input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return ModelAnswer(
            answer=answer,
            model=message.model,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens or 0,
        )


def _parse_answer(text: str | None) -> dict[str, Any]:
    if text is None:
        raise DraftModelError("draft_invalid")
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as error:
        raise DraftModelError("draft_invalid") from error
    if not isinstance(answer, dict):
        raise DraftModelError("draft_invalid")
    return answer


class OpenAIDraftModel:
    """OpenAI Chat Completions with a strict JSON schema, over httpx.

    `DRAFT_SCHEMA` already meets strict mode's rules: every property required
    and no additional properties. A refusal arrives as `message.refusal`
    rather than content, and is reported as `model_refused` like Claude's.
    """

    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        max_tokens: int = 32_000,
        timeout_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def draft(self, *, api_key: str, model: str, system: str, user: str) -> ModelAnswer:
        body = {
            "model": model,
            "max_completion_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "blog_draft", "strict": True, "schema": DRAFT_SCHEMA},
            },
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds), transport=self.transport
            ) as client:
                response = await client.post(
                    self.URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.TimeoutException as error:
            raise DraftModelError("provider_timeout", retryable=True) from error
        except httpx.HTTPError as error:
            raise DraftModelError("provider_unreachable", retryable=True) from error
        code = response.status_code
        if code == 401:
            raise DraftModelError("openai_key_rejected")
        if code == 403:
            raise DraftModelError("openai_key_forbidden")
        if code == 429:
            raise DraftModelError("provider_rate_limited", retryable=True)
        if code >= 500:
            raise DraftModelError("provider_unavailable", retryable=True)
        if code >= 400:
            raise DraftModelError("provider_request_rejected")
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DraftModelError("draft_invalid") from error
        if message.get("refusal"):
            raise DraftModelError("model_refused")
        if choice.get("finish_reason") == "length":
            raise DraftModelError("draft_too_long")
        if choice.get("finish_reason") == "content_filter":
            raise DraftModelError("model_refused")
        usage = payload.get("usage") or {}
        return ModelAnswer(
            answer=_parse_answer(message.get("content")),
            model=str(payload.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
