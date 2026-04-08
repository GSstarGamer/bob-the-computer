from __future__ import annotations

import os

from bob.llm import DEFAULT_MAX_RETRIES, DEFAULT_OPENAI_MODEL, DEFAULT_TIMEOUT_SECONDS, OpenAIResponsesClient
from bob.runtime.models import LLMEndpointConfig, LLMProvider

DEFAULT_PROVIDER = LLMProvider.OPENAI_COMPATIBLE
DEFAULT_LOCAL_BASE_URL = "https://llm.rionnag.net/gpt-oss/v1"
DEFAULT_LOCAL_MODEL = "gpt-oss-20b"


def resolve_endpoint_config(
    *,
    role: str,
    provider_override: str | None = None,
    base_url_override: str | None = None,
    api_key_env_override: str | None = None,
    shared_model_override: str | None = None,
    role_model_override: str | None = None,
    timeout_seconds_override: int | None = None,
    max_retries_override: int | None = None,
) -> LLMEndpointConfig:
    normalized_role = role.strip().upper()
    provider = _parse_provider(provider_override or os.environ.get("BOB_LLM_PROVIDER") or DEFAULT_PROVIDER.value)
    role_model_env = os.environ.get(f"BOB_{normalized_role}_MODEL")
    role_base_url_env = os.environ.get(f"BOB_{normalized_role}_BASE_URL")

    openai_model_env = os.environ.get("BOB_OPENAI_MODEL") if provider == LLMProvider.OPENAI else None
    model = (
        role_model_override
        or shared_model_override
        or role_model_env
        or os.environ.get("BOB_LLM_MODEL")
        or openai_model_env
    )
    if not model:
        model = DEFAULT_OPENAI_MODEL if provider == LLMProvider.OPENAI else DEFAULT_LOCAL_MODEL

    base_url = base_url_override or role_base_url_env or os.environ.get("BOB_LLM_BASE_URL")
    if not base_url and provider == LLMProvider.OPENAI_COMPATIBLE:
        base_url = DEFAULT_LOCAL_BASE_URL
    timeout_seconds = timeout_seconds_override or int(
        os.environ.get("BOB_LLM_TIMEOUT_SECONDS")
        or os.environ.get("BOB_OPENAI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    )
    max_retries = max_retries_override or int(
        os.environ.get("BOB_LLM_MAX_RETRIES")
        or os.environ.get("BOB_OPENAI_MAX_RETRIES", str(DEFAULT_MAX_RETRIES))
    )

    if api_key_env_override:
        api_key_env = api_key_env_override
        api_key = os.environ.get(api_key_env_override)
    elif provider == LLMProvider.OPENAI_COMPATIBLE:
        api_key_env = "BOB_LLM_API_KEY" if os.environ.get("BOB_LLM_API_KEY") else None
        api_key = os.environ.get("BOB_LLM_API_KEY")
    else:
        api_key_env = "OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else "BOB_LLM_API_KEY"
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("BOB_LLM_API_KEY")

    return LLMEndpointConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def build_responses_client(config: LLMEndpointConfig) -> OpenAIResponsesClient:
    return OpenAIResponsesClient(
        api_key=config.api_key,
        api_key_env_var=config.api_key_env,
        model=config.model,
        base_url=config.base_url,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
    )


def _parse_provider(value: str) -> LLMProvider:
    normalized = value.strip().lower()
    if normalized == LLMProvider.OPENAI.value:
        return LLMProvider.OPENAI
    if normalized in {LLMProvider.OPENAI_COMPATIBLE.value, "local", "local_openai"}:
        return LLMProvider.OPENAI_COMPATIBLE
    raise ValueError(f"Unsupported LLM provider: {value}")
