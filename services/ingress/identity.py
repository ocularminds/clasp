"""Resolve the sender's phone to a Cognito `sub`. Cognito is the source of truth when the
user pool is configured; the seeded parties table is the fallback (same subs, see demo/seed.py).
AgentSafe later checks that this sub equals the order owner, so a spoofed `from` can only
ever act as the account that owns that phone number."""
from __future__ import annotations

from functools import lru_cache

from ..common.config import settings
from ..common.log import Log
from ..common.stores import get_stores

log = Log("ingress.identity")


@lru_cache(maxsize=512)
def _cognito_lookup(pool_id: str, phone: str) -> str | None:
    from ..common.aws import client

    r = client("cognito-idp").list_users(UserPoolId=pool_id, Filter=f'phone_number = "{phone}"', Limit=1)
    for u in r.get("Users", []):
        for a in u.get("Attributes", []):
            if a["Name"] == "sub":
                return a["Value"]
    return None


def resolve_sub(phone: str) -> tuple[str | None, dict | None]:
    """Return (sub, party). party is the seeded profile when known (role, name, email)."""
    party = get_stores().party_by_phone(phone)
    pool = settings.cognito_user_pool_id
    if pool:
        try:
            sub = _cognito_lookup(pool, phone)
        except Exception as e:  # noqa: BLE001
            log.error("cognito.lookup_failed", phone=phone, error=str(e))
            sub = None
        if sub:
            return sub, party
        log.warn("cognito.unknown_phone", phone=phone)
        return None, party
    return (party or {}).get("sub"), party
