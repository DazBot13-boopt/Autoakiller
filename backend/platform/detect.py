"""Auto-detect CTF platform from a URL.

Detection order:
1. URL hostname/path pattern matching (fast, no HTTP request)
2. HTTP probe of the root page (fingerprint HTML/headers)
3. Fallback to GenericPlatform
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx

from backend.platform.base import CTFPlatform

logger = logging.getLogger(__name__)


def _hostname(url: str) -> str:
    return urlparse(url).hostname or ""


async def detect_platform(
    url: str,
    username: str = "",
    password: str = "",
    token: str = "",
    challenges_json: str = "",
) -> CTFPlatform:
    """Return the best-matching platform adapter for the given URL.

    Probes the URL if needed. Never raises — falls back to GenericPlatform.
    """
    host = _hostname(url.lower())
    path = urlparse(url).path.lower()

    # ── Fast hostname/path rules ──────────────────────────────────────────────

    # HackTheBox — app.hackthebox.com or ctf.hackthebox.com
    if "hackthebox" in host:
        from backend.platform.htb import HTBPlatform
        logger.info(f"[detect] Platform: HackTheBox ({url})")
        return HTBPlatform(url, username=username, password=password, token=token)

    # picoCTF — picoctf.org or self-hosted picoCTF
    if "picoctf" in host:
        from backend.platform.picoctf import PicoCTFPlatform
        logger.info(f"[detect] Platform: picoCTF ({url})")
        return PicoCTFPlatform(url, username=username, password=password, token=token)

    # ── HTTP fingerprint probe ────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            html = resp.text.lower()
            headers = {k.lower(): v.lower() for k, v in resp.headers.items()}

            # CTFd fingerprints
            ctfd_signals = [
                "ctfd" in html,
                "ctfd" in headers.get("x-powered-by", ""),
                re.search(r"window\.init\s*\(", html) is not None,
                "csrfNonce".lower() in html,
                "/api/v1/challenges" in html,
                # Common CTFd page structure markers
                'id="challenge-container"' in html.replace(" ", ""),
                "powered by ctfd" in html,
            ]
            if sum(ctfd_signals) >= 1:
                from backend.platform.ctfd import CTFdPlatform
                logger.info(f"[detect] Platform: CTFd (fingerprint match, {url})")
                return CTFdPlatform(url, username=username, password=password, token=token)

            # rCTF fingerprints
            rctf_signals = [
                "rctf" in html,
                "/api/v1/challs" in html,
                '"goodFlag"' in html,
                '"badFlag"' in html,
                "redpwn" in html,
            ]
            if sum(rctf_signals) >= 1:
                from backend.platform.rctf import RCTFPlatform
                logger.info(f"[detect] Platform: rCTF (fingerprint match, {url})")
                return RCTFPlatform(url, username=username, password=password, token=token)

            # HackTheBox via HTML
            if any(k in html for k in ("hackthebox", "hack the box")):
                from backend.platform.htb import HTBPlatform
                logger.info(f"[detect] Platform: HackTheBox (HTML match, {url})")
                return HTBPlatform(url, username=username, password=password, token=token)

            # picoCTF via HTML
            if any(k in html for k in ("picoctf", "pico ctf", "carnegie mellon")):
                from backend.platform.picoctf import PicoCTFPlatform
                logger.info(f"[detect] Platform: picoCTF (HTML match, {url})")
                return PicoCTFPlatform(url, username=username, password=password, token=token)

    except Exception as e:
        logger.warning(f"[detect] HTTP probe failed: {e} — falling back to Generic")

    # ── Fallback ──────────────────────────────────────────────────────────────
    from backend.platform.generic import GenericPlatform
    logger.info(f"[detect] Platform: Generic (unknown, {url})")
    return GenericPlatform(url, username=username, password=password, token=token, challenges_json=challenges_json)
