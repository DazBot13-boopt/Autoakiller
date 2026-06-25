"""Model resolution — defaults to claude-sdk and codex CLIs (no API keys needed).

The default setup requires only the `claude` and `codex` CLIs to be installed
and authenticated. API-backed providers (Bedrock, Azure, Google) are optional
fallbacks for when the CLIs are unavailable or quota-limited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.settings import ModelSettings

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from backend.config import Settings

# ── Default models — no API keys required ────────────────────────────────────
# Uses local claude CLI (Claude Code) and codex CLI exclusively.
DEFAULT_MODELS: list[str] = [
    "claude-sdk/claude-opus-4-6/medium",   # claude CLI, medium thinking
    "claude-sdk/claude-opus-4-6/max",      # claude CLI, max thinking
    "codex/gpt-5.4",                        # codex CLI
    "codex/gpt-5.4-mini",                  # codex CLI, fast
    "codex/gpt-5.3-codex",                 # codex CLI, reasoning
]

# Context window sizes (tokens)
CONTEXT_WINDOWS: dict[str, int] = {
    "us.anthropic.claude-opus-4-6-v1": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "gpt-5.4": 1_000_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.3-codex": 1_000_000,
    "gpt-5.3-codex-spark": 128_000,
    "gemini-3-flash-preview": 1_000_000,
}

# Models that support vision
VISION_MODELS: set[str] = {
    "us.anthropic.claude-opus-4-6-v1",
    "claude-opus-4-6",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gemini-3-flash-preview",
}


def resolve_model(spec: str, settings: "Settings") -> "Model":
    """Resolve a 'provider/model_id' spec to a Pydantic AI Model.

    API-backed providers (bedrock, azure, zen, google) require the corresponding
    keys in Settings. The default claude-sdk and codex providers never reach here
    — they use their own solver backends.
    """
    # Lazy imports so optional heavy deps (boto3, etc.) don't block the default path
    provider = provider_from_spec(spec)
    model_id = model_id_from_spec(spec)
    match provider:
        case "bedrock":
            import boto3
            from pydantic_ai.models.bedrock import BedrockConverseModel
            from pydantic_ai.providers.bedrock import BedrockProvider
            if settings.aws_bearer_token:
                return BedrockConverseModel(
                    model_id,
                    provider=BedrockProvider(api_key=settings.aws_bearer_token, region_name=settings.aws_region),
                )
            session = boto3.Session()
            client = session.client("bedrock-runtime", region_name=settings.aws_region)
            return BedrockConverseModel(model_id, provider=BedrockProvider(bedrock_client=client))

        case "azure":
            from pydantic_ai.models.openai import OpenAIModel
            from pydantic_ai.providers.openai import OpenAIProvider
            return OpenAIModel(
                model_id,
                provider=OpenAIProvider(base_url=settings.azure_openai_endpoint, api_key=settings.azure_openai_api_key),
            )

        case "zen":
            from pydantic_ai.models.openai import OpenAIModel
            from pydantic_ai.providers.openai import OpenAIProvider
            return OpenAIModel(
                model_id,
                provider=OpenAIProvider(base_url="https://opencode.ai/zen/v1", api_key=settings.opencode_zen_api_key),
            )

        case "google":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider
            return GoogleModel(model_id, provider=GoogleProvider(api_key=settings.gemini_api_key))

        case "claude-sdk" | "codex":
            raise ValueError(
                f"Provider '{provider}' uses its own solver backend (no API key). "
                f"resolve_model() should not be called for {spec}."
            )
        case _:
            raise ValueError(f"Unknown provider: {provider}")


def resolve_model_settings(spec: str) -> ModelSettings:
    """Get provider-specific model settings with caching enabled."""
    provider = spec.split("/", 1)[0]
    match provider:
        case "bedrock":
            from pydantic_ai.models.bedrock import BedrockModelSettings
            return BedrockModelSettings(
                max_tokens=128_000,
                bedrock_cache_instructions=True,
                bedrock_cache_tool_definitions=True,
                bedrock_cache_messages=True,
            )
        case "azure" | "zen":
            from pydantic_ai.models.openai import OpenAIModelSettings
            return OpenAIModelSettings(max_tokens=128_000)
        case "google":
            from pydantic_ai.models.google import GoogleModelSettings
            return GoogleModelSettings(
                max_tokens=64_000,
                google_thinking_config={"thinking_level": "high", "include_thoughts": True},
            )
        case _:
            return ModelSettings(max_tokens=128_000)


def model_id_from_spec(spec: str) -> str:
    """Extract just the model ID from a spec (strips effort suffix)."""
    parts = spec.split("/")
    return parts[1] if len(parts) >= 2 else spec


def provider_from_spec(spec: str) -> str:
    """Extract the provider from a spec."""
    return spec.split("/", 1)[0]


def effort_from_spec(spec: str) -> str | None:
    """Extract effort level from a spec like 'claude-sdk/claude-opus-4-6/max'."""
    parts = spec.split("/")
    if len(parts) >= 3 and parts[2] in ("low", "medium", "high", "max"):
        return parts[2]
    return None


def supports_vision(spec: str) -> bool:
    """Check if a model spec supports vision."""
    return model_id_from_spec(spec) in VISION_MODELS


def context_window(spec: str) -> int:
    """Get context window size for a model spec."""
    return CONTEXT_WINDOWS.get(model_id_from_spec(spec), 200_000)
