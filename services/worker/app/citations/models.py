"""Ask an answer engine a question, with web search on, and keep what it cited.

Two providers, one contract, each called with the workspace's own key. The
question goes to the model exactly as a person would type it: no system
prompt, and never the tracked site's name, so the answer is not steered
toward it.

The answer is third-party model output. It comes back as data -- text and the
URLs it cited -- and nothing here or downstream acts on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
import httpx

from app.content_drafts.model import FALLBACK_BETA, price_for

# Integer micro-dollars per web search: $10 per 1,000 on both providers' list
# prices. An estimate for the monthly budget, not an invoice.
SEARCH_PRICE_MICROS = 10_000
MAX_SEARCHES = 3
MAX_OUTPUT_TOKENS = 4_000
MAX_CITED_URLS = 40


class CitationModelError(Exception):
    """A failure with a stable, storable code. Never carries provider text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CitationAnswer:
    text: str
    # In the order the answer cited them, duplicates kept; analysis dedupes.
    cited_urls: list[str]
    web_searches: int
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_micros(self) -> int:
        price_in, price_out = price_for(self.model)
        tokens = -(-(self.input_tokens * price_in + self.output_tokens * price_out) // 1_000_000)
        return tokens + self.web_searches * SEARCH_PRICE_MICROS


class CitationModel(Protocol):
    async def ask(self, *, api_key: str, model: str, question: str) -> CitationAnswer: ...


def anthropic_answer(message: dict[str, Any]) -> tuple[str, list[str]]:
    """Text and cited URLs from a Messages API response, as a plain dict."""
    parts: list[str] = []
    urls: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        parts.append(str(block.get("text") or ""))
        for citation in block.get("citations") or []:
            if isinstance(citation, dict) and citation.get("type") == "web_search_result_location":
                url = citation.get("url")
                if isinstance(url, str) and url:
                    urls.append(url)
    return "".join(parts).strip(), urls[:MAX_CITED_URLS]


def openai_answer(payload: dict[str, Any]) -> tuple[str, list[str], int]:
    """Text, cited URLs and search count from a Responses API response."""
    parts: list[str] = []
    urls: list[str] = []
    searches = 0
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            searches += 1
            continue
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            parts.append(str(content.get("text") or ""))
            for note in content.get("annotations") or []:
                if isinstance(note, dict) and note.get("type") == "url_citation":
                    url = note.get("url")
                    if isinstance(url, str) and url:
                        urls.append(url)
    return "".join(parts).strip(), urls[:MAX_CITED_URLS], searches


class AnthropicCitationModel:
    """Claude with the server-side web search tool. One client per call: keys are per tenant."""

    def __init__(self, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def ask(self, *, api_key: str, model: str, question: str) -> CitationAnswer:
        client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=self.timeout_seconds, max_retries=1
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        searches = input_tokens = output_tokens = 0
        served_by = model
        body: dict[str, Any] = {}
        try:
            # A long search loop pauses the turn; resuming sends the paused
            # assistant turn back unchanged. Two resumes at most.
            for _ in range(3):
                response = await client.beta.messages.create(
                    model=model,
                    max_tokens=MAX_OUTPUT_TOKENS,
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                    thinking={"type": "adaptive"},
                    # A lookup question, not a reasoning task.
                    output_config={"effort": "low"},
                    tools=[
                        {"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}
                    ],
                    messages=messages,  # type: ignore[arg-type]
                )
                body = response.to_dict()
                served_by = str(body.get("model") or model)
                usage = body.get("usage") or {}
                input_tokens += (
                    int(usage.get("input_tokens") or 0)
                    + int(usage.get("cache_creation_input_tokens") or 0)
                    + int(usage.get("cache_read_input_tokens") or 0)
                )
                output_tokens += int(usage.get("output_tokens") or 0)
                searches += int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
                if body.get("stop_reason") != "pause_turn":
                    break
                messages = [*messages, {"role": "assistant", "content": body.get("content") or []}]
        except anthropic.AuthenticationError as error:
            raise CitationModelError("anthropic_key_rejected") from error
        except anthropic.PermissionDeniedError as error:
            raise CitationModelError("anthropic_key_forbidden") from error
        except anthropic.BadRequestError as error:
            raise CitationModelError("provider_request_rejected") from error
        except anthropic.RateLimitError as error:
            raise CitationModelError("provider_rate_limited") from error
        except anthropic.APIStatusError as error:
            raise CitationModelError("provider_unavailable") from error
        except anthropic.APIConnectionError as error:
            raise CitationModelError("provider_unreachable") from error
        finally:
            await client.close()

        if body.get("stop_reason") == "refusal":
            raise CitationModelError("model_refused")
        text, urls = anthropic_answer(body)
        if not text:
            raise CitationModelError("answer_empty")
        return CitationAnswer(text, urls, searches, served_by, input_tokens, output_tokens)


class OpenAICitationModel:
    """OpenAI Responses API with the built-in web search tool, over httpx."""

    URL = "https://api.openai.com/v1/responses"

    def __init__(
        self, timeout_seconds: float = 120.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def ask(self, *, api_key: str, model: str, question: str) -> CitationAnswer:
        body = {
            "model": model,
            "input": question,
            "tools": [{"type": "web_search"}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": "low"},
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds), transport=self.transport
            ) as client:
                response = await client.post(
                    self.URL, json=body, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.TimeoutException as error:
            raise CitationModelError("provider_timeout") from error
        except httpx.HTTPError as error:
            raise CitationModelError("provider_unreachable") from error
        code = response.status_code
        if code == 401:
            raise CitationModelError("openai_key_rejected")
        if code == 403:
            raise CitationModelError("openai_key_forbidden")
        if code == 429:
            raise CitationModelError("provider_rate_limited")
        if code >= 500:
            raise CitationModelError("provider_unavailable")
        if code >= 400:
            raise CitationModelError("provider_request_rejected")
        try:
            payload = response.json()
        except ValueError as error:
            raise CitationModelError("answer_invalid") from error
        if not isinstance(payload, dict):
            raise CitationModelError("answer_invalid")
        text, urls, searches = openai_answer(payload)
        if not text:
            raise CitationModelError("answer_empty")
        usage = payload.get("usage") or {}
        return CitationAnswer(
            text,
            urls,
            searches,
            str(payload.get("model") or model),
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )
