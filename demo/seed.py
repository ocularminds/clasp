#!/usr/bin/env python
"""Seed items, parties and (when configured) Cognito users. Idempotent.

    make seed            # uses STORE_MODE / COGNITO_USER_POOL_ID from .env
Tests import `seed()` and run it against moto-backed stores."""
from __future__ import annotations

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.config import settings  # noqa: E402
from services.common.log import Log  # noqa: E402
from services.common.stores import get_stores  # noqa: E402

log = Log("seed")

ITEMS = [
    {"item_id": "item_bag", "name": "Vintage leather camera bag", "price_minor": 12000, "currency": "USD", "stock": 5,
     "seller_id": "seller_demo", "description": "Full-grain leather, fits a body and two lenses. Light wear on the strap.",
     "image_url": "https://images.unsplash.com/photo-1544816155-12df9643f363?w=800"},
    {"item_id": "item_lens", "name": "50mm f/1.8 prime lens", "price_minor": 9500, "currency": "USD", "stock": 2,
     "seller_id": "seller_demo", "description": "Sharp, clean glass, both caps included.",
     "image_url": "https://images.unsplash.com/photo-1495707902641-75cac588d2e9?w=800"},
    {"item_id": "item_tripod", "name": "Carbon travel tripod", "price_minor": 4500, "currency": "USD", "stock": 0,
     "seller_id": "seller_demo", "description": "Folds to 40cm. Sold out at the moment.",
     "image_url": "https://images.unsplash.com/photo-1519183071298-a2962feb14f4?w=800"},
]


def parties(*, live: bool = False) -> list[dict]:
    from services.common.config import env

    def phone(key: str, local_default: str) -> str | None:
        return env(key) if live else env(key, local_default)

    people = [
        {"party_id": "buyer_demo", "role": "buyer", "name": "Ada", "phone": phone("DEMO_BUYER_PHONE", "+15550000001"),
         "email": env("DEMO_BUYER_EMAIL", "buyer@example.com")},
        {"party_id": "buyer_demo_2", "role": "buyer", "name": "Bo", "phone": phone("DEMO_BUYER2_PHONE", "+15550000004"),
         "email": env("DEMO_BUYER2_EMAIL", "buyer2@example.com")},
        {"party_id": "seller_demo", "role": "seller", "name": "Bram's Camera Shop",
         "phone": phone("DEMO_SELLER_PHONE", "+15550000002"), "email": env("DEMO_SELLER_EMAIL", "seller@example.com")},
        {"party_id": "supervisor_demo", "role": "supervisor", "name": "Supervisor", "phone": phone("DEMO_SUPERVISOR_PHONE", "+15550000003"),
         "email": settings.supervisor_email},
    ]
    if not live:
        return people
    # Optional second buyer is omitted; a supervisor can approve through email alone.
    people = [p for p in people if p["party_id"] != "buyer_demo_2" or p["phone"]]
    seen = set()
    for person in people:
        number = person["phone"]
        if not number and person["role"] == "supervisor":
            continue
        if not number or not re.fullmatch(r"\+[1-9]\d{7,14}", number) or number.startswith("+1555000"):
            raise ValueError(f"Set a real verified E.164 phone for {person['party_id']} before seeding Cognito.")
        if number in seen:
            raise ValueError("Each demo identity needs a distinct phone number.")
        seen.add(number)
    return people


def _cognito_sub(cognito, pool_id: str, party: dict) -> str:
    """Create (or fetch) the Cognito user for a party; the phone attribute is what ingress resolves."""
    from botocore.exceptions import ClientError

    username = party["phone"]
    attrs = [{"Name": "phone_number", "Value": party["phone"]}, {"Name": "phone_number_verified", "Value": "true"},
             {"Name": "email", "Value": party["email"]}, {"Name": "email_verified", "Value": "true"},
             {"Name": "custom:role", "Value": party["role"]}]
    try:
        r = cognito.admin_create_user(UserPoolId=pool_id, Username=username, UserAttributes=attrs, MessageAction="SUPPRESS")
        user_attrs = r["User"]["Attributes"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
        user_attrs = cognito.admin_get_user(UserPoolId=pool_id, Username=username)["UserAttributes"]
        role = next((a["Value"] for a in user_attrs if a["Name"] == "custom:role"), None)
        if role != party["role"]:
            raise ValueError(f"Existing Cognito user for {party['party_id']} has a different role; do not reassign it silently.")
    return next(a["Value"] for a in user_attrs if a["Name"] == "sub")


def seed(stores=None, cognito=None, pool_id: str | None = None) -> dict:
    stores = stores or get_stores()
    pool_id = pool_id or settings.cognito_user_pool_id
    people = parties(live=bool(pool_id))  # Validate every identity before writing any data.
    if pool_id and cognito is None:
        from services.common.aws import client

        cognito = client("cognito-idp")
    for item in ITEMS:
        existing = stores.get_item(item["item_id"])
        stores.put_item({**item, "stock": existing["stock"] if existing else item["stock"]})
    subs = {}
    for p in people:
        existing = stores.get_party(p["party_id"])
        if pool_id and cognito and p["phone"]:
            sub = _cognito_sub(cognito, pool_id, p)
        else:
            sub = (existing or {}).get("sub") or f"sub-{p['party_id']}"
        stores.put_party({**p, "sub": sub})
        subs[p["party_id"]] = sub
    log.info("seed.done", items=len(ITEMS), parties=len(subs), cognito=bool(pool_id))
    return subs


if __name__ == "__main__":
    out = seed()
    print("seeded:", out)
