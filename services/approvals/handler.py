"""SES approval link → API Gateway → here → AgentSafe (with the signed approval token) → executor.
GET /approvals?token=<jws>. The token is minted by the reference service and bound to one
order, one amount and one escalate decision; approving it still yields a single-use authorization."""
from __future__ import annotations

import html
import json

from ..agentsafe.service import AgentSafeService
from ..agentsafe.signer import get_verifier
from ..common.config import settings
from ..common.ids import correlation_id as new_corr
from ..common.log import Log
from ..common.money import fmt
from ..common.stores import get_stores
from ..tools.money import propose
from ..workflows.client import get_driver

log = Log("approvals")


def _page(title: str, body: str, status: int = 200) -> dict:
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>body{{font-family:-apple-system,"
           f"system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 16px;color:#1b1b1f}}</style></head>"
           f"<body><h1>{html.escape(title)}</h1>{body}</body></html>")
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8"}, "body": doc}


def handle(token: str | None) -> dict:
    if not token:
        return _page("Missing token", "<p>This approval link is incomplete.</p>", 400)
    # Verify signature, key, type and expiry BEFORE reading a single claim.
    claims = AgentSafeService.read_approval_token(token, get_verifier())
    if claims is None:
        log.warn("approval.invalid_token")
        return _page("Invalid or expired link", "<p>This approval link is not valid. It may have been altered or expired.</p>", 400)
    order_id = str(claims.get("order_id", ""))
    stores = get_stores()
    order = stores.get_order(order_id)
    if not order:
        return _page("Unknown order", f"<p>No order {html.escape(order_id)}.</p>", 404)
    corr = order.get("correlation_id") or new_corr()
    amount, currency = int(claims.get("amount_minor", 0)), str(claims.get("currency", "USD"))
    approver = str(claims.get("approver", settings.supervisor_email))
    if order.get("status") != "ESCALATED":
        return _page("Nothing to approve", f"<p>Order {html.escape(order_id)} is {html.escape(order.get('status', ''))}; "
                                           f"this approval is no longer pending.</p>")
    token_phase = (order.get("tokens") or {}).get("approval")

    if claims.get("approved") is not True:
        log.info("approval.rejected", order_id=order_id, approver=approver, correlation_id=corr)
        if token_phase:
            get_driver().task_failure(token_phase, "ApprovalRejected", f"{approver} rejected {fmt(amount, currency)}")
        return _page("Refund rejected", f"<p>{html.escape(approver)} rejected the {fmt(amount, currency)} refund for order "
                                        f"{html.escape(order_id)}. The buyer will be offered in-policy options instead.</p>")

    # Approve: AgentSafe verifies the token itself (signature, expiry, amount, decision) and decides.
    result = propose("refund", order_id=order_id, amount_minor=amount, currency=currency, actor_sub=order["buyer_sub"],
                     evidence={"challenge_verified": _latest_verified(stores, order_id)},
                     context={"approval_token": token, "approver": approver},
                     metadata={"kind": "refund", "approved_by": approver}, correlation_id=corr)
    log.info("approval.processed", order_id=order_id, approver=approver, decision=result.get("decision"),
             executed=result.get("executed"), correlation_id=corr)
    if result.get("executed"):
        return _page("Refund approved and executed",
                     f"<p>{fmt(amount, currency)} refunded to the buyer for order {html.escape(order_id)}.</p>"
                     f"<p>AgentSafe decision <code>{html.escape(result['decision_id'])}</code> · ledger entry "
                     f"<code>{html.escape(str(result.get('ledger_entry_id')))}</code>.</p>")
    return _page("Not executed", f"<p>AgentSafe returned <b>{html.escape(result.get('decision', '?'))}</b>: "
                                 f"{html.escape(result.get('reason', ''))}</p>"
                                 f"<p>{html.escape(result.get('execution_reason') or '')}</p>", 409)


def _latest_verified(stores, order_id: str) -> bool:
    for c in reversed(stores.challenges_for_order(order_id)):
        if c.get("kind") == "dispute" and c.get("status") == "verified":
            return True
    return False


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    return handle(params.get("token"))
