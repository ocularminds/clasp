"""Tool schemas for the AgentCore Gateway Lambda target (inlinePayload) and for the
local Strands tools. `actor_sub` / `correlation_id` are system-set: the runtime hook
overwrites them on every call, so the model's value never reaches the Lambda."""
from __future__ import annotations

SYSTEM_PROPS = {
    "actor_sub": {"type": "string", "description": "Set by the system. Ignore."},
    "correlation_id": {"type": "string", "description": "Set by the system. Ignore."},
}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {**props, **SYSTEM_PROPS}, "required": required}


TOOL_SCHEMAS: list[dict] = [
    {"name": "list_items", "description": "List items for sale with price and stock.", "inputSchema": _schema({}, [])},
    {"name": "get_item", "description": "Get one item by id (or a name fragment): price, stock, description, max discount.",
     "inputSchema": _schema({"item_id": {"type": "string"}}, ["item_id"])},
    {"name": "start_order", "description": "Create an order for the buyer you are talking to. Reserves stock; charges nothing.",
     "inputSchema": _schema({"item_id": {"type": "string"}, "qty": {"type": "integer", "minimum": 1}}, ["item_id"])},
    {"name": "propose_discount", "description": "Propose a percentage discount on an unpaid order. AgentSafe decides; the policy cap applies.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "percent": {"type": "number"}}, ["order_id", "percent"])},
    {"name": "create_checkout", "description": "Create the hosted payment link for an unpaid order. Never ask for card details in chat.",
     "inputSchema": _schema({"order_id": {"type": "string"}}, ["order_id"])},
    {"name": "get_order", "description": "Status, amounts and ledger of one order. Accepts the full order ID or the 6-character short code from an alert.",
     "inputSchema": _schema({"order_id": {"type": "string", "description": "Full order ID or 6-character short code"}}, ["order_id"])},
    {"name": "my_orders", "description": "Recent orders of the person you are talking to, as buyer or as seller (with short codes).",
     "inputSchema": _schema({}, [])},
    {"name": "record_dispatch", "description": "Seller only: record that the item was dispatched, with the dispatch photo key. "
                                               "order_id may be the full ID or the 6-character code from the seller's alert; omit it when the seller has exactly one order awaiting dispatch.",
     "inputSchema": _schema({"order_id": {"type": "string", "description": "Full order ID or short code; optional with a single open order"},
                             "photo_key": {"type": "string"}}, [])},
    {"name": "confirm_delivery", "description": "Buyer gives the one-time SMS delivery code. If it matches, a payout release is proposed.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "code": {"type": "string"}}, ["order_id", "code"])},
    {"name": "report_problem", "description": "Buyer reports damage or a wrong item. Opens a Truce: payout stays held, a challenge code is issued.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "description": {"type": "string"}}, ["order_id", "description"])},
    {"name": "issue_challenge", "description": "Issue a fresh one-time challenge code the buyer must show next to the item in a live photo.",
     "inputSchema": _schema({"order_id": {"type": "string"}}, ["order_id"])},
    {"name": "verify_evidence", "description": "Compare the buyer's live photo (media key) with the dispatch photo and the challenge code. Reports only.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "photo_key": {"type": "string"}, "challenge_id": {"type": "string"}},
                            ["order_id", "photo_key"])},
    {"name": "get_truce_options", "description": "The in-policy compensation ladder for this order, with amounts.",
     "inputSchema": _schema({"order_id": {"type": "string"}}, ["order_id"])},
    {"name": "propose_refund", "description": "Propose a refund in minor units (cents). AgentSafe allows, denies or escalates; you cannot override it.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "amount_minor": {"type": "integer"}}, ["order_id", "amount_minor"])},
    {"name": "propose_credit", "description": "Propose store credit in minor units. AgentSafe decides.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "amount_minor": {"type": "integer"}}, ["order_id", "amount_minor"])},
    {"name": "propose_replacement", "description": "Propose a replacement item (no money moves). AgentSafe decides.",
     "inputSchema": _schema({"order_id": {"type": "string"}}, ["order_id"])},
    {"name": "offer_options", "description": "Put up to three ladder options to the buyer as reply buttons. Kinds: partial_refund, replacement, store_credit.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "option_kinds": {"type": "array", "items": {"type": "string"}}},
                            ["order_id", "option_kinds"])},
    {"name": "notify_party", "description": "Send a short message to the buyer (WhatsApp) or seller (RCS, SMS fallback) of an order.",
     "inputSchema": _schema({"order_id": {"type": "string"}, "party": {"type": "string", "enum": ["buyer", "seller"]},
                             "text": {"type": "string"}}, ["order_id", "party", "text"])},
]
