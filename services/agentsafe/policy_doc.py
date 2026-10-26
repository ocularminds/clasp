"""Render the compensation and dispute policy (policy.yaml) as a plain-language document.
`make kb-sync` uploads it to the Bedrock Knowledge Base; `explain_policy` also searches it locally
so the tool works offline. Keeping it generated means the words can never drift from the numbers."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..common.config import settings
from ..common.money import fmt

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
POLICY_DOC = KNOWLEDGE_DIR / "compensation-policy.md"
FAQ_DOC = KNOWLEDGE_DIR / "truce-faq.md"


def load_policy() -> dict:
    return yaml.safe_load(settings.policy_path.read_text())


def _pct(v) -> str:
    return f"{int(v)}%" if float(v).is_integer() else f"{v}%"


def render(policy: dict | None = None) -> str:
    p = policy or load_policy()
    cur = (p.get("currencies") or ["USD"])[0]
    acts = p.get("actions", {})
    refund, credit, disc = acts.get("refund", {}), acts.get("credit", {}), acts.get("discount", {})
    hold, release, repl = acts.get("hold", {}), acts.get("release", {}), acts.get("replacement", {})
    ladder = (p.get("truce") or {}).get("ladder", [])
    authz = p.get("authorization", {})
    esc = p.get("escalation", {})
    vel = refund.get("velocity", {})
    cap = fmt(int(refund.get("auto_cap_minor", 0)), cur)
    lines = [
        f"# Clasp compensation and dispute policy (version {p.get('version')})",
        "",
        "## Overview",
        "Clasp holds the buyer's payment from purchase to confirmed delivery. The seller is paid only when the buyer confirms "
        "delivery with a one-time code. If something goes wrong, Clasp calls a Truce: the payout stays held, evidence is "
        "collected, and compensation is chosen from a fixed ladder. The assistant can propose a discount, hold, release, refund, "
        "store credit or replacement, but it has no authority to move money. Every proposal is decided by AgentSafe, the policy "
        "service, which allows it, denies it with a reason, or escalates it to a human supervisor.",
        "",
        "## Who can act on an order",
        "Only the buyer who placed an order can ask for a discount, confirm delivery or open a dispute on it, and only its seller "
        "can record dispatch. Identity comes from the verified phone number the message was sent from, never from what the "
        "message says. A payment is confirmed only by the payment provider's signed webhook; screenshots and promises are not payment.",
        "",
        "## Discounts",
        f"The assistant may offer a discount of up to {_pct(disc.get('max_percent', 0))} of the list price while the order is "
        f"unpaid (states: {', '.join(disc.get('allowed_states', []))}). Stacked discounts count together: the total may never "
        f"exceed {_pct(disc.get('max_percent', 0))}.",
        "",
        "## Payment hold",
        f"When the provider confirms payment, the full order amount is placed on hold (it must equal the order amount; required "
        f"evidence: {', '.join(hold.get('required_evidence', []))}). The seller is told that funds are secured by the provider and "
        "may dispatch.",
        "",
        "## Delivery and release",
        "After the seller records dispatch with a photo, the buyer receives a one-time delivery code by SMS. Giving that code to "
        f"the assistant releases the held amount to the seller (states: {', '.join(release.get('allowed_states', []))}; required "
        f"evidence: {', '.join(release.get('required_evidence', []))}). The release always equals the held amount. A wrong code "
        "releases nothing.",
        "",
        "## Truce: how a dispute works",
        "If the buyer reports damage or a wrong item after dispatch, Clasp opens a Truce. The payout stays held. The buyer gets a "
        "fresh one-time challenge code and must send one live photo showing the code written next to the item. The assistant "
        "compares it with the seller's dispatch photo and reports whether the code is visible and matches, whether it is the same "
        "item, and whether damage is visible. That report is evidence only; AgentSafe decides. No refund, credit or replacement "
        f"is possible until the challenge is verified (required evidence: {', '.join(refund.get('required_evidence', []))}).",
        "",
        "## Compensation ladder (what the assistant may offer)",
    ]
    for step in ladder:
        pct = f" ({_pct(step['percent'])} of the order)" if step.get("percent") is not None else ""
        lines.append(f"- {step.get('label')}{pct}, action: {step.get('action')}")
    lines += [
        "At most three options are offered as reply buttons. The buyer may also ask for a different amount; the assistant submits "
        "it and relays AgentSafe's decision.",
        "",
        "## Refund limits",
        f"A refund is allowed automatically when the challenge is verified, the amount is within what is held, and the amount is "
        f"at most {cap} and at most {_pct(refund.get('auto_cap_percent_of_order', 0))} of the order value. Anything above that is "
        f"escalated to a supervisor ({refund.get('above_cap', 'escalate')}). A refund can never exceed the amount held for the "
        f"order. Each buyer may receive at most {vel.get('max_per_buyer')} refund in any {vel.get('window_days')}-day period; "
        "further refund proposals are denied for velocity.",
        "",
        "## Store credit and replacement",
        f"Store credit follows the same limits as refunds (automatic up to {fmt(int(credit.get('auto_cap_minor', 0)), cur)} and "
        f"{_pct(credit.get('auto_cap_percent_of_order', 0))} of the order, verified evidence required, above that escalated). "
        f"A replacement item moves no money (zero amount) and also requires verified evidence. Both are possible only in a Truce "
        f"(states: {', '.join(repl.get('allowed_states', []))}).",
        "",
        "## Supervisor approval",
        f"Above-cap refunds and credits are sent to a {esc.get('approver_role', 'supervisor')} by email with signed Approve and "
        f"Reject links that expire after {int(esc.get('approval_link_ttl_seconds', 0)) // 3600} hours. An approval is bound to one "
        "order, one amount and one decision; it cannot be reused or edited. If the supervisor rejects, the order returns to the "
        "Truce and the ladder options remain available. While an order is escalated, nothing else moves on it.",
        "",
        "## Authorizations",
        f"Every allowed action is a signed authorization ({authz.get('algorithm')}) that lives for {authz.get('ttl_seconds')} seconds "
        f"(never more than {authz.get('max_ttl_seconds')}), can be used once, and matches exactly one action, order, amount, currency "
        "and actor. The executor refuses anything else, so a denial cannot be talked around and a decision cannot be replayed.",
        "",
        "## What never happens",
        "The assistant never moves money on its own, never accepts card or bank details in chat, never treats a screenshot as "
        "payment, never retries a denied amount, and never acts for anyone other than the verified sender.",
        "",
    ]
    return "\n".join(lines)


def sections(text: str) -> list[dict]:
    """Split a markdown document into {title, text} sections on level-2 headings."""
    out: list[dict] = []
    title, buf = "Overview", []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf and "".join(buf).strip():
                out.append({"title": title, "text": "\n".join(buf).strip()})
            title, buf = line[3:].strip(), []
        elif not line.startswith("# "):
            buf.append(line)
    if buf and "".join(buf).strip():
        out.append({"title": title, "text": "\n".join(buf).strip()})
    return out


def documents() -> dict[str, str]:
    """Current policy document (rendered now, so always in step with policy.yaml) plus the FAQ."""
    docs = {"compensation-policy.md": render()}
    if FAQ_DOC.exists():
        docs["truce-faq.md"] = FAQ_DOC.read_text()
    return docs
