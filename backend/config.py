"""Pydantic Settings — credentials from .env file + environment variables.

No API keys required — the agent uses the local `claude` and `codex` CLIs.
Just provide a CTF URL + credentials and you're good to go.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── CTF Platform (any supported site) ────────────────────────────────────
    # Pass these via .env, env vars, or CLI flags.
    ctf_url: str = "http://localhost:8000"     # Any CTF site URL
    ctf_user: str = ""                          # Username / email
    ctf_pass: str = ""                          # Password
    ctf_token: str = ""                         # API token (optional, overrides user/pass)

    # Legacy CTFd field aliases (backward-compat — mapped to ctf_* above)
    ctfd_url: str = ""
    ctfd_user: str = ""
    ctfd_pass: str = ""
    ctfd_token: str = ""

    # ── Optional API keys (only needed for Bedrock/Azure/Google fallback) ────
    # Not required when using claude-sdk or codex CLI providers (the default).
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    aws_region: str = "us-east-1"
    aws_bearer_token: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # ── Infra ────────────────────────────────────────────────────────────────
    sandbox_image: str = "ctf-sandbox"
    max_concurrent_challenges: int = 3
    max_attempts_per_challenge: int = 3
    container_memory_limit: str = "4g"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    def effective_url(self) -> str:
        """Return the effective CTF URL (ctf_url takes precedence over legacy ctfd_url)."""
        return self.ctf_url or self.ctfd_url or "http://localhost:8000"

    def effective_user(self) -> str:
        return self.ctf_user or self.ctfd_user or ""

    def effective_pass(self) -> str:
        return self.ctf_pass or self.ctfd_pass or ""

    def effective_token(self) -> str:
        return self.ctf_token or self.ctfd_token or ""
