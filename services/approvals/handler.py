"""SES approval link → API Gateway → here → AgentSafe (with the signed approval token) → executor.

GET  /approvals?token=<jws>   verifies the token and shows a confirmation page. It changes nothing, so mail
                              scanners and link previews that fetch the URL cannot approve or reject anything.
POST /approvals (token=<jws>) acts on it.

The token is minted by the reference service and bound to one order, one amount, one escalate decision and
one approval stage (seller, then supervisor); approving it still yields a single-use authorization."""
from __future__ import annotations

import base64
import html
from urllib.parse import parse_qs

from ..agentsafe.service import AgentSafeService
from ..agentsafe.signer import get_verifier
from ..common.config import settings
from ..common.ids import correlation_id as new_corr
from ..common.log import Log
from ..common.money import fmt
from ..common.stores import get_stores
from ..tools.money import propose
from ..workflows import tasks
from ..workflows.client import get_driver

log = Log("approvals")

_STYLE = ("body{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 16px;color:#1b1b1f}"
          "button{padding:12px 18px;border:0;border-radius:8px;color:#fff;font-size:16px;margin-top:8px}"
          ".ok{background:#0a7d3e}.no{background:#b00020}.muted{color:#666;font-size:13px}pre{white-space:pre-wrap;font:inherit}")


def _page(title: str, body: str, status: int = 200) -> dict:
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>")
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"}, "body": doc}


def _check(token: str | None) -> tuple[dict | None, dict | None, dict | None]:
    """Return (claims, order, error_page)."""
    if not token:
        return None, None, _page("Missing token", "<p>This approval link is incomplete.</p>", 400)
    # Verify signature, key, type and expiry BEFORE reading a single claim.
    claims = AgentSafeService.read_approval_token(token, get_verifier())
    if claims is None:
        log.warn("approval.invalid_token")
        return None, None, _page("Invalid or expired link", "<p>This approval link is not valid. It may have been altered or expired.</p>", 400)
    order_id = str(claims.get("order_id", ""))
    order = get_stores().get_order(order_id)
    if not order:
        return claims, None, _page("Unknown order", f"<p>No order {html.escape(order_id[-6:])}.</p>", 404)
    code = html.escape(order_id[-6:])
    if order.get("status") != "ESCALATED":
        words = tasks.STATUS_WORDS.get(order.get("status", ""), "no longer escalated")
        return claims, order, _page("Nothing to decide", f"<p>Order {code} is {html.escape(words)}; "
                                                         "this request is no longer pending.</p>")
    stage = str(claims.get("stage") or "supervisor")
    current = (order.get("escalation") or {}).get("stage") or "supervisor"
    if stage != current:
        who = "a Clasp supervisor" if current == "supervisor" else "the seller"
        return claims, order, _page("Decision has moved on", f"<p>The decision on order {code} is now with {who}, "
                                                             "so this link no longer applies.</p>")
    return claims, order, None


def _what(order: dict, claims: dict) -> str:
    esc = order.get("escalation") or {}
    amount, currency = int(claims.get("amount_minor", 0)), str(claims.get("currency", "USD"))
    kind = tasks.KIND_LABEL.get(esc.get("kind", "refund"), "refund")
    return f"{kind} of {fmt(amount, currency)}" if amount else kind


def confirm(token: str | None) -> dict:
    """GET: show what will happen and ask for one more click."""
    claims, order, error = _check(token)
    if error:
        return error
    assert claims is not None and order is not None
    esc = order.get("escalation") or {}
    what, code = _what(order, claims), order["order_id"][-6:]
    approving = claims.get("approved") is True
    why = "".join(f"<li>{html.escape(w)}</li>" for w in tasks.why_lines(esc.get("reason", ""), order))
    action = "Approve" if approving else "Reject"
    body = (f"<p>{action} the buyer's request for a <b>{html.escape(what)}</b> on order <b>{html.escape(code)}</b> "
            f"({html.escape(order.get('item_name', ''))})?</p><p>It needed a human decision because:</p><ul>{why}</ul>"
            f"<p>What the buyer reported: <i>“{html.escape((order.get('dispute') or {}).get('reason') or '-')}”</i></p>"
            f"<p>Evidence check:</p><pre>{html.escape(tasks.verdict_lines(order['order_id']))}</pre>"
            f"<form method='post' action='{html.escape(settings.api_base_url)}/approvals'>"
            f"<input type='hidden' name='token' value='{html.escape(str(token))}'>"
            f"<button class='{'ok' if approving else 'no'}' type='submit'>{action} {html.escape(what)}</button></form>"
            "<p class='muted'>Nothing has changed yet. Payments in this prototype are simulated.</p>")
    return _page(f"{action} {what}?", body)


def handle(token: str | None) -> dict:
    """POST: act on a verified token."""
    claims, order, error = _check(token)
    if error:
        return error
    assert claims is not None and order is not None
    stores = get_stores()
    order_id = order["order_id"]
    code = html.escape(order_id[-6:])
    corr = order.get("correlation_id") or new_corr()
    esc = order.get("escalation") or {}
    amount, currency = int(claims.get("amount_minor", 0)), str(claims.get("currency", "USD"))
    approver = str(claims.get("approver") or settings.supervisor_email)
    stage = str(claims.get("stage") or "supervisor")
    what = _what(order, claims)
    token_phase = (order.get("tokens") or {}).get("approval")

    if claims.get("approved") is not True:
        log.info("approval.rejected", order_id=order_id, approver=approver, stage=stage, correlation_id=corr)
        if token_phase:
            error_code = "ApprovalRejected" if stage == "seller" else "ApprovalRejectedFinal"
            get_driver().task_failure(token_phase, error_code, f"{stage} rejected {fmt(amount, currency)}")
        nxt = ("A Clasp supervisor will now decide." if stage == "seller"
               else "The buyer will be offered the standard Truce options instead.")
        return _page("Request rejected", f"<p>You rejected the {html.escape(what)} on order {code}. {nxt}</p>")

    # Approve: AgentSafe verifies the token itself (signature, expiry, amount, decision, stage) and decides.
    result = propose(esc.get("action") or "refund", order_id=order_id, amount_minor=amount, currency=currency,
                     actor_sub=order["buyer_sub"], evidence=_latest_evidence(stores, order_id),
                     context={"approval_token": token, "approver": approver, "approval_stage": stage},
                     metadata={"kind": esc.get("kind", "refund"), "approval_stage": stage},  # the ledger reaches both parties
                     correlation_id=corr)
    log.info("approval.processed", order_id=order_id, approver=approver, stage=stage, decision=result.get("decision"),
             executed=result.get("executed"), correlation_id=corr)
    if result.get("executed"):
        return _page("Approved", f"<p>The {html.escape(what)} on order {code} has been approved and recorded. "
                                 "The buyer has been told in their chat.</p>"
                                 f"<p class='muted'>AgentSafe decision <code>{html.escape(result['decision_id'])}</code> · ledger entry "
                                 f"<code>{html.escape(str(result.get('ledger_entry_id')))}</code>.</p>")
    return _page("Not executed", f"<p>AgentSafe returned <b>{html.escape(result.get('decision', '?'))}</b>: "
                                 f"{html.escape(result.get('message') or result.get('reason', ''))}</p>"
                                 f"<p class='muted'>{html.escape(result.get('reason', ''))} "
                                 f"{html.escape(result.get('execution_reason') or '')}</p>", 409)


def _latest_evidence(stores, order_id: str) -> dict:
    for c in reversed(stores.challenges_for_order(order_id)):
        if c.get("kind") == "dispute" and c.get("status") == "verified":
            return {"challenge_verified": True, "challenge_id": c["challenge_id"], "verdict": c.get("verdict") or {}}
    return {"challenge_verified": False}


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "POST":
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        return handle((parse_qs(raw).get("token") or [None])[0])
    params = event.get("queryStringParameters") or {}
    return confirm(params.get("token"))
