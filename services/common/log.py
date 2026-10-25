"""Structured JSON logging. Every agent turn, decision and ledger write carries correlation_id."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

_root_configured = False


def _configure() -> None:
    global _root_configured
    if _root_configured:
        return
    root = logging.getLogger()
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler(sys.stdout))
    for h in root.handlers:
        h.setFormatter(logging.Formatter("%(message)s"))
    _root_configured = True


class Log:
    def __init__(self, component: str, **bound: Any):
        _configure()
        self._logger = logging.getLogger(component)
        self.component = component
        self.bound = dict(bound)

    def bind(self, **more: Any) -> "Log":
        merged = {**self.bound, **{k: v for k, v in more.items() if v is not None}}
        return Log(self.component, **merged)

    def _emit(self, level: int, event: str, fields: dict[str, Any]) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": logging.getLevelName(level),
            "component": self.component,
            "event": event,
            **self.bound,
            **{k: v for k, v in fields.items() if v is not None},
        }
        self._logger.log(level, json.dumps(record, default=str, ensure_ascii=False))

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields)
