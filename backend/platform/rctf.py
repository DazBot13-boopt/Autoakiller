"""rCTF platform adapter — open-source CTF framework used by many competitions."""

from __future__ import annotations

import logging

import httpx

from backend.platform.base import CTFPlatform, Challenge, SubmitResult

logger = logging.getLogger(__name__)


class RCTFPlatform(CTFPlatform):
    platform_name = "rCTF"

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token  # rCTF auth token (JWT)

        self._client: httpx.AsyncClient | None = None
        self._team_token: str = token
        self._challenge_map: dict[str, str] = {}  # name → id

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                headers={"User-Agent": "ctf-agent/2.0"},
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._team_token:
            h["Authorization"] = f"Bearer {self._team_token}"
        return h

    async def login(self) -> None:
        if self._team_token:
            return
        client = await self._ensure_client()

        # rCTF uses team token auth — try to auth with teamToken if password looks like one
        if self.password and len(self.password) > 20:
            resp = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"teamToken": self.password},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                self._team_token = data.get("data", {}).get("authToken", "")
                if self._team_token:
                    logger.info("[rCTF] Logged in via team token")
                    return

        # Register/login with name+email pattern
        resp = await client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"teamToken": self.password or self.username},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            self._team_token = data.get("data", {}).get("authToken", "")
            if self._team_token:
                logger.info("[rCTF] Logged in successfully")
                return

        raise RuntimeError(f"rCTF login failed: {resp.status_code} {resp.text[:200]}")

    async def fetch_challenges(self) -> list[Challenge]:
        await self.login()
        client = await self._ensure_client()
        resp = await client.get(f"{self.base_url}/api/v1/challs", headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("data", data if isinstance(data, list) else [])

        challenges = []
        for ch in raw:
            ch_id = str(ch.get("id", ""))
            name = ch.get("name", "")
            self._challenge_map[name] = ch_id

            files = []
            for f in ch.get("files") or []:
                url = f.get("url", "") if isinstance(f, dict) else f
                if url:
                    files.append(url if url.startswith("http") else f"{self.base_url}{url}")

            challenges.append(Challenge(
                id=ch_id,
                name=name,
                category=ch.get("category", ""),
                description=ch.get("description", ""),
                value=ch.get("points", 0),
                connection_info="",
                tags=[],
                files=files,
                solves=ch.get("solves", 0),
                solved_by_me=bool(ch.get("solves") and ch.get("userSolveTime")),
            ))
        return challenges

    async def fetch_solved_names(self) -> set[str]:
        await self.login()
        client = await self._ensure_client()
        try:
            resp = await client.get(f"{self.base_url}/api/v1/users/me", headers=self._headers())
            resp.raise_for_status()
            data = resp.json().get("data", {})
            solves = data.get("solves", [])
            return {s.get("name", s.get("challId", "")) for s in solves}
        except Exception:
            logger.warning("[rCTF] Could not fetch solved challenges", exc_info=True)
            return set()

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        await self.login()
        client = await self._ensure_client()

        ch_id = self._challenge_map.get(challenge_name)
        if not ch_id:
            # Refresh challenge map
            await self.fetch_challenges()
            ch_id = self._challenge_map.get(challenge_name)
        if not ch_id:
            return SubmitResult("unknown", "Not found", f'Challenge "{challenge_name}" not found')

        resp = await client.post(
            f"{self.base_url}/api/v1/challs/{ch_id}/submit",
            json={"flag": flag.strip()},
            headers=self._headers(),
        )
        try:
            data = resp.json()
        except Exception:
            data = {}

        kind = data.get("kind", "")
        message = data.get("message", "")

        if kind == "goodFlag":
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted.')
        if kind == "badFlag":
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected.')
        if kind == "alreadySolvedChallenge":
            return SubmitResult("already_solved", message, f'ALREADY SOLVED.')
        if kind == "ownFlag":
            return SubmitResult("incorrect", message, "Cannot submit your own flag.")
        return SubmitResult("unknown", message, f"Response: {kind} — {message}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
