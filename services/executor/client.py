"""How callers reach the executor: in-process, or the dedicated Lambda when deployed.
Only the executor's role can write the ledger; tools merely hand it a request + authorization."""
from __future__ import annotations

import json

from ..common.config import settings
from .executor import ExecRequest, ExecResult


def execute(req: ExecRequest, authorization: str | None) -> ExecResult:
    arn = settings.executor_function_arn
    if arn:
        from ..common.aws import client

        resp = client("lambda").invoke(FunctionName=arn, Payload=json.dumps(
            {"request": req.to_dict(), "authorization": authorization}).encode())
        return ExecResult.from_dict(json.loads(resp["Payload"].read()))
    from .executor import get_executor

    return get_executor().execute(req, authorization)
