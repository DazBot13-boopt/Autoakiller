"""Universal CTF platform abstraction — auto-detect and support any CTF site."""

from backend.platform.base import CTFPlatform, Challenge, SubmitResult
from backend.platform.detect import detect_platform

__all__ = ["CTFPlatform", "Challenge", "SubmitResult", "detect_platform"]
