"""PicoCTF platform adapter — picoctf.org and self-hosted picoCTF instances."""

from __future__ import annotations

import logging

import httpx

from backend.platform.base import CTFPlatform, Challenge, SubmitResult

logger = logging.getLogger(__name__)


class PicoCTFPlatform(CTFPlatform):
    platform_name = "picoCTF"

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token

        self._client: httpx.AsyncClient | None = None
        self._logged_in = bool(token)
        self._challenge_map: dict[str, dict] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                headers={"User-Agent": "ctf-agent/2.0"},
            )
            if self.token:
                self._client.headers.update({"Authorization": f"Token {self.token}"})
        return self._client

    async def login(self) -> None:
        if self._logged_in:
            return
        client = await self._ensure_client()

        # picoCTF login endpoint
        resp = await client.post(
            f"{self.base_url}/api/v1/user/login",
            json={"username": self.username, "password": self.password},
        )
        if resp.status_code not in (200, 201):
            # Try alternative endpoint
            resp = await client.post(
                f"{self.base_url}/login",
                data={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code not in (200, 201, 302):
            raise RuntimeError(f"picoCTF login failed: {resp.status_code}")
        self._logged_in = True
        logger.info("[picoCTF] Logged in successfully")

    async def fetch_challenges(self) -> list[Challenge]:
        await self.login()
        client = await self._ensure_client()

        # Try the REST API first
        resp = await client.get(f"{self.base_url}/api/v1/challenges")
        if resp.status_code != 200:
            resp = await client.get(f"{self.base_url}/api/problems")
        resp.raise_for_status()

        data = resp.json()
        raw = data if isinstance(data, list) else data.get("data", data.get("problems", []))

        challenges = []
        for ch in raw:
            if not ch.get("name") and not ch.get("title"):
                continue
            name = ch.get("name") or ch.get("title", "")
            ch_id = str(ch.get("id", ch.get("pid", name)))
            self._challenge_map[name] = ch

            files = []
            for f in ch.get("files") or []:
                url = f if isinstance(f, str) else f.get("url", "")
                if url:
                    files.append(url if url.startswith("http") else f"{self.base_url}{url}")

            conn_info = ch.get("connection_info", "") or ch.get("instance_connection_info", "")

            challenges.append(Challenge(
                id=ch_id,
                name=name,
                category=ch.get("category", ""),
                description=ch.get("description", ch.get("problem_statement", "")),
                value=ch.get("score", ch.get("value", ch.get("points", 0))),
                connection_info=conn_info,
                files=files,
                solves=ch.get("solve_count", ch.get("solves", 0)),
                solved_by_me=bool(ch.get("solved_by_me", ch.get("solved", False))),
            ))
        return challenges

    async def fetch_solved_names(self) -> set[str]:
        challenges = await self.fetch_challenges()
        return {ch.name for ch in challenges if ch.solved_by_me}

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        await self.login()
        client = await self._ensure_client()

        ch = self._challenge_map.get(challenge_name)
        if not ch:
            await self.fetch_challenges()
            ch = self._challenge_map.get(challenge_name)
        if not ch:
            return SubmitResult("unknown", "Not found", f'Challenge "{challenge_name}" not found')

        ch_id = ch.get("id") or ch.get("pid")

        # Try REST API submission
        resp = await client.post(
            f"{self.base_url}/api/v1/challenges/{ch_id}/submit",
            json={"flag": flag.strip()},
        )
        if resp.status_code == 404:
            resp = await client.post(
                f"{self.base_url}/api/submit-flag",
                json={"pid": ch_id, "flag": flag.strip(), "method": "mfa"},
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        correct = data.get("correct", data.get("success", False))
        message = data.get("message", data.get("error", ""))

        if correct:
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted.')
        if "already" in message.lower():
            return SubmitResult("already_solved", message, f'ALREADY SOLVED.')
        return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
