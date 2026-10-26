"""Single source of truth for DynamoDB key schemas. Used by the CDK data stack,
the moto fixtures in tests, and the local store's secondary indexes."""
from __future__ import annotations

TABLE_SPECS: dict[str, dict] = {
    "orders": {
        "pk": "order_id",
        "gsis": {
            "by_buyer": {"pk": "buyer_sub", "sk": "created_at"},
            "by_seller": {"pk": "seller_sub", "sk": "created_at"},
            "by_checkout": {"pk": "checkout_id"},
        },
    },
    # Append-only. The executor role is the only principal with PutItem.
    "ledger": {
        "pk": "order_id",
        "sk": "entry_id",
        "gsis": {"by_buyer": {"pk": "buyer_sub", "sk": "ts"}},
    },
    "decisions": {
        "pk": "decision_id",
        "gsis": {
            "by_order": {"pk": "order_id", "sk": "ts"},
            "by_day": {"pk": "day", "sk": "ts"},
        },
    },
    # Single-use: conditional put on attribute_not_exists(nonce); TTL on expires_at.
    "nonces": {"pk": "nonce", "ttl": "expires_at"},
    "challenges": {"pk": "order_id", "sk": "challenge_id"},
    "items": {"pk": "item_id"},
    "parties": {
        "pk": "party_id",
        "gsis": {"by_phone": {"pk": "phone"}, "by_sub": {"pk": "sub"}},
    },
}


def key_attribute_definitions(spec: dict) -> list[dict]:
    names = {spec["pk"]}
    if spec.get("sk"):
        names.add(spec["sk"])
    for g in spec.get("gsis", {}).values():
        names.add(g["pk"])
        if g.get("sk"):
            names.add(g["sk"])
    return [{"AttributeName": n, "AttributeType": "S"} for n in sorted(names)]


def key_schema(pk: str, sk: str | None = None) -> list[dict]:
    schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    if sk:
        schema.append({"AttributeName": sk, "KeyType": "RANGE"})
    return schema
