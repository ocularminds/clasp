"""Money proposals. Every function here is a *proposal*: it asks AgentSafe, and only
hands an `allow` (with its signed authorization) to the executor. Nothing here can
write the ledger; the static test in tests/test_boundary.py keeps it that way."""
from __future__ import annotations

from typing import Any

from ..agentsafe import adapter as agentsafe
from ..common.log import Log
from ..common.money import fmt
from ..executor import client as executor
from ..executor.executor import ExecRequest

log = Log("tools.money")


def propose(action: str, *, order_id: str, amount_minor: int, currency: str, actor_sub: str,
            evidence: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None, correlation_id: str = "") -> dict:
    """decide -> (allow ? execute : report). Returns a flat dict the agent can read aloud."""
    decision = agentsafe.decide(action=action, order_id=order_id, amount_minor=amount_minor, currency=currency,
                                actor_sub=actor_sub, evidence=evidence or {}, context=context or {},
                                correlation_id=correlation_id)
    out: dict[str, Any] = {
        "action": action, "order_id": order_id, "amount": fmt(amount_minor, currency), "amount_minor": amount_minor,
        "currency": currency, "decision": decision.decision, "reason": decision.reason, "decision_id": decision.decision_id,
        "policy_version": decision.policy_version,
    }
    if decision.decision != "allow":
        log.info("proposal.not_executed", action=action, order_id=order_id, decision=decision.decision,
                 reason=decision.reason, correlation_id=correlation_id or None)
        return out
    result = executor.execute(
        ExecRequest(action=action, order_id=order_id, amount_minor=amount_minor, currency=currency, actor_sub=actor_sub,
                    correlation_id=correlation_id, metadata=metadata or {}),
        decision.authorization,
    )
    out.update({"executed": result.ok, "execution_code": result.code, "execution_reason": result.reason,
                "ledger_entry_id": result.entry_id, "status": result.status})
    return out
