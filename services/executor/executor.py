"""The executor: the only code that writes the ledger or changes a money state.

It will not act on a request unless the accompanying authorization is
  * a well-formed compact JWS signed by the configured key (ES256, exact kid),
  * unexpired, with a lifetime no longer than AUTHZ_MAX_TTL_SECONDS (<= 120s),
  * single-use (nonce consumed atomically with the ledger write),
  * an exact match for the request: action, order_id, amount_minor, currency, actor_sub.
Anything else is refused with a reason and nothing is written.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..agentsafe import jws
from ..agentsafe.signer import ALG, Verifier
from ..common import clock
from ..common.config import settings
from ..common.ids import new_id
from ..common.log import Log
from ..common.stores import ConflictError, ReplayError

log = Log("executor")

# action -> (states it may run from, status afterwards or None for "unchanged")
TRANSITIONS: dict[str, tuple[list[str], str | None]] = {
    "discount": (["DRAFT", "PAYMENT_PENDING"], None),
    "hold": (["PAYMENT_PENDING"], "HELD"),
    "release": (["DISPATCHED", "DELIVERED"], "RELEASED"),
    "refund": (["DISPUTED", "ESCALATED"], "RESOLVED_REFUND"),  # or RESOLVED_PARTIAL_REFUND
    "credit": (["DISPUTED", "ESCALATED"], "RESOLVED_CREDIT"),
    "replacement": (["DISPUTED", "ESCALATED"], "RESOLVED_REPLACEMENT"),
}
EXACT_CLAIMS = ("action", "order_id", "amount_minor", "currency", "actor_sub")


@dataclass
class ExecRequest:
    action: str
    order_id: str
    amount_minor: int
    currency: str
    actor_sub: str
    correlation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ExecRequest":
        return cls(action=d["action"], order_id=d["order_id"], amount_minor=d["amount_minor"], currency=d["currency"],
                   actor_sub=d["actor_sub"], correlation_id=d.get("correlation_id", ""), metadata=dict(d.get("metadata") or {}))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecResult:
    ok: bool
    code: str
    reason: str
    entry_id: str | None = None
    decision_id: str | None = None
    status: str | None = None
    order: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExecResult":
        return cls(**{k: d.get(k) for k in ("ok", "code", "reason", "entry_id", "decision_id", "status", "order")})


class Executor:
    def __init__(self, verifier: Verifier, stores, workflow=None, max_ttl: int | None = None):
        self.verifier = verifier
        self.stores = stores
        self.workflow = workflow
        self.max_ttl = min(int(max_ttl if max_ttl is not None else settings.authz_max_ttl), 120)

    # -- verification ----------------------------------------------------------
    def verify_authorization(self, req: ExecRequest, authorization: str | None) -> tuple[dict | None, ExecResult | None]:
        if not authorization:
            return None, ExecResult(False, "missing_authorization", "no AgentSafe authorization was presented")
        try:
            header, claims, signing_input, sig = jws.split(authorization)
        except jws.TokenError as e:
            return None, ExecResult(False, "bad_token", f"authorization is not a compact JWS: {e}")
        if header.get("alg") != ALG:
            return None, ExecResult(False, "bad_token", f"unexpected alg {header.get('alg')!r}")
        if header.get("kid") != self.verifier.kid:
            return None, ExecResult(False, "bad_signature", f"authorization signed by unknown key {header.get('kid')!r}")
        if not self.verifier.verify(signing_input, sig):
            return None, ExecResult(False, "bad_signature", "signature does not verify against the AgentSafe key")

        now = clock.now()
        exp, iat = claims.get("exp"), claims.get("iat")
        if not isinstance(exp, int) or not isinstance(iat, int):
            return None, ExecResult(False, "bad_token", "exp/iat missing")
        if exp <= now:
            return None, ExecResult(False, "expired", f"authorization expired at {exp}, now {now}")
        if exp - iat > self.max_ttl:
            return None, ExecResult(False, "ttl_too_long", f"authorization lifetime {exp - iat}s exceeds {self.max_ttl}s")
        for k in ("nonce", "decision_id", "policy_version"):
            if not claims.get(k):
                return None, ExecResult(False, "bad_token", f"claim {k} missing")

        requested = {"action": req.action, "order_id": req.order_id, "amount_minor": req.amount_minor,
                     "currency": req.currency.upper(), "actor_sub": req.actor_sub}
        for k in EXACT_CLAIMS:
            if claims.get(k) != requested[k] or type(claims.get(k)) is not type(requested[k]):
                return None, ExecResult(False, "mismatch",
                                        f"authorization.{k}={claims.get(k)!r} does not match request.{k}={requested[k]!r}")
        return claims, None

    # -- execution ---------------------------------------------------------------
    def execute(self, req: ExecRequest, authorization: str | None) -> ExecResult:
        elog = log.bind(correlation_id=req.correlation_id or None, order_id=req.order_id, action=req.action)
        claims, failure = self.verify_authorization(req, authorization)
        if failure:
            elog.warn("executor.refused", code=failure.code, reason=failure.reason, amount_minor=req.amount_minor)
            return failure
        assert claims is not None

        if self.stores.nonce_consumed(claims["nonce"]):
            res = ExecResult(False, "replay", "authorization nonce already used", decision_id=claims["decision_id"])
            elog.warn("executor.refused", code=res.code, reason=res.reason)
            return res

        order = self.stores.get_order(req.order_id)
        if order is None:
            return ExecResult(False, "order_not_found", req.order_id, decision_id=claims["decision_id"])
        allowed_states, next_status = TRANSITIONS[req.action]
        if order.get("status") not in allowed_states:
            res = ExecResult(False, "invalid_state", f"order is {order.get('status')}, {req.action} needs one of {allowed_states}",
                             decision_id=claims["decision_id"])
            elog.warn("executor.refused", code=res.code, reason=res.reason)
            return res

        updates, entry_type = self._plan(req, order, next_status)
        entry = {
            "order_id": req.order_id, "entry_id": new_id("led"), "type": entry_type,
            "amount_minor": req.amount_minor, "currency": req.currency.upper(), "actor_sub": req.actor_sub,
            "buyer_sub": order.get("buyer_sub"), "decision_id": claims["decision_id"], "nonce": claims["nonce"],
            "policy_version": claims["policy_version"], "correlation_id": req.correlation_id,
            "ts": str(clock.now()).zfill(12), "metadata": req.metadata,
            "status_before": order.get("status"), "status_after": updates.get("status", order.get("status")),
        }
        try:
            self.stores.commit_money_move(claims["nonce"], claims["exp"] + 3600, entry, req.order_id, updates, allowed_states)
        except ReplayError:
            res = ExecResult(False, "replay", "authorization nonce already used", decision_id=claims["decision_id"])
            elog.warn("executor.refused", code=res.code, reason=res.reason)
            return res
        except ConflictError as e:
            res = ExecResult(False, "invalid_state", str(e), decision_id=claims["decision_id"])
            elog.warn("executor.refused", code=res.code, reason=res.reason)
            return res

        after = self.stores.get_order(req.order_id)
        elog.info("ledger.append", entry_id=entry["entry_id"], type=entry_type, amount_minor=req.amount_minor,
                  currency=req.currency, decision_id=claims["decision_id"], status=after.get("status"))
        result = ExecResult(True, "executed", f"{entry_type} {req.amount_minor} {req.currency.upper()} recorded",
                            entry_id=entry["entry_id"], decision_id=claims["decision_id"], status=after.get("status"), order=after)
        if self.workflow is not None:
            try:
                self.workflow.advance_after(req.action, order, after, {
                    "entry_id": entry["entry_id"], "decision_id": claims["decision_id"], "status": after.get("status"),
                    "amount_minor": req.amount_minor, "currency": req.currency.upper(), **req.metadata,
                })
            except Exception as e:  # noqa: BLE001 — the ledger is truth; workflow catch-up is logged
                elog.error("workflow.advance_failed", error=str(e))
        return result

    @staticmethod
    def _plan(req: ExecRequest, order: dict, next_status: str | None) -> tuple[dict, str]:
        a = req.amount_minor
        if req.action == "discount":
            return {"amount_minor": int(order["amount_minor"]) - a, "discount_minor": int(order.get("discount_minor", 0)) + a}, "DISCOUNT"
        if req.action == "hold":
            return {"status": "HELD", "held_minor": a, "psp_event_id": req.metadata.get("psp_event_id"), "held_at": clock.now()}, "HOLD"
        if req.action == "release":
            return {"status": "RELEASED", "released_minor": a, "released_at": clock.now()}, "RELEASE"
        if req.action == "refund":
            available = int(order.get("held_minor", 0)) - int(order.get("refunded_minor", 0)) - int(order.get("credited_minor", 0))
            status = "RESOLVED_REFUND" if a >= available else "RESOLVED_PARTIAL_REFUND"
            return {"status": status, "refunded_minor": int(order.get("refunded_minor", 0)) + a, "resolution": status,
                    "resolved_at": clock.now()}, "REFUND"
        if req.action == "credit":
            return {"status": "RESOLVED_CREDIT", "credited_minor": int(order.get("credited_minor", 0)) + a,
                    "resolution": "RESOLVED_CREDIT", "resolved_at": clock.now()}, "CREDIT"
        if req.action == "replacement":
            return {"status": "RESOLVED_REPLACEMENT", "resolution": "RESOLVED_REPLACEMENT", "resolved_at": clock.now()}, "REPLACEMENT"
        raise ValueError(req.action)


_executor: Executor | None = None


def get_executor() -> Executor:
    global _executor
    if _executor is None:
        from ..agentsafe.signer import get_verifier
        from ..common.stores import get_stores
        from ..workflows.client import get_driver

        _executor = Executor(get_verifier(), get_stores(), get_driver())
    return _executor


def set_executor(executor: Executor | None) -> None:
    global _executor
    _executor = executor
