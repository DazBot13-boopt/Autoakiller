"""HackTheBox platform adapter — supports HTB CTF events."""

from __future__ import annotations

import logging

import httpx

from backend.platform.base import CTFPlatform, Challenge, SubmitResult

logger = logging.getLogger(__name__)

HTB_API = "https://www.hackthebox.com/api/v4"
HTB_CTF_API = "https://ctf.hackthebox.com/api"


class HTBPlatform(CTFPlatform):
    platform_name = "HackTheBox"

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "") -> None:
        # base_url may be https://ctf.hackthebox.com/event/123 or https://app.hackthebox.com
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token  # HTB API key or session bearer

        self._client: httpx.AsyncClient | None = None
        self._event_id: str | None = None
        self._bearer: str = token

        # Extract event ID from URL if present: /event/123
        import re
        m = re.search(r"/event/(\d+)", self.base_url)
        if m:
            self._event_id = m.group(1)

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                verify=True,
                timeout=30.0,
                headers={"User-Agent": "ctf-agent/2.0"},
            )
        return self._client

    async def login(self) -> None:
        if self._bearer:
            return
        client = await self._ensure_client()
        # HTB uses email/password → JWT
        resp = await client.post(
            f"{HTB_API}/login",
            json={"email": self.username, "password": self.password, "remember": True},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("message", {}).get("access_token") or data.get("access_token", "")
        if not token:
            raise RuntimeError("HTB login failed — no token returned")
        self._bearer = token
        logger.info("[HTB] Logged in successfully")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer}", "Content-Type": "application/json"}

    async def fetch_challenges(self) -> list[Challenge]:
        await self.login()
        client = await self._ensure_client()

        if self._event_id:
            # CTF event challenges
            resp = await client.get(
                f"{HTB_CTF_API}/ctf/{self._event_id}/challenges",
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            raw = resp.json().get("challenges", resp.json().get("data", []))
        else:
            # Regular HTB challenges (active)
            resp = await client.get(f"{HTB_API}/challenges", headers=self._auth_headers())
            resp.raise_for_status()
            raw = resp.json().get("challenges", {}).get("data", [])

        challenges = []
        for ch in raw:
            challenges.append(Challenge(
                id=str(ch.get("id", "")),
                name=ch.get("name", ""),
                category=ch.get("category_name", ch.get("category", "")),
                description=ch.get("description", ""),
                value=ch.get("points", ch.get("value", 0)),
                connection_info=ch.get("connection_info", ""),
                tags=[t if isinstance(t, str) else t.get("name", "") for t in (ch.get("tags") or [])],
                files=[f.get("url", f) if isinstance(f, dict) else f for f in (ch.get("files") or [])],
                solves=ch.get("solves", 0),
                solved_by_me=ch.get("solved", False),
            ))
        return challenges

    async def fetch_solved_names(self) -> set[str]:
        challenges = await self.fetch_challenges()
        return {ch.name for ch in challenges if ch.solved_by_me}

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        await self.login()
        client = await self._ensure_client()

        # Find challenge ID from name
        challenges = await self.fetch_challenges()
        ch = next((c for c in challenges if c.name == challenge_name), None)
        if not ch:
            return SubmitResult("unknown", "Challenge not found", f'Challenge "{challenge_name}" not found on HTB')

        if self._event_id:
            resp = await client.post(
                f"{HTB_CTF_API}/ctf/{self._event_id}/challenges/{ch.id}/flag",
                json={"flag": flag.strip()},
                headers=self._auth_headers(),
            )
        else:
            resp = await client.post(
                f"{HTB_API}/challenges/{ch.id}/flag",
                json={"id": int(ch.id), "flag": flag.strip()},
                headers=self._auth_headers(),
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        # HTB returns {"message": "Correct flag!"} or {"message": "Incorrect flag!"}
        message = data.get("message", "")
        msg_lower = message.lower()

        if resp.status_code in (200, 201) and any(k in msg_lower for k in ("correct", "solved", "congrat")):
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
        if "already" in msg_lower or resp.status_code == 400:
            return SubmitResult("already_solved", message, f'ALREADY SOLVED — {message}'.strip())
        if "incorrect" in msg_lower or "wrong" in msg_lower:
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())
        return SubmitResult("unknown", message, f"Response: {message or resp.status_code}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
