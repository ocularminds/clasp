"""Policy loading and evaluation. Pure functions: no I/O, no clock, no network.

evaluate() answers one question for one request: allow, deny or escalate — and why.
The model never gets a vote here; it only produced the request.
describe() turns that answer into one plain sentence the agent can relay as-is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.money import fmt, pct

ALLOW, DENY, ESCALATE = "allow", "deny", "escalate"
MONEY_ACTIONS = ("discount", "hold", "release", "settle", "refund", "credit", "replacement")
TRUCE_ACTIONS = ("refund", "credit", "replacement")


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
    drift: dict = field(default_factory=dict)
    stages: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        authz = d.get("authorization", {})
        ttl = int(authz.get("ttl_seconds", 90))
        max_ttl = min(int(authz.get("max_ttl_seconds", 120)), 120)
        if ttl > max_ttl:
            raise ValueError(f"policy ttl_seconds {ttl} exceeds max_ttl_seconds {max_ttl}")
        ident = d.get("identity", {})
        esc = d.get("escalation", {})
        stages = list(esc.get("stages") or [{"role": esc.get("approver_role", "supervisor"),
                                             "ttl_seconds": int(esc.get("approval_link_ttl_seconds", 172800))}])
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
            drift=dict(d.get("drift") or {}),
            stages=[{"role": str(s["role"]), "ttl_seconds": int(s["ttl_seconds"])} for s in stages],
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        import yaml

        from ..common.config import settings

        p = Path(path) if path else settings.policy_path
        with open(p, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh))

    # -- escalation stages -------------------------------------------------------
    def stage(self, role: str) -> dict:
        return next((s for s in self.stages if s["role"] == role), self.stages[-1])

    def next_stage(self, role: str) -> dict | None:
        roles = [s["role"] for s in self.stages]
        i = roles.index(role) if role in roles else len(roles) - 1
        return self.stages[i + 1] if i + 1 < len(self.stages) else None

    @property
    def approval_link_ttl(self) -> int:
        return max(s["ttl_seconds"] for s in self.stages)

    # -- the compensation ladder -------------------------------------------------
    def truce_options(self, order: dict) -> list[dict]:
        """Concrete, in-policy options for this order (what the agent may offer)."""
        out = []
        left = available(order)
        cur = order.get("currency", "USD")
        for rung in self.ladder[:3]:
            amount = 0
            if rung.get("percent"):
                amount = min(int(left * rung["percent"] // 100), left)
            out.append({
                "kind": rung["kind"], "action": rung["action"], "label": rung.get("label", rung["kind"]),
                "amount_minor": amount, "currency": cur,
                "display": f"{rung.get('label', rung['kind'])}" + (f" ({fmt(amount, cur)})" if amount else ""),
            })
        return out


def available(order: dict) -> int:
    """What is still held for an order: the hold less everything refunded, credited or paid out."""
    return (int(order.get("held_minor", 0)) - int(order.get("refunded_minor", 0)) - int(order.get("credited_minor", 0))
            - int(order.get("released_minor", 0)))


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


def drift_signals(policy: Policy, action: str, amount: int, order: dict, evidence: dict, signals: dict) -> list[str]:
    """Why an otherwise in-policy Truce resolution should go to a human. Empty means no drift."""
    cfg = policy.drift
    if not cfg or action not in TRUCE_ACTIONS:
        return []
    out: list[str] = []
    cur = order.get("currency", "USD")
    limit = int(cfg.get("max_denials_per_order") or 0)
    denials = int(signals.get("denials_on_order") or 0)
    if limit and denials >= limit:
        out.append(f"repeated_denials ({denials} denied proposals on this order)")
    ev = cfg.get("evidence") or {}
    verdict = evidence.get("verdict") if isinstance(evidence.get("verdict"), dict) else None
    if ev and verdict is not None:
        conf = float(verdict.get("confidence") or 0)
        if conf < float(ev.get("min_confidence", 0)):
            out.append(f"inconclusive_evidence (confidence {conf:.2f})")
        if ev.get("require_damage_observed") and verdict.get("damage_observed") is not True:
            out.append("inconclusive_evidence (no damage visible)")
        if ev.get("require_same_item") and verdict.get("same_item_as_dispatch") is not True:
            out.append("inconclusive_evidence (item does not match the dispatch photo)")
    held_under = signals.get("hold_policy_version")
    if cfg.get("policy_version_change") and held_under and held_under != policy.version:
        out.append(f"policy_version_change (held under {held_under}, now {policy.version})")
    if cfg.get("off_ladder") and action in {"refund", "credit"}:
        rungs = {o["amount_minor"] for o in policy.truce_options(order) if o["action"] == action and o["amount_minor"]}
        if amount not in rungs:
            out.append(f"off_ladder ({fmt(amount, cur)} is not a ladder amount)")
    return out


def evaluate(policy: Policy, req: dict, order: dict | None, prior_refunds: int, approval: dict | None,
             now: int, signals: dict | None = None) -> tuple[str, str, list[str]]:
    """Return (outcome, reason, checks). `req` is the DecideRequest as a dict.
    `approval`, when present, is an already-verified human approval (claims dict).
    `signals` are facts the service read from its own records: denials_on_order, hold_policy_version."""
    checks: list[str] = []
    signals = signals or {}
    action = req.get("action")
    amount = req.get("amount_minor")
    currency = str(req.get("currency", "")).upper()
    actor = req.get("actor_sub")
    evidence = req.get("evidence") or {}
    rules = policy.actions.get(action or "")
    approved = bool(approval and approval.get("approved") is True)

    def deny(code: str, detail: str = "") -> tuple[str, str, list[str]]:
        checks.append(f"FAIL {code}")
        return DENY, f"{code}: {detail}" if detail else code, checks

    def approved_reason() -> str:
        # The approver's address stays in the decision's context; the reason is shown to both parties.
        role = approval.get("stage") or "supervisor"  # type: ignore[union-attr]
        return f"{role}_approved: the {role} approved decision {approval.get('decision_id')}"  # type: ignore[union-attr]

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

    # an escalated order is parked for a human: nothing moves without their signed approval
    if status == "ESCALATED" and not approved:
        return deny("approval_required", "the order is escalated; only a verified human approval can move it")

    # evidence --------------------------------------------------------------
    for key in rules.get("required_evidence", []):
        if not _truthy(evidence.get(key)):
            return deny("evidence_required", f"{action} requires evidence.{key} == true")
    if rules.get("required_evidence"):
        checks.append("ok evidence:" + ",".join(rules["required_evidence"]))

    # amounts ---------------------------------------------------------------
    order_amount = int(order.get("amount_minor", 0))
    left = available(order)

    if rules.get("zero_amount"):
        if amount != 0:
            return deny("amount_must_be_zero", f"{action} moves no money; got {fmt(amount, currency)}")
        checks.append("ok zero_amount")
        drift = drift_signals(policy, action, amount, order, evidence, signals)
        if drift and not approved:
            checks.append("ESCALATE policy_drift")
            return ESCALATE, "policy_drift: " + "; ".join(drift), checks
        if drift:
            checks.append("ok human_approved")
            return ALLOW, approved_reason(), checks
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
        if amount != left:
            return deny("amount_mismatch", f"{action} must equal held funds {fmt(left, currency)}, got {fmt(amount, currency)}")
        checks.append("ok amount_equals_held")
        return ALLOW, "in_policy", checks

    # refund / credit ---------------------------------------------------------
    if amount > left:
        return deny("amount_exceeds_held_funds",
                    f"requested {fmt(amount, currency)} but only {fmt(left, currency)} is held for order {order.get('order_id')}")
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
    drift = drift_signals(policy, action, amount, order, evidence, signals)
    if over_cap or drift:
        if approved:
            checks.append("ok human_approved")
            return ALLOW, approved_reason(), checks
        parts = []
        if over_cap:
            checks.append("ESCALATE above_auto_cap")
            parts.append(f"above_auto_cap: {fmt(amount, currency)} exceeds the automatic cap "
                         f"({fmt(cap, currency)} and {cap_pct:.0f}% of order)")
        if drift:
            checks.append("ESCALATE policy_drift")
            parts.append("policy_drift: " + "; ".join(drift))
        return ESCALATE, "; ".join(parts) + "; needs human approval", checks
    checks.append("ok within_auto_cap")
    checks.append("ok no_drift")
    return ALLOW, "in_policy", checks


def describe(policy: Policy, outcome: str, reason: str, req: dict, order: dict | None) -> str:
    """One plain sentence for the person in the chat. The `reason` code stays for logs and the evidence pack."""
    action = req.get("action")
    cur = str(req.get("currency") or (order or {}).get("currency") or "USD").upper()
    amount = req.get("amount_minor") if isinstance(req.get("amount_minor"), int) else 0
    code = reason.split(":", 1)[0]
    if outcome == ALLOW:
        done = {"discount": f"{fmt(amount, cur)} off approved.", "hold": "Payment confirmed and held.",
                "release": "Delivery confirmed; the seller's payout is released.",
                "settle": "The rest of the payment is released to the seller.",
                "refund": f"Your {fmt(amount, cur)} refund is approved and recorded (test mode: no real money moves).",
                "credit": f"{fmt(amount, cur)} store credit is approved and added to your account (test mode).",
                "replacement": "A replacement is approved. A courier will collect it from the seller, and you'll get a "
                               "new delivery code when it's on its way."}.get(action or "", "Approved.")
        if code.endswith("_approved"):
            who = "The seller" if code.startswith("seller") else "A Clasp supervisor"
            return f"{who} approved it. {done}"
        return done
    if outcome == ESCALATE:
        first = policy.stages[0]["role"] if policy.stages else "supervisor"
        who = "the seller" if first == "seller" else "a Clasp supervisor"
        if reason.startswith("decionis_escalate"):
            if action in {"refund", "credit", "replacement"}:  # a held Truce request goes to the same human approvers
                return f"This needs a human check, so {who} has been asked to approve it."
            return "The marketplace's policy service is holding this for a manual check, so nothing has changed yet."
        if "above_auto_cap" in reason:
            return f"{fmt(amount, cur)} is above the automatic limit, so {who} has been asked to approve it."
        return f"This needs a human check before it can go ahead, so {who} has been asked to approve it."
    left = fmt(available(order), cur) if order else fmt(0, cur)
    rules = policy.actions.get(action or "", {})
    vel = (rules.get("velocity") or {}).get("window_days", 30)
    if code == "evidence_required":
        if action == "release":
            return "I need the delivery code from your text message first."
        return "I need your live photo or video with the challenge code next to the item before anything can be paid."
    return {
        "amount_exceeds_held_funds": f"That's more than the {left} held for this order.",
        "velocity_exceeded": f"This account has already had a refund in the last {vel} days, so another refund can't be made. "
                             "Store credit or a replacement is still possible.",
        "identity_binding": "Only the buyer who placed this order can ask for that.",
        "invalid_order_state": "That isn't possible at this stage of the order.",
        "approval_required": "This order is waiting for a human decision, so nothing else can change until then.",
        "discount_exceeds_policy": f"The most I can take off is {policy.actions.get('discount', {}).get('max_percent', 0)}%.",
        "amount_mismatch": "That amount doesn't match what this step allows.",
        "amount_must_be_zero": "A replacement doesn't involve a payment.",
        "non_positive_amount": "That amount isn't valid.",
        "decionis_unavailable": "The policy service can't be reached right now, so nothing has changed. Please try again shortly.",
        "decionis_block": "That request was blocked by the marketplace's policy.",
    }.get(code, "That request couldn't be approved.")
