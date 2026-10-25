"""Policy loading and evaluation. Pure functions: no I/O, no clock, no network.

evaluate() answers one question for one request: allow, deny or escalate — and why.
The model never gets a vote here; it only produced the request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.money import fmt, pct

ALLOW, DENY, ESCALATE = "allow", "deny", "escalate"
MONEY_ACTIONS = ("discount", "hold", "release", "refund", "credit", "replacement")


@dataclass
class Policy:
    version: str
    raw: dict
    ttl_seconds: int
    max_ttl_seconds: int
    currencies: list[str]
    binding_required: bool
    system_actors: dict[str, list[str]]
    actions: dict[str, dict]
    ladder: list[dict] = field(default_factory=list)
    approval_link_ttl: int = 172800

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        authz = d.get("authorization", {})
        ttl = int(authz.get("ttl_seconds", 90))
        max_ttl = min(int(authz.get("max_ttl_seconds", 120)), 120)
        if ttl > max_ttl:
            raise ValueError(f"policy ttl_seconds {ttl} exceeds max_ttl_seconds {max_ttl}")
        ident = d.get("identity", {})
        return cls(
            version=str(d["version"]),
            raw=d,
            ttl_seconds=ttl,
            max_ttl_seconds=max_ttl,
            currencies=[c.upper() for c in d.get("currencies", ["USD"])],
            binding_required=bool(ident.get("binding_required", True)),
            system_actors={k: list(v) for k, v in ident.get("system_actors", {}).items()},
            actions=d.get("actions", {}),
            ladder=list(d.get("truce", {}).get("ladder", [])),
            approval_link_ttl=int(d.get("escalation", {}).get("approval_link_ttl_seconds", 172800)),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        import yaml

        from ..common.config import settings

        p = Path(path) if path else settings.policy_path
        with open(p, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh))

    def truce_options(self, order: dict) -> list[dict]:
        """Concrete, in-policy options for this order (what the agent may offer)."""
        out = []
        available = int(order.get("held_minor", 0)) - int(order.get("refunded_minor", 0)) - int(order.get("credited_minor", 0))
        for rung in self.ladder[:3]:
            amount = 0
            if rung.get("percent"):
                amount = min(int(available * rung["percent"] // 100), available)
            out.append({
                "kind": rung["kind"], "action": rung["action"], "label": rung.get("label", rung["kind"]),
                "amount_minor": amount, "currency": order.get("currency", "USD"),
                "display": f"{rung.get('label', rung['kind'])}" + (f" ({fmt(amount, order.get('currency', 'USD'))})" if amount else ""),
            })
        return out


def _truthy(v: Any) -> bool:
    """Evidence flags may arrive as bools, strings or ids. Only unambiguous truth counts."""
    if v is True:
        return True
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        s = v.strip().lower()
        return bool(s) and s not in {"false", "no", "0", "none", "null"}
    return False


def evaluate(policy: Policy, req: dict, order: dict | None, prior_refunds: int, approval: dict | None,
             now: int) -> tuple[str, str, list[str]]:
    """Return (outcome, reason, checks). `req` is the DecideRequest as a dict.
    `approval`, when present, is an already-verified supervisor approval (claims dict)."""
    checks: list[str] = []
    action = req.get("action")
    amount = req.get("amount_minor")
    currency = str(req.get("currency", "")).upper()
    actor = req.get("actor_sub")
    evidence = req.get("evidence") or {}
    rules = policy.actions.get(action or "")

    def deny(code: str, detail: str = "") -> tuple[str, str, list[str]]:
        checks.append(f"FAIL {code}")
        return DENY, f"{code}: {detail}" if detail else code, checks

    if not rules:
        return deny("unknown_action", f"'{action}' is not a money action this policy knows")
    if not isinstance(amount, int) or isinstance(amount, bool):
        return deny("amount_not_integer_minor_units", repr(amount))
    if currency not in policy.currencies:
        return deny("currency_not_allowed", currency or "(empty)")
    if order is None:
        return deny("order_not_found", str(req.get("order_id")))
    checks.append("ok order_exists")
    if order.get("currency", "USD").upper() != currency:
        return deny("currency_mismatch", f"order is {order.get('currency')}, request is {currency}")
    checks.append("ok currency")

    status = order.get("status")
    if status not in rules.get("allowed_states", []):
        return deny("invalid_order_state", f"{action} is not allowed while the order is {status}")
    checks.append(f"ok state:{status}")

    # identity binding ------------------------------------------------------
    if policy.binding_required:
        system = policy.system_actors.get(action)
        if system:
            if actor not in system:
                return deny("identity_binding", f"{action} may only be requested by {system}, not {actor!r}")
        elif not actor or actor != order.get("buyer_sub"):
            return deny("identity_binding", f"actor {actor!r} is not the owner of order {order.get('order_id')}")
        checks.append("ok identity_binding")

    # evidence --------------------------------------------------------------
    for key in rules.get("required_evidence", []):
        if not _truthy(evidence.get(key)):
            return deny("evidence_required", f"{action} requires evidence.{key} == true")
    if rules.get("required_evidence"):
        checks.append("ok evidence:" + ",".join(rules["required_evidence"]))

    # amounts ---------------------------------------------------------------
    order_amount = int(order.get("amount_minor", 0))
    held = int(order.get("held_minor", 0))
    available = held - int(order.get("refunded_minor", 0)) - int(order.get("credited_minor", 0))

    if rules.get("zero_amount"):
        if amount != 0:
            return deny("amount_must_be_zero", f"{action} moves no money; got {fmt(amount, currency)}")
        checks.append("ok zero_amount")
        return ALLOW, "in_policy", checks

    if amount <= 0:
        return deny("non_positive_amount", fmt(amount, currency))

    if action == "discount":
        list_amount = int(order.get("list_amount_minor", order_amount))
        requested_pct = pct(int(order.get("discount_minor", 0)) + amount, list_amount)
        if requested_pct > float(rules.get("max_percent", 0)) + 1e-9:
            return deny("discount_exceeds_policy",
                        f"{requested_pct:.1f}% total discount requested, policy allows {rules.get('max_percent')}%")
        checks.append(f"ok discount_pct:{requested_pct:.1f}")
        return ALLOW, "in_policy", checks

    if rules.get("must_equal") == "order_amount":
        if amount != order_amount:
            return deny("amount_mismatch", f"hold must equal order amount {fmt(order_amount, currency)}, got {fmt(amount, currency)}")
        checks.append("ok amount_equals_order")
        return ALLOW, "in_policy", checks

    if rules.get("must_equal") == "held_amount":
        if amount != available:
            return deny("amount_mismatch", f"release must equal held funds {fmt(available, currency)}, got {fmt(amount, currency)}")
        checks.append("ok amount_equals_held")
        return ALLOW, "in_policy", checks

    # refund / credit ---------------------------------------------------------
    if amount > available:
        return deny("amount_exceeds_held_funds",
                    f"requested {fmt(amount, currency)} but only {fmt(available, currency)} is held for order {order.get('order_id')}")
    checks.append("ok within_held_funds")

    velocity = rules.get("velocity")
    if velocity and prior_refunds >= int(velocity.get("max_per_buyer", 1)):
        return deny("velocity_exceeded",
                    f"buyer already received {prior_refunds} refund(s) in the last {velocity.get('window_days')} days")
    if velocity:
        checks.append("ok velocity")

    cap = int(rules.get("auto_cap_minor", 0))
    cap_pct = float(rules.get("auto_cap_percent_of_order", 100))
    over_cap = amount > cap or pct(amount, order_amount) > cap_pct + 1e-9
    if over_cap:
        if approval and approval.get("approved") is True:
            checks.append("ok supervisor_approved")
            return ALLOW, f"supervisor_approved: {approval.get('approver')} approved decision {approval.get('decision_id')}", checks
        checks.append("ESCALATE above_auto_cap")
        return ESCALATE, (f"above_auto_cap: {fmt(amount, currency)} exceeds the automatic cap "
                          f"({fmt(cap, currency)} and {cap_pct:.0f}% of order); needs supervisor approval"), checks
    checks.append("ok within_auto_cap")
    return ALLOW, "in_policy", checks
