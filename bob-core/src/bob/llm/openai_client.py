from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        InternalServerError,
        OpenAI,
        PermissionDeniedError,
        RateLimitError,
    )
except ImportError as exc:  # pragma: no cover - guarded by package dependency
    raise RuntimeError("The openai package is required to use Bob's planner.") from exc

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 3

ParsedModelT = TypeVar("ParsedModelT", bound=BaseModel)


class OpenAIClientError(RuntimeError):
    """Base error for OpenAI planner client failures."""


class OpenAIConfigurationError(OpenAIClientError):
    """Raised when the OpenAI client is misconfigured."""


class OpenAIRequestError(OpenAIClientError):
    """Raised when the OpenAI API request fails."""


class OpenAIResponseError(OpenAIClientError):
    """Raised when the OpenAI API response cannot be consumed."""


@dataclass(frozen=True)
class StructuredResponse(Generic[ParsedModelT]):
    parsed: ParsedModelT
    response_id: str | None
    model: str
    output_text: str | None
    raw_payload: dict[str, Any]


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: OpenAI | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None and not resolved_api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is required for Bob's planner.")

        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._sleep = sleep
        self._client = client or OpenAI(
            api_key=resolved_api_key,
            timeout=float(timeout_seconds),
            max_retries=0,
        )

    def parse_structured_output(
        self,
        *,
        instructions: str,
        prompt: str,
        output_type: type[ParsedModelT],
        metadata: dict[str, str] | None = None,
    ) -> StructuredResponse[ParsedModelT]:
        attempt = 0
        while True:
            try:
                response = self._client.responses.parse(
                    model=self.model,
                    instructions=instructions,
                    input=prompt,
                    text_format=output_type,
                    metadata=metadata or {},
                    max_output_tokens=4000,
                    timeout=float(self.timeout_seconds),
                )
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    raise OpenAIResponseError("OpenAI returned no parsed planner output.")
                return StructuredResponse(
                    parsed=parsed,
                    response_id=getattr(response, "id", None),
                    model=getattr(response, "model", self.model) or self.model,
                    output_text=getattr(response, "output_text", None),
                    raw_payload=_serialize_response(response),
                )
            except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as exc:
                if attempt >= self.max_retries:
                    raise OpenAIRequestError(_format_request_error(exc)) from exc
                self._sleep(_retry_delay_seconds(attempt))
                attempt += 1
            except APIStatusError as exc:
                if _is_retryable_status(exc.status_code) and attempt < self.max_retries:
                    self._sleep(_retry_delay_seconds(attempt))
                    attempt += 1
                    continue
                raise OpenAIRequestError(_format_request_error(exc)) from exc
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise OpenAIConfigurationError(_format_request_error(exc)) from exc
            except OpenAIClientError:
                raise
            except Exception as exc:  # pragma: no cover - defensive wrapper
                raise OpenAIRequestError(f"Unexpected OpenAI planner failure: {exc}") from exc


def _retry_delay_seconds(attempt: int) -> float:
    return min(2.0 * (attempt + 1), 6.0)


def _is_retryable_status(status_code: int | None) -> bool:
    return status_code == 429 or (status_code is not None and status_code >= 500)


def _format_request_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    parts = [type(exc).__name__]
    if status_code is not None:
        parts.append(f"status={status_code}")
    message = str(exc).strip()
    if message:
        parts.append(message)
    return ": ".join(parts)


def _serialize_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        payload = response.model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
    if hasattr(response, "to_dict"):
        payload = response.to_dict()
        if isinstance(payload, dict):
            return payload
    if hasattr(response, "model_dump_json"):
        try:
            payload = json.loads(response.model_dump_json())
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"repr": repr(response)}
