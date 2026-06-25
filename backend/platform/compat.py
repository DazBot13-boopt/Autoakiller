"""Compatibility shim — wraps any CTFPlatform to look like the old CTFdClient.

All internal code that uses CTFdClient can be migrated gradually. This shim
makes the new universal platform drop-in compatible with the old interface.
"""

from __future__ import annotations

from typing import Any

from backend.platform.base import CTFPlatform


class PlatformClient:
    """Wraps a CTFPlatform and exposes the CTFdClient-compatible API.

    This is the single object passed through the whole solver pipeline.
    Internal code accesses it exactly like the old CTFdClient.
    """

    def __init__(self, platform: CTFPlatform) -> None:
        self._platform = platform
        # Expose platform info
        self.platform_name: str = platform.platform_name
        self.base_url: str = getattr(platform, "base_url", "")

    # ── CTFdClient-compatible methods ─────────────────────────────────────────

    async def submit_flag(self, challenge_name: str, flag: str):
        """Submit a flag. Returns a SubmitResult (same shape as before)."""
        result = await self._platform.submit_flag(challenge_name, flag)
        # Wrap in a legacy-compatible object
        return _LegacySubmitResult(result.status, result.message, result.display)

    async def fetch_challenge_stubs(self) -> list[dict[str, Any]]:
        challenges = await self._platform.fetch_challenges()
        return [
            {
                "id": ch.id, "name": ch.name, "category": ch.category,
                "value": ch.value, "solves": ch.solves,
            }
            for ch in challenges
        ]

    async def fetch_all_challenges(self) -> list[dict[str, Any]]:
        challenges = await self._platform.fetch_challenges()
        return [
            {
                "id": ch.id, "name": ch.name, "category": ch.category,
                "description": ch.description, "value": ch.value,
                "connection_info": ch.connection_info, "tags": ch.tags,
                "hints": ch.hints, "files": ch.files, "solves": ch.solves,
            }
            for ch in challenges
        ]

    async def fetch_solved_names(self) -> set[str]:
        return await self._platform.fetch_solved_names()

    async def get_challenge_id(self, name: str) -> str:
        challenges = await self._platform.fetch_challenges()
        ch = next((c for c in challenges if c.name == name), None)
        if not ch:
            raise RuntimeError(f'Challenge "{name}" not found on {self.platform_name}')
        return ch.id

    async def pull_challenge(self, challenge: Any, output_dir: str) -> str:
        """Pull a challenge dict or Challenge object."""
        from backend.platform.base import Challenge
        if isinstance(challenge, Challenge):
            return await self._platform.pull_challenge(challenge, output_dir)
        # Convert legacy dict to Challenge
        ch = Challenge(
            id=str(challenge.get("id", "")),
            name=challenge.get("name", ""),
            category=challenge.get("category", ""),
            description=challenge.get("description", ""),
            value=challenge.get("value", 0),
            connection_info=challenge.get("connection_info", ""),
            tags=challenge.get("tags", []),
            hints=challenge.get("hints", []),
            files=challenge.get("files", []),
            solves=challenge.get("solves", 0),
        )
        return await self._platform.pull_challenge(ch, output_dir)

    async def close(self) -> None:
        await self._platform.close()

    # ── Direct platform access ────────────────────────────────────────────────

    @property
    def platform(self) -> CTFPlatform:
        return self._platform


class _LegacySubmitResult:
    """Matches the old SubmitResult shape (status, message, display)."""
    def __init__(self, status: str, message: str, display: str) -> None:
        self.status = status
        self.message = message
        self.display = display
