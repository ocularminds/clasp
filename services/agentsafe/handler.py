"""Lambda entrypoint for the reference policy service.
Accepts a direct invoke payload or an API Gateway HTTP API event for POST /decide."""
from __future__ import annotations

import json

from .service import DecideRequest, get_service


def _parse(event: dict) -> dict:
    if "body" in event and isinstance(event.get("body"), str):
        return json.loads(event["body"] or "{}")
    return event


def lambda_handler(event, context):
    payload = _parse(event)
    try:
        req = DecideRequest.from_dict(payload)
        decision = get_service().decide(req)
        body = decision.to_dict()
        status = 200
    except Exception as e:  # noqa: BLE001 — a broken request is a deny, never an allow
        body = {"decision": "deny", "reason": f"malformed_request: {type(e).__name__}: {e}", "decision_id": "-",
                "policy_version": "-"}
        status = 400
    if "requestContext" in event:  # API Gateway
        return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}
    return body
