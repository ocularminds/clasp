"""The one way any Clasp code asks for permission to move money.

    decision = agentsafe.decide(action="refund", order_id=..., amount_minor=2400, ...)

Resolution order: AGENTSAFE_URL (HTTP, Decionis or the deployed reference API)
 -> AGENTSAFE_FUNCTION_ARN (Lambda invoke) -> in-process reference service.
"""
from __future__ import annotations

import json
from typing import Any

from ..common.config import settings
from ..common.log import Log
from .service import DecideRequest, Decision

log = Log("agentsafe.adapter")


class AgentSafeClient:
    def __init__(self, url: str | None = None, function_arn: str | None = None, service=None):
        self.url = url
        self.function_arn = function_arn
        self._service = service

    def decide(self, *, action: str, order_id: str, amount_minor: int, currency: str, actor_sub: str,
               evidence: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
               correlation_id: str = "") -> Decision:
        req = DecideRequest(action=action, order_id=order_id, amount_minor=amount_minor, currency=currency,
                            actor_sub=actor_sub, evidence=dict(evidence or {}), context=dict(context or {}),
                            correlation_id=correlation_id)
        if self.url:
            return self._via_http(req)
        if self.function_arn:
            return self._via_lambda(req)
        return self._via_inprocess(req)

    def approval_links(self, *, order_id: str, decision_id: str, amount_minor: int, currency: str, approver: str) -> dict:
        """Signed approve/reject tokens for the supervisor email. Minted by the policy service, so KMS Sign
        stays with it. (Extension to the /decide contract: POST /approval-links.)"""
        body = {"op": "approval_links", "order_id": order_id, "decision_id": decision_id, "amount_minor": amount_minor,
                "currency": currency, "approver": approver}
        if self.url:
            import requests

            r = requests.post(f"{self.url.rstrip('/')}/approval-links", json=body, timeout=10)
            r.raise_for_status()
            return r.json()
        if self.function_arn:
            from ..common.aws import client

            resp = client("lambda").invoke(FunctionName=self.function_arn, Payload=json.dumps(body).encode())
            out = json.loads(resp["Payload"].read())
            return json.loads(out["body"]) if isinstance(out, dict) and "body" in out else out
        if self._service is None:
            from .service import get_service

            self._service = get_service()
        return self._service.approval_links(order_id, decision_id, amount_minor, currency, approver)

    def _via_http(self, req: DecideRequest) -> Decision:
        import requests

        r = requests.post(f"{self.url.rstrip('/')}/decide", json=req.to_dict(), timeout=10)
        r.raise_for_status()
        return Decision.from_dict(r.json())

    def _via_lambda(self, req: DecideRequest) -> Decision:
        from ..common.aws import client

        resp = client("lambda").invoke(FunctionName=self.function_arn, Payload=json.dumps(req.to_dict()).encode())
        body = json.loads(resp["Payload"].read())
        if isinstance(body, dict) and "body" in body and "statusCode" in body:  # API-shaped
            body = json.loads(body["body"])
        return Decision.from_dict(body)

    def _via_inprocess(self, req: DecideRequest) -> Decision:
        if self._service is None:
            from .service import get_service

            self._service = get_service()
        return self._service.decide(req)


_client: AgentSafeClient | None = None


def get_client() -> AgentSafeClient:
    global _client
    if _client is None:
        _client = AgentSafeClient(url=settings.agentsafe_url, function_arn=settings.agentsafe_function_arn)
    return _client


def set_client(client: AgentSafeClient | None) -> None:
    global _client
    _client = client


def decide(**kwargs) -> Decision:
    return get_client().decide(**kwargs)


def approval_links(**kwargs) -> dict:
    return get_client().approval_links(**kwargs)
