"""The one way any Clasp code asks for permission to move money.

    decision = agentsafe.decide(action="refund", order_id=..., amount_minor=2400, ...)

Resolution order:
  AGENTSAFE_URL      the Decionis AgentSafe hosted gateway (e.g. https://dci.decionisedge.com). The gateway
                     evaluates the call, records a Decionis dossier, and relays it to the tenant's upstream,
                     which is Clasp's own POST /decide (the reference service, which signs the authorization).
                     In enforcement mode it can block (403) or hold (202) the call itself.
  failure            enforcement (AGENTSAFE_GATEWAY_MODE, the default): the gateway is the only way to a decision.
                     If it can't answer (unreachable, rate-limited, key or origin proof rejected), the call is
                     denied as `decionis_unavailable` and nothing moves: going around an enforcing gateway is
                     no fallback. shadow: the gateway only observes, so the reference service decides directly
                     (AGENTSAFE_FUNCTION_ARN or in-process). A gateway block is final in both modes.
Without AGENTSAFE_URL the reference service decides directly.
"""
from __future__ import annotations

import json
from typing import Any

from ..common.config import settings
from ..common.ids import new_id
from ..common.log import Log
from .service import DecideRequest, Decision

log = Log("agentsafe.adapter")


class GatewayUnavailable(Exception):
    """The gateway gave no decision (transport error, auth, rate limit, 5xx). Safe to fall back."""


class AgentSafeClient:
    def __init__(self, url: str | None = None, function_arn: str | None = None, service=None,
                 tenant_key: str | None = None, upstream_token: str | None = None, timeout: float = 15.0,
                 mode: str = "enforcement"):
        self.url = url
        self.function_arn = function_arn
        self._service = service
        self.tenant_key = tenant_key
        self.upstream_token = upstream_token
        self.timeout = timeout
        self.mode = mode

    def decide(self, *, action: str, order_id: str, amount_minor: int, currency: str, actor_sub: str,
               evidence: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
               correlation_id: str = "") -> Decision:
        req = DecideRequest(action=action, order_id=order_id, amount_minor=amount_minor, currency=currency,
                            actor_sub=actor_sub, evidence=dict(evidence or {}), context=dict(context or {}),
                            correlation_id=correlation_id)
        if self.url:
            try:
                return self._via_gateway(req)
            except GatewayUnavailable as e:
                if self.mode == "enforcement":  # fail closed: nothing moves without the gateway's verdict
                    log.error("agentsafe.gateway_unavailable", reason=str(e), order_id=order_id, action=action,
                              correlation_id=correlation_id or None)
                    return self._record_external(req, "deny", f"decionis_unavailable: {e}", {})
                log.warn("agentsafe.gateway_fallback", reason=str(e), order_id=order_id, action=action,
                         correlation_id=correlation_id or None)
                req.context["gateway_fallback"] = str(e)
        return self._direct(req)

    def _direct(self, req: DecideRequest) -> Decision:
        return self._via_lambda(req) if self.function_arn else self._via_inprocess(req)

    # -- Decionis hosted gateway -------------------------------------------------------
    def _via_gateway(self, req: DecideRequest) -> Decision:
        import requests

        if not self.tenant_key:
            raise GatewayUnavailable("no tenant key configured")
        idem = new_id("req")
        headers = {"content-type": "application/json", "AgentSafe-Tenant-Key": self.tenant_key,
                   "Idempotency-Key": idem, "X-Correlation-Id": req.correlation_id or idem}
        if self.upstream_token:
            headers["Authorization"] = f"Bearer {self.upstream_token}"
        try:
            r = requests.post(f"{self.url.rstrip('/')}/decide", data=json.dumps(req.to_dict()), headers=headers,
                              timeout=self.timeout)
        except requests.RequestException as e:
            raise GatewayUnavailable(type(e).__name__) from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        execution = r.headers.get("agentsafe-execution") or ""
        mode = r.headers.get("agentsafe-mode") or ("SHADOW" if execution == "PASSTHROUGH" else "ENFORCEMENT")
        if 200 <= r.status_code < 300 and body.get("decision") in {"allow", "deny", "escalate"}:
            d = Decision.from_dict(body)  # decided and recorded by the reference service behind the gateway
            d.engine = f"decionis-gateway:{mode.lower()}"
            log.info("agentsafe.gateway", decision=d.decision, mode=mode, execution=execution,
                     dossier_id=r.headers.get("agentsafe-dossier-id"), order_id=req.order_id,
                     correlation_id=req.correlation_id or None)
            return d
        state = body.get("state") or r.headers.get("agentsafe-state")
        if (r.status_code, state) in {(403, "BLOCK"), (202, "ESCALATE")}:
            # Enforcement: the gateway itself refused or held the call; the upstream never saw it.
            # (A held call is handled as our own escalation: the seller, then a supervisor, decide.)
            codes = ",".join(body.get("reason_codes") or []) or state.lower()
            outcome = "deny" if state == "BLOCK" else "escalate"
            refs = {k: body.get(k) for k in ("decision_id", "dossier_id", "intent_hash") if body.get(k)}
            return self._record_external(req, outcome, f"decionis_{state.lower()}: {codes}", refs)
        where = "upstream" if execution in {"FORWARDED", "PASSTHROUGH"} else "gateway"
        raise GatewayUnavailable(f"{where} HTTP {r.status_code} {body.get('code') or state or body.get('reason') or ''}".strip())

    def _record_external(self, req: DecideRequest, outcome: str, reason: str, refs: dict) -> Decision:
        payload = {"op": "record_external", "request": req.to_dict(), "decision": outcome, "reason": reason,
                   "engine": "decionis-gateway", "refs": refs}
        if self.function_arn:
            from ..common.aws import client

            resp = client("lambda").invoke(FunctionName=self.function_arn, Payload=json.dumps(payload).encode())
            return Decision.from_dict(json.loads(resp["Payload"].read()))
        return self._service_obj().record_external(req, outcome, reason, "decionis-gateway", refs)

    # -- reference service ---------------------------------------------------------------
    def _service_obj(self):
        if self._service is None:
            from .service import get_service

            self._service = get_service()
        return self._service

    def approval_links(self, *, order_id: str, decision_id: str, amount_minor: int, currency: str, approver: str,
                       stage: str = "supervisor") -> dict:
        """Signed approve/reject tokens for the supervisor email. Minted by the policy service, so KMS Sign
        stays with it. (Extension to the /decide contract: POST /approval-links.)"""
        body = {"op": "approval_links", "order_id": order_id, "decision_id": decision_id, "amount_minor": amount_minor,
                "currency": currency, "approver": approver, "stage": stage}
        if self.function_arn:
            from ..common.aws import client

            resp = client("lambda").invoke(FunctionName=self.function_arn, Payload=json.dumps(body).encode())
            out = json.loads(resp["Payload"].read())
            return json.loads(out["body"]) if isinstance(out, dict) and "body" in out else out
        return self._service_obj().approval_links(order_id, decision_id, amount_minor, currency, approver, stage)

    def _via_lambda(self, req: DecideRequest) -> Decision:
        from ..common.aws import client

        resp = client("lambda").invoke(FunctionName=self.function_arn, Payload=json.dumps(req.to_dict()).encode())
        body = json.loads(resp["Payload"].read())
        if isinstance(body, dict) and "body" in body and "statusCode" in body:  # API-shaped
            body = json.loads(body["body"])
        return Decision.from_dict(body)

    def _via_inprocess(self, req: DecideRequest) -> Decision:
        return self._service_obj().decide(req)


_client: AgentSafeClient | None = None


def get_client() -> AgentSafeClient:
    global _client
    if _client is None:
        url = settings.agentsafe_url
        tenant_key = upstream_token = None
        if url:
            try:
                tenant_key, upstream_token = settings.agentsafe_tenant_key, settings.agentsafe_upstream_token
            except Exception as e:  # noqa: BLE001 — unreadable secrets mean no gateway, never no decision
                log.error("agentsafe.gateway_secrets_unavailable", error=str(e)[:200])
        _client = AgentSafeClient(url=url, function_arn=settings.agentsafe_function_arn, tenant_key=tenant_key,
                                  upstream_token=upstream_token, timeout=settings.agentsafe_gateway_timeout,
                                  mode=settings.agentsafe_gateway_mode)
    return _client


def set_client(client: AgentSafeClient | None) -> None:
    global _client
    _client = client


def decide(**kwargs) -> Decision:
    return get_client().decide(**kwargs)


def approval_links(**kwargs) -> dict:
    return get_client().approval_links(**kwargs)
