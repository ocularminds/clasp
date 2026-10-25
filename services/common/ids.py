"""Opaque identifiers. Prefixes make logs greppable."""
from __future__ import annotations

import secrets
import string
import time

_ALPHABET = string.ascii_uppercase + string.digits
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time()):x}{secrets.token_hex(6)}"


def new_nonce() -> str:
    return "non_" + secrets.token_urlsafe(18)


def new_code(length: int = 6) -> str:
    """Human-friendly one-time code (delivery / challenge)."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def correlation_id() -> str:
    return "corr_" + secrets.token_hex(8)
