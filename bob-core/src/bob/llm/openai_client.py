from __future__ import annotations

import json
import os
import time
import warnings
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
DEFAULT_HTTP_USER_AGENT = "BobRuntime/0.1"

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
        api_key_env_var: str | None = "OPENAI_API_KEY",
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str | None = None,
        user_agent: str = DEFAULT_HTTP_USER_AGENT,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: OpenAI | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved_api_key = api_key or (os.environ.get(api_key_env_var) if api_key_env_var else None)
        if client is None and not resolved_api_key and not base_url:
            missing = api_key_env_var or "OPENAI_API_KEY"
            raise OpenAIConfigurationError(f"{missing} is required for Bob's planner.")

        self.model = model
        self.base_url = base_url
        self.user_agent = user_agent
        self.api_key_env_var = api_key_env_var
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._sleep = sleep
        self._client = client or OpenAI(
            api_key=resolved_api_key or "",
            base_url=base_url,
            default_headers={"User-Agent": user_agent},
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

    def probe(self) -> StructuredResponse["_ProbePayload"]:
        attempt = 0
        while True:
            try:
                response = self._client.models.list()
                model_ids = [model_id for model_id in _extract_model_ids(response) if model_id]
                return StructuredResponse(
                    parsed=_ProbePayload(message=model_ids[0] if model_ids else "models_list_ok"),
                    response_id=None,
                    model=self.model,
                    output_text=None,
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
            except Exception as exc:  # pragma: no cover - defensive wrapper
                raise OpenAIRequestError(f"Unexpected OpenAI planner failure: {exc}") from exc


class _ProbePayload(BaseModel):
    message: str


def _extract_model_ids(response: Any) -> list[str]:
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        return []
    model_ids: list[str] = []
    for item in data:
        model_id = getattr(item, "id", None)
        if isinstance(model_id, str) and model_id.strip():
            model_ids.append(model_id)
    return model_ids


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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            payload = response.model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
    if hasattr(response, "to_dict"):
        payload = response.to_dict()
        if isinstance(payload, dict):
            return payload
    if hasattr(response, "model_dump_json"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=UserWarning)
                payload = json.loads(response.model_dump_json())
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {"repr": repr(response)}
