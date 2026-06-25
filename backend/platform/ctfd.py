"""CTFd platform adapter — wraps the existing CTFdClient."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from backend.platform.base import CTFPlatform, Challenge, SubmitResult

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36"


class CTFdPlatform(CTFPlatform):
    platform_name = "CTFd"

    def __init__(self, base_url: str, username: str = "", password: str = "", token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token

        self._client: httpx.AsyncClient | None = None
        self._csrf_token: str = ""
        self._logged_in: bool = False
        self._challenge_ids: dict[str, int] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                follow_redirects=False,
                verify=False,
                timeout=30.0,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    async def login(self) -> None:
        if self._logged_in or self.token:
            return
        client = await self._ensure_client()

        resp = await client.get("/login")
        nonce = None
        for pattern in [r'id="nonce"[^>]*value="([^"]+)"', r'name="nonce"[^>]*value="([^"]+)"']:
            m = re.search(pattern, resp.text)
            if m:
                nonce = m.group(1)
                break
        if not nonce:
            raise RuntimeError("Could not find nonce on CTFd login page")

        resp = await client.post(
            "/login",
            data={"name": self.username, "password": self.password, "_submit": "Submit", "nonce": nonce},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            raise RuntimeError("CTFd login failed — check credentials")
        self._logged_in = True
        logger.info(f"[CTFd] Logged in as {self.username}")

    async def _get_csrf(self) -> str:
        if self._csrf_token:
            return self._csrf_token
        client = await self._ensure_client()
        resp = await client.get("/challenges")
        m = re.search(r"csrfNonce':\s*\"([A-Fa-f0-9]+)\"", resp.text)
        if not m:
            raise RuntimeError("Could not find csrfNonce on CTFd challenges page")
        self._csrf_token = m.group(1)
        return self._csrf_token

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Token {self.token}"
        return h

    async def _get(self, path: str) -> Any:
        await self.login()
        client = await self._ensure_client()
        resp = await client.get(f"/api/v1{path}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        await self.login()
        client = await self._ensure_client()
        headers = self._headers()
        if not self.token:
            headers["CSRF-Token"] = await self._get_csrf()
        resp = await client.post(f"/api/v1{path}", json=body, headers=headers)
        if resp.status_code == 403 and not self.token:
            self._csrf_token = ""
            headers["CSRF-Token"] = await self._get_csrf()
            resp = await client.post(f"/api/v1{path}", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def fetch_challenges(self) -> list[Challenge]:
        data = await self._get("/challenges?per_page=500")
        challenges = []
        for stub in data.get("data", []):
            if stub.get("type") == "hidden":
                continue
            try:
                detail = await self._get(f"/challenges/{stub['id']}")
                ch = detail["data"]
            except Exception:
                ch = stub

            try:
                from markdownify import markdownify as html2md
                desc = html2md(ch.get("description") or "", heading_style="atx", escape_asterisks=False).strip()
            except Exception:
                desc = ch.get("description") or ""

            tags = [t["value"] if isinstance(t, dict) else str(t) for t in (ch.get("tags") or [])]
            hints = []
            for h in ch.get("hints") or []:
                hint: dict[str, Any] = {"cost": h.get("cost", 0)}
                if h.get("content"):
                    hint["content"] = h["content"]
                hints.append(hint)

            challenges.append(Challenge(
                id=str(ch.get("id", stub["id"])),
                name=ch.get("name", ""),
                category=ch.get("category", ""),
                description=desc,
                value=ch.get("value", 0),
                connection_info=ch.get("connection_info") or "",
                tags=tags,
                hints=hints,
                files=ch.get("files") or [],
                solves=ch.get("solves", 0),
            ))
        return challenges

    async def fetch_solved_names(self) -> set[str]:
        try:
            me = await self._get("/users/me")
            user_data = me.get("data", {})
            team_id = user_data.get("team_id")
            if team_id:
                solves = await self._get(f"/teams/{team_id}/solves")
            else:
                uid = user_data.get("id")
                if not uid:
                    return set()
                solves = await self._get(f"/users/{uid}/solves")
            return {
                s["challenge"]["name"]
                for s in solves.get("data", [])
                if s.get("challenge", {}).get("name")
            }
        except Exception:
            logger.warning("[CTFd] Could not fetch solved challenges", exc_info=True)
            return set()

    async def _get_challenge_id(self, name: str) -> int:
        if name in self._challenge_ids:
            return self._challenge_ids[name]
        data = await self._get("/challenges?per_page=500")
        for ch in data.get("data", []):
            self._challenge_ids[ch["name"]] = ch["id"]
        if name not in self._challenge_ids:
            raise RuntimeError(f'Challenge "{name}" not found in CTFd')
        return self._challenge_ids[name]

    async def submit_flag(self, challenge_name: str, flag: str) -> SubmitResult:
        challenge_id = await self._get_challenge_id(challenge_name)
        resp = await self._post(
            "/challenges/attempt",
            {"challenge_id": challenge_id, "submission": flag.strip()},
        )
        status = resp.get("data", {}).get("status", "unknown")
        message = resp.get("data", {}).get("message", "")
        flag = flag.strip()
        if status == "correct":
            return SubmitResult("correct", message, f'CORRECT — "{flag}" accepted. {message}'.strip())
        if status == "already_solved":
            return SubmitResult("already_solved", message, f'ALREADY SOLVED — "{flag}" accepted. {message}'.strip())
        if status == "incorrect":
            return SubmitResult("incorrect", message, f'INCORRECT — "{flag}" rejected. {message}'.strip())
        return SubmitResult("unknown", message, f"Unknown status: {status}")

    async def pull_challenge(self, challenge: Challenge, output_dir: str) -> str:
        import re as _re
        from urllib.parse import urlparse as _up

        slug = _re.sub(r'[<>:"/\\|?*.\x00-\x1f]', "", challenge.name.lower().strip())
        slug = _re.sub(r"[\s_]+", "-", slug)
        slug = _re.sub(r"-+", "-", slug).strip("-") or "challenge"

        ch_dir = Path(output_dir) / slug
        ch_dir.mkdir(parents=True, exist_ok=True)

        await self.login()
        client = await self._ensure_client()

        for raw_url in challenge.files:
            dist_dir = ch_dir / "distfiles"
            dist_dir.mkdir(exist_ok=True)
            url = raw_url if raw_url.startswith("http") else f"{self.base_url}/{raw_url.lstrip('/')}"
            url_path = _up(url).path
            fname = url_path.rstrip("/").rsplit("/", 1)[-1] or "file"
            dest = dist_dir / fname
            if not dest.exists():
                try:
                    headers = self._headers() if _up(url).hostname == _up(self.base_url).hostname else {}
                    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=60.0)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                    logger.info(f"[CTFd] Downloaded {fname} ({len(resp.content)} bytes)")
                except Exception as e:
                    logger.warning(f"[CTFd] Failed to download {url}: {e}")

        meta: dict[str, Any] = {
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
        if self._client:
            await self._client.aclose()
            self._client = None
