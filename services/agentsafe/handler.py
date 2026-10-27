"""Lambda entrypoint for the reference policy service.

Direct invoke (other Clasp Lambdas): a DecideRequest, {"op": "approval_links", ...} or {"op": "record_external", ...}.
API Gateway POST /decide: the upstream of the Decionis AgentSafe hosted gateway. It answers only callers that present
the upstream token in AGENTSAFE_UPSTREAM_HEADER (an enforcing gateway sets it from its upstreamCredential; in shadow
Clasp's services send it) and records the gateway's x-agent-safe-* references with the decision, so every logged
decision points at its Decionis dossier.
API Gateway GET /.well-known/agentsafe-upstream: the origin proof an enforcing gateway checks before it forwards."""
from __future__ import annotations

import hmac
import json

from ..common.config import settings
from .service import DecideRequest, get_service

GATEWAY_HEADERS = {"x-agent-safe-decision-id": "decision_id", "x-agent-safe-dossier-id": "dossier_id",
                   "x-agent-safe-intent-hash": "intent_hash"}


def _parse(event: dict) -> dict:
    if "body" in event and isinstance(event.get("body"), str):
        return json.loads(event["body"] or "{}")
    return event


def _authorized(event: dict) -> bool:
    expected = settings.agentsafe_upstream_token
    if not expected:
        return False  # fail closed
    got = {k.lower(): v for k, v in (event.get("headers") or {}).items()}.get(settings.agentsafe_upstream_header, "")
    bare = got[7:] if got.lower().startswith("bearer ") else got  # "Bearer …" for authorization, the bare key elsewhere
    return hmac.compare_digest(bare.encode(), expected.encode())


def _http(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def lambda_handler(event, context):
    via_http = "requestContext" in event
    if via_http and (event.get("rawPath") or "").endswith("/.well-known/agentsafe-upstream"):
        proof = settings.agentsafe_upstream_proof
        return {"statusCode": 200 if proof else 404, "headers": {"content-type": "text/plain; charset=utf-8",
                                                                 "cache-control": "no-store"},
                "body": (proof.strip() + "\n") if proof else "no proof configured\n"}
    if via_http and not _authorized(event):
        return _http(401, {"decision": "deny", "reason": "unauthorized_caller", "decision_id": "-", "policy_version": "-"})
    payload = _parse(event)
    try:
        if payload.get("op") == "approval_links" or (event.get("rawPath") or "").endswith("/approval-links"):
            body = get_service().approval_links(str(payload["order_id"]), str(payload["decision_id"]),
                                                int(payload["amount_minor"]), str(payload["currency"]), str(payload["approver"]),
                                                str(payload.get("stage") or "supervisor"))
            status = 200
        elif payload.get("op") == "record_external" and not via_http:
            req = DecideRequest.from_dict(payload["request"])
            body = get_service().record_external(req, str(payload["decision"]), str(payload["reason"]),
                                                 str(payload.get("engine") or "external"), dict(payload.get("refs") or {})).to_dict()
            status = 200
        else:
            req = DecideRequest.from_dict(payload)
            if via_http:
                headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
                refs = {name: headers[h] for h, name in GATEWAY_HEADERS.items() if headers.get(h)}
                req.context.pop("decionis", None)  # only the gateway's own headers may set this
                if refs:
                    req.context["decionis"] = refs
            decision = get_service().decide(req)
            body = decision.to_dict()
            status = 200
    except Exception as e:  # noqa: BLE001 — a broken request is a deny, never an allow
        body = {"decision": "deny", "reason": f"malformed_request: {type(e).__name__}: {e}", "decision_id": "-",
                "policy_version": "-"}
        status = 400
    return _http(status, body) if via_http else body
