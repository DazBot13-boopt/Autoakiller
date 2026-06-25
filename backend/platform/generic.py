"""Generic/fallback platform adapter — works with any CTF site by scraping.

For unknown platforms, the agent will:
1. Fetch the homepage and detect what it can
2. Ask the user to provide a challenges JSON if auto-detection fails
3. Use regex to find and submit flags via the best-guess form/API
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from backend.platform.base import CTFPlatform, Challenge, SubmitResult

logger = logging.getLogger(__name__)


class GenericPlatform(CTFPlatform):
    platform_name = "Generic"

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        token: str = "",
        challenges_json: str = "",  # path to a local JSON file with challenge data
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.challenges_json = challenges_json

        self._client: httpx.AsyncClient | None = None
        self._session_cookies: dict = {}
        self._csrf: str = ""
        self._challenge_map: dict[str, dict] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            if self.token:
                headers["Authorization"] = f"Token {self.token}"
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                verify=False,
                timeout=30.0,
                headers=headers,
            )
        return self._client

    async def _try_find_csrf(self, html: str) -> str:
        """Extract CSRF token from HTML using common patterns."""
        patterns = [
            r'name=["\']?csrf[_-]?token["\']?\s+content=["\']([^"\']+)["\']',
            r'content=["\']([^"\']+)["\']\s+name=["\']?csrf[_-]?token["\']?',
            r'<input[^>]+name=["\']?_token["\']?[^>]+value=["\']([^"\']+)["\']',
            r'<input[^>]+name=["\']?csrf["\']?[^>]+value=["\']([^"\']+)["\']',
            r'"csrfmiddlewaretoken"\s+value="([^"]+)"',
            r"csrfNonce':\s*[\"']([A-Fa-f0-9]+)[\"']",
            r'window\.__csrf\s*=\s*["\']([^"\']+)["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                return m.group(1)
        return ""

    async def login(self) -> None:
        if not self.username and not self.password and not self.token:
            logger.info("[Generic] No credentials provided — proceeding unauthenticated")
            return

        client = await self._ensure_client()

        # Try common login endpoints
        login_endpoints = ["/login", "/auth/login", "/user/login", "/api/login", "/api/v1/auth/login"]
        home_resp = await client.get(self.base_url)
        csrf = await self._try_find_csrf(home_resp.text)

        # Try JSON login first (REST APIs)
        for endpoint in login_endpoints:
            try:
                payload: dict = {"username": self.username, "password": self.password}
                if csrf:
                    payload["csrf_token"] = csrf
                resp = await client.post(
                    f"{self.base_url}{endpoint}",
                    json=payload,
                )
                if resp.status_code in (200, 201, 302):
                    data = {}
                    try:
                        data = resp.json()
                    except Exception:
                        pass
                    # Check for token in response
                    token = (data.get("token") or data.get("access_token") or
                             data.get("data", {}).get("token") if isinstance(data.get("data"), dict) else None)
                    if token:
                        self.token = token
                        client.headers.update({"Authorization": f"Token {token}"})
                    logger.info(f"[Generic] Logged in via {endpoint}")
                    return
            except Exception:
                continue

        # Try form-based login
        for endpoint in login_endpoints:
            try:
                form_data: dict = {"username": self.username, "password": self.password}
                if csrf:
                    form_data["csrf_token"] = csrf
                resp = await client.post(
                    f"{self.base_url}{endpoint}",
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code in (200, 302):
                    logger.info(f"[Generic] Logged in via form at {endpoint}")
                    return
            except Exception:
                continue

        logger.warning("[Generic] Login may have failed — will attempt to continue")

    async def fetch_challenges(self) -> list[Challenge]:
        # Load from local JSON file if provided
        if self.challenges_json:
            import yaml
            from pathlib import Path
            p = Path(self.challenges_json)
            if p.exists():
                with open(p) as f:
                    raw = json.load(f) if p.suffix == ".json" else yaml.safe_load(f)
                return self._parse_challenges(raw if isinstance(raw, list) else raw.get("challenges", []))

        await self.login()
        client = await self._ensure_client()

        # Try common challenge API endpoints
        api_endpoints = [
            "/api/v1/challenges",
            "/api/challenges",
            "/challenges.json",
            "/api/problems",
            "/api/v1/problems",
            "/ctf/challenges",
        ]
        for endpoint in api_endpoints:
            try:
                resp = await client.get(f"{self.base_url}{endpoint}")
                if resp.status_code == 200:
                    data = resp.json()
                    raw = (data if isinstance(data, list)
                           else data.get("data", data.get("challenges", data.get("problems", []))))
                    if raw:
                        logger.info(f"[Generic] Found challenges at {endpoint}")
                        return self._parse_challenges(raw)
            except Exception:
                continue

        logger.warning("[Generic] Could not auto-fetch challenges — returning empty list")
        return []

    def _parse_challenges(self, raw: list) -> list[Challenge]:
        challenges = []
        for ch in raw:
            name = ch.get("name") or ch.get("title") or ch.get("challenge_name", "")
            if not name:
                continue
            ch_id = str(ch.get("id") or ch.get("pid") or name)
            self._challenge_map[name] = ch

            files = []
            for f in ch.get("files") or []:
                url = f if isinstance(f, str) else f.get("url", "")
                if url:
                    files.append(url if url.startswith("http") else f"{self.base_url}{url}")

            challenges.append(Challenge(
                id=ch_id,
                name=name,
                category=ch.get("category", ch.get("type", "")),
                description=ch.get("description") or ch.get("problem_statement") or ch.get("body", ""),
                value=ch.get("points") or ch.get("value") or ch.get("score", 0),
                connection_info=ch.get("connection_info") or ch.get("instance_connection_info", ""),
                files=files,
                solves=ch.get("solves") or ch.get("solve_count", 0),
                solved_by_me=bool(ch.get("solved") or ch.get("solved_by_me", False)),
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

        ch_id = ch.get("id") or ch.get("pid") if ch else None

        # Try common submission endpoints
        submit_endpoints = []
        if ch_id:
            submit_endpoints = [
                (f"/api/v1/challenges/{ch_id}/submit", {"flag": flag.strip()}),
                (f"/api/challenges/{ch_id}/flag", {"flag": flag.strip()}),
                (f"/api/v1/challs/{ch_id}/submit", {"flag": flag.strip()}),
                ("/api/v1/challenges/attempt", {"challenge_id": ch_id, "submission": flag.strip()}),
                ("/submit", {"challenge": ch_id, "flag": flag.strip()}),
            ]
        submit_endpoints.append(("/api/submit", {"name": challenge_name, "flag": flag.strip()}))

        for endpoint, payload in submit_endpoints:
            try:
                resp = await client.post(f"{self.base_url}{endpoint}", json=payload)
                if resp.status_code in (200, 201):
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}

                    # Detect correctness from common response patterns
                    status = (data.get("status") or data.get("result") or
                              data.get("kind") or data.get("data", {}).get("status", "") if isinstance(data.get("data"), dict) else "")
                    message = (data.get("message") or data.get("msg") or
                               data.get("data", {}).get("message", "") if isinstance(data.get("data"), dict) else "")
                    correct = (data.get("correct") or data.get("success") or
                               status in ("correct", "goodFlag") or
                               any(k in str(message).lower() for k in ("correct", "congrat", "solved", "right")))

                    if correct:
                        return SubmitResult("correct", str(message), f'CORRECT — "{flag}" accepted.')
                    if any(k in str(message).lower() for k in ("already", "alreadysolved")):
                        return SubmitResult("already_solved", str(message), "ALREADY SOLVED.")
                    if status in ("incorrect", "badFlag", "wrong") or any(k in str(message).lower() for k in ("incorrect", "wrong", "invalid")):
                        return SubmitResult("incorrect", str(message), f'INCORRECT — "{flag}" rejected.')
            except Exception as e:
                logger.debug(f"[Generic] Submit attempt at {endpoint} failed: {e}")
                continue

        return SubmitResult("unknown", "No matching submission endpoint", f'Could not submit "{flag}" — unknown platform format')

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
