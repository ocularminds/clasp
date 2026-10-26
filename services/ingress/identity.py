"""Resolve the sender's phone to a Cognito `sub`. Cognito is the source of truth when the
user pool is configured; the seeded parties table is the fallback (same subs, see demo/seed.py).
AgentSafe later checks that this sub equals the order owner, so a spoofed `from` can only
ever act as the account that owns that phone number.

Unknown senders are rejected unless AUTO_ENROL_CHANNELS names their channel (judging / demo):
then first contact enrols them as a buyer with their own Cognito identity, and the same binding applies."""
from __future__ import annotations

from ..common import clock
from ..common.config import settings
from ..common.log import Log
from ..common.stores import get_stores

log = Log("ingress.identity")
_sub_cache: dict[tuple[str, str], str] = {}  # hits only: a miss can become a hit after enrolment


def _sub_from(attrs) -> str | None:
    for a in attrs or []:
        if a.get("Name") == "sub":
            return a.get("Value")
    return None


def _cognito_lookup(pool_id: str, phone: str) -> str | None:
    key = (pool_id, phone)
    if key in _sub_cache:
        return _sub_cache[key]
    from ..common.aws import client

    r = client("cognito-idp").list_users(UserPoolId=pool_id, Filter=f'phone_number = "{phone}"', Limit=1)
    for u in r.get("Users", []):
        sub = _sub_from(u.get("Attributes"))
        if sub:
            _sub_cache[key] = sub
            return sub
    return None


def _cognito_enrol(pool_id: str, phone: str, role: str) -> str:
    """Create the Cognito user for a phone, or fetch it if it already exists; return its sub."""
    from botocore.exceptions import ClientError

    from ..common.aws import client

    cog = client("cognito-idp")
    attrs = [{"Name": "phone_number", "Value": phone}, {"Name": "phone_number_verified", "Value": "true"},
             {"Name": "custom:role", "Value": role}]
    try:
        r = cog.admin_create_user(UserPoolId=pool_id, Username=phone, UserAttributes=attrs, MessageAction="SUPPRESS")
        user_attrs = r["User"]["Attributes"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
        user_attrs = cog.admin_get_user(UserPoolId=pool_id, Username=phone)["UserAttributes"]
    sub = _sub_from(user_attrs)
    if not sub:
        raise RuntimeError("Cognito returned no sub for the enrolled user")
    _sub_cache[(pool_id, phone)] = sub
    return sub


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


def auto_enrol(phone: str, name: str | None = None) -> tuple[str, dict]:
    """First-contact enrolment (AUTO_ENROL_CHANNELS). Idempotent: an existing party keeps its id, role and name.
    The sub comes from Cognito when a pool is configured, so an enrolled judge can only act on their own orders."""
    stores = get_stores()
    existing = stores.party_by_phone(phone) or {}
    role = existing.get("role") or "buyer"
    party_id = existing.get("party_id") or f"buyer_{phone.lstrip('+')}"
    pool = settings.cognito_user_pool_id
    sub = _cognito_enrol(pool, phone, role) if pool else (existing.get("sub") or f"sub-{party_id}")
    party = {**existing, "party_id": party_id, "role": role, "name": existing.get("name") or (name or "").strip() or "Buyer",
             "phone": phone, "email": existing.get("email") or "", "sub": sub,
             "auto_enrolled": existing.get("auto_enrolled", not existing), "enrolled_at": existing.get("enrolled_at", clock.now())}
    stores.put_party(party)
    log.info("identity.auto_enrolled", phone=phone, party_id=party_id, role=role, new=not existing)
    return sub, party
