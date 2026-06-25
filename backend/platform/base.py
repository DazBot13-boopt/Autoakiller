"""Base types for the universal CTF platform abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubmitResult:
    status: str          # "correct" | "already_solved" | "incorrect" | "unknown"
    message: str
    display: str


@dataclass
class Challenge:
    id: str
    name: str
    category: str = ""
    description: str = ""
    value: int = 0
    connection_info: str = ""
    tags: list[str] = field(default_factory=list)
    hints: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    solves: int = 0
    solved_by_me: bool = False


class CTFPlatform(ABC):
    """Abstract interface every platform adapter must implement."""

    # Human-readable name shown in logs
    platform_name: str = "unknown"

    @abstractmethod
    async def login(self) -> None:
        """Authenticate — token, session cookie, OAuth, whatever the site needs."""
        ...

    @abstractmethod
    async def fetch_challenges(self) -> list[Challenge]:
        """Return all visible challenges."""
        ...

    @abstractmethod
    async def fetch_solved_names(self) -> set[str]:
        """Return names of challenges already solved by the current user/team."""
        ...

    @abstractmethod
    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        """Submit a flag for a challenge. Returns a SubmitResult."""
        ...

    async def pull_challenge(self, challenge: Challenge, output_dir: str) -> str:
        """Download distfiles and write metadata.yml. Returns the challenge dir path.

        Default implementation only writes metadata — platforms with file downloads
        should override this.
        """
        import re
        from pathlib import Path

        import yaml

        slug = re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", challenge.name.lower().strip())
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug).strip("-") or "challenge"

        ch_dir = Path(output_dir) / slug
        ch_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "name": challenge.name,
            "category": challenge.category,
            "description": challenge.description.strip(),
            "value": challenge.value,
            "connection_info": challenge.connection_info,
            "tags": challenge.tags,
            "solves": challenge.solves,
        }
        if challenge.hints:
            meta["hints"] = challenge.hints

        (ch_dir / "metadata.yml").write_text(
            yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
        )
        return str(ch_dir)

    async def close(self) -> None:
        """Clean up HTTP sessions etc."""
        ...
