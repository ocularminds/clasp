"""A clock the tests can freeze and advance. Everything time-based goes through now()."""
from __future__ import annotations

import time

_frozen: int | None = None
_offset: int = 0


def now() -> int:
    if _frozen is not None:
        return _frozen + _offset
    return int(time.time()) + _offset


def freeze(ts: int) -> None:
    global _frozen, _offset
    _frozen, _offset = int(ts), 0


def advance(seconds: int) -> None:
    global _offset
    _offset += int(seconds)


def reset() -> None:
    global _frozen, _offset
    _frozen, _offset = None, 0
