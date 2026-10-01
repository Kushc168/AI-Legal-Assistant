"""LLM client. Calls Claude with a prompt from PROMPTS.md and returns schema-validated JSON.

Services never build prompts: they pass a prompt id and variables, and get a dict back.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

import anthropic

from ..config import get_settings
from ..prompts import loader
from .verifier import SchemaError, validate_schema

log = logging.getLogger("llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class LLMError(Exception):
    pass


class LLMRefusal(LLMError):
    pass


@dataclass
class LLMResult:
    data: dict
    prompt_ref: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.model = settings.llm_model
        self._fallbacks = settings.llm_fallbacks == "default"
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            base_url=settings.llm_base_url or "https://api.anthropic.com",
            timeout=settings.llm_timeout,
            max_retries=2,
        )

    def _call(self, system: str, user: str, schema: dict, effort: str):
        kwargs = dict(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": loader.api_schema(schema)},
            },
        )
        if self._fallbacks:
            try:
                return self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                )
            except anthropic.BadRequestError as exc:
                # Some gateways reject beta parameters; continue without server-side fallback.
                if "fallback" not in str(exc).lower():
                    raise
                log.warning("Server-side fallback rejected; disabling it: %s", exc)
                self._fallbacks = False
        return self._client.messages.create(**kwargs)

    def complete_json(self, prompt_id: str, variables: dict) -> LLMResult:
        prompt = loader.get(prompt_id)
        if prompt.schema is None:
            raise LLMError(f"Prompt {prompt_id} has no output schema")
        system, user = loader.render(prompt_id, variables)
        effort = str(prompt.meta.get("effort", "medium"))
        last_error: Exception | None = None
        for attempt in range(2):  # one retry on invalid output (SPEC 3.6 rule 1)
            started = time.perf_counter()
            try:
                response = self._call(system, user, prompt.schema, effort)
            except anthropic.AuthenticationError as exc:
                raise LLMError("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
            except anthropic.RateLimitError as exc:
                raise LLMError("The LLM rate limit was reached. Try again shortly.") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMError("Could not reach the LLM endpoint.") from exc
            except anthropic.APIStatusError as exc:
                raise LLMError(f"LLM request failed ({exc.status_code}): {exc.message}") from exc
            latency = int((time.perf_counter() - started) * 1000)
            usage = getattr(response, "usage", None)
            log.info(
                "llm %s attempt=%d latency_ms=%d in=%s out=%s stop=%s",
                prompt.ref, attempt + 1, latency,
                getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"),
                response.stop_reason,
            )
            if response.stop_reason == "refusal":
                raise LLMRefusal("The model declined to analyse this content.")
            if response.stop_reason == "max_tokens":
                last_error = LLMError("The model output was cut off (max_tokens).")
                continue
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                data = json.loads(text)
                validate_schema(data, prompt.schema)
            except (json.JSONDecodeError, SchemaError) as exc:
                last_error = exc
                log.warning("llm %s invalid output: %s", prompt.ref, exc)
                continue
            return LLMResult(
                data=data,
                prompt_ref=prompt.ref,
                model=getattr(response, "model", self.model),
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                latency_ms=latency,
            )
        raise LLMError(f"Invalid model output for {prompt.ref}: {last_error}")


_client: LLMClient | None = None
_lock = threading.Lock()


def get_client() -> LLMClient:
    global _client
    with _lock:
        if _client is None:
            _client = LLMClient()
        return _client


def is_enabled() -> bool:
    settings = get_settings()
    return settings.llm_provider == "anthropic" and bool(settings.anthropic_api_key)
