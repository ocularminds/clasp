"""Demo hardening: the prompt makes the injection beat deterministic, and first-contact enrolment creates exactly one
Cognito identity per phone."""
from __future__ import annotations

import boto3

from services.agent.prompt import SYSTEM_PROMPT
from tests.conftest import REGION


def test_prompt_directs_the_model_to_submit_any_requested_amount():
    truce = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Truce (disputes)"):SYSTEM_PROMPT.index("Sellers:")]
    assert "ALWAYS submit exactly that amount with propose_refund" in truce
    assert "Never refuse to submit a proposal on your own judgement" in truce
    assert "you may propose it" not in truce
    assert "Never retry a denied amount" in truce
    sellers = SYSTEM_PROMPT[SYSTEM_PROMPT.index("Sellers:"):]
    assert "short code" in sellers and "needs no reference" in sellers


def test_auto_enrol_creates_one_cognito_identity_per_phone(stores, aws, monkeypatch):
    cog = boto3.client("cognito-idp", region_name=REGION)
    pool = cog.create_user_pool(PoolName="clasp-test", UsernameAttributes=["phone_number"],
                                Schema=[{"Name": "role", "AttributeDataType": "String", "Mutable": True}])["UserPool"]["Id"]
    monkeypatch.setenv("COGNITO_USER_POOL_ID", pool)
    from services.ingress import identity

    identity._sub_cache.clear()
    assert identity.resolve_sub("+15550007777") == (None, None)
    sub1, party = identity.auto_enrol("+15550007777", "Judge")
    sub2, _ = identity.auto_enrol("+15550007777", "Someone else")  # idempotent
    assert sub1 == sub2 and party["role"] == "buyer" and party["name"] == "Judge"
    attrs = {a["Name"]: a["Value"] for a in cog.admin_get_user(UserPoolId=pool, Username="+15550007777")["UserAttributes"]}
    assert attrs["sub"] == sub1 and attrs["custom:role"] == "buyer" and attrs["phone_number"] == "+15550007777"
    identity._sub_cache.clear()
    assert identity.resolve_sub("+15550007777")[0] == sub1  # found through Cognito on the next message
    assert len(cog.list_users(UserPoolId=pool)["Users"]) == 1
    identity._sub_cache.clear()
