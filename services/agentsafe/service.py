"""Reference AgentSafe policy service. Same contract as Decionis AgentSafe:

POST /decide {action, order_id, amount_minor, currency, actor_sub, evidence, context}
 -> {decision: allow|deny|escalate, reason, decision_id, authorization?}

An `allow` carries a compact JWS (ES256, KMS-signed) that the executor demands.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..common import clock
from ..common.ids import new_id, new_nonce
from ..common.log import Log
from . import jws
from .policy import ALLOW, DENY, ESCALATE, Policy, evaluate
from .signer import ALG, Signer, Verifier

log = Log("agentsafe")


@dataclass
class DecideRequest:
    action: str
    order_id: str
    amount_minor: int
    currency: str
    actor_sub: str
    evidence: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DecideRequest":
        amount = d.get("amount_minor")
        if isinstance(amount, str) and amount.strip().lstrip("-").isdigit():
            amount = int(amount)
        return cls(
            action=str(d.get("action", "")),
            order_id=str(d.get("order_id", "")),
            amount_minor=amount,  # type: ignore[arg-type]
            currency=str(d.get("currency", "")),
            actor_sub=str(d.get("actor_sub", "")),
            evidence=dict(d.get("evidence") or {}),
            context=dict(d.get("context") or {}),
            correlation_id=str(d.get("correlation_id") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    decision: str
    reason: str
    decision_id: str
    policy_version: str
    authorization: str | None = None
    checks: list[str] = field(default_factory=list)
    expires_at: int | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["authorization"] is None:
            d.pop("authorization")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        return cls(
            decision=d["decision"], reason=d.get("reason", ""), decision_id=d.get("decision_id", ""),
            policy_version=d.get("policy_version", ""), authorization=d.get("authorization"),
            checks=list(d.get("checks") or []), expires_at=d.get("expires_at"),
        )


class AgentSafeService:
    def __init__(self, policy: Policy, stores, signer: Signer, verifier: Verifier | None = None):
        self.policy = policy
        self.stores = stores
        self.signer = signer
        self.verifier = verifier

    # -- approvals -----------------------------------------------------------
    def mint_approval_link_token(self, order_id: str, decision_id: str, amount_minor: int, currency: str,
                                 approver: str, verdict: str) -> str:
        now = clock.now()
        claims = {
            "typ": "approval", "order_id": order_id, "decision_id": decision_id, "amount_minor": amount_minor,
            "currency": currency, "approver": approver, "approved": verdict == "approve",
            "nonce": new_nonce(), "iat": now, "exp": now + self.policy.approval_link_ttl,
        }
        return jws.encode({"alg": ALG, "typ": "JWT", "kid": self.signer.kid}, claims, self.signer.sign)

    def verify_approval_token(self, token: str | None, req: DecideRequest) -> dict | None:
        """Return the approval claims only if the token is ours, unexpired and about this request."""
        if not token or not self.verifier:
            return None
        try:
            header, claims, signing_input, sig = jws.split(token)
        except jws.TokenError:
            return None
        if header.get("alg") != ALG or header.get("kid") != self.verifier.kid:
            return None
        if not self.verifier.verify(signing_input, sig):
            return None
        if claims.get("typ") != "approval" or int(claims.get("exp", 0)) <= clock.now():
            return None
        if (claims.get("order_id"), claims.get("amount_minor"), str(claims.get("currency", "")).upper()) != (
            req.order_id, req.amount_minor, req.currency.upper()
        ):
            return None
        prior = [d for d in self.stores.decisions_for_order(req.order_id) if d.get("decision_id") == claims.get("decision_id")]
        if not prior or prior[0].get("decision") != ESCALATE:
            return None
        return claims

    # -- the decision --------------------------------------------------------
    def decide(self, req: DecideRequest) -> Decision:
        now = clock.now()
        clog = log.bind(correlation_id=req.correlation_id or None, order_id=req.order_id, action=req.action)
        order = self.stores.get_order(req.order_id) if req.order_id else None
        prior_refunds = 0
        rules = self.policy.actions.get(req.action, {})
        if order and rules.get("velocity"):
            window = int(rules["velocity"].get("window_days", 30)) * 86400
            prior_refunds = self.stores.count_refunds_for_buyer(order.get("buyer_sub", ""), now - window)
        approval = self.verify_approval_token(req.context.get("approval_token"), req)
        outcome, reason, checks = evaluate(self.policy, req.to_dict(), order, prior_refunds, approval, now)

        decision_id = new_id("dec")
        authorization = None
        expires_at = None
        if outcome == ALLOW:
            expires_at = now + self.policy.ttl_seconds
            claims = {
                "action": req.action, "order_id": req.order_id, "amount_minor": req.amount_minor,
                "currency": req.currency.upper(), "actor_sub": req.actor_sub,
                "policy_version": self.policy.version, "decision_id": decision_id,
                "nonce": new_nonce(), "iat": now, "exp": expires_at,
            }
            authorization = jws.encode({"alg": ALG, "typ": "JWT", "kid": self.signer.kid}, claims, self.signer.sign)

        record = {
            "decision_id": decision_id, "order_id": req.order_id or "-", "action": req.action,
            "amount_minor": req.amount_minor if isinstance(req.amount_minor, int) else str(req.amount_minor),
            "currency": req.currency.upper(), "actor_sub": req.actor_sub, "decision": outcome, "reason": reason,
            "checks": checks, "policy_version": self.policy.version, "evidence": req.evidence,
            "context": {k: v for k, v in req.context.items() if k != "approval_token"},
            "correlation_id": req.correlation_id, "ts": str(now).zfill(12),
            "authorization_expires_at": expires_at,
        }
        self.stores.put_decision(record)
        (clog.info if outcome == ALLOW else clog.warn)(
            "agentsafe.decision", decision=outcome, reason=reason, decision_id=decision_id,
            amount_minor=req.amount_minor, currency=req.currency, actor_sub=req.actor_sub, checks=checks,
        )
        return Decision(decision=outcome, reason=reason, decision_id=decision_id, policy_version=self.policy.version,
                        authorization=authorization, checks=checks, expires_at=expires_at)


_service: AgentSafeService | None = None


def get_service() -> AgentSafeService:
    """Build the reference service from env (policy file, stores, KMS or local signer)."""
    global _service
    if _service is None:
        from ..common.stores import get_stores
        from .signer import get_signer, get_verifier

        _service = AgentSafeService(Policy.load(), get_stores(), get_signer(), get_verifier())
    return _service


def set_service(service: AgentSafeService | None) -> None:
    global _service
    _service = service
