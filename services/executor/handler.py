"""Lambda entrypoint: {"request": {...ExecRequest}, "authorization": "<jws>"} -> ExecResult."""
from __future__ import annotations

from .executor import ExecRequest, get_executor


def lambda_handler(event, context):
    try:
        req = ExecRequest.from_dict(event["request"])
    except (KeyError, TypeError) as e:
        return {"ok": False, "code": "bad_request", "reason": f"{type(e).__name__}: {e}"}
    return get_executor().execute(req, event.get("authorization")).to_dict()
