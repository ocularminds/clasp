"""Shared fixtures: moto-backed DynamoDB tables and a KMS ECC key, the reference
AgentSafe service, the executor, and a recording workflow driver."""
from __future__ import annotations

import os

os.environ.pop("AWS_PROFILE", None)  # a stray profile would break moto

import boto3
import pytest
from moto import mock_aws

from services.agentsafe.policy import Policy
from services.agentsafe.service import AgentSafeService, set_service
from services.agentsafe.signer import KmsSigner, KmsVerifier
from services.agentsafe import adapter
from services.common import clock
from services.common.aws import reset_cache
from services.common.ids import new_id
from services.common.stores import DynamoStores, create_tables, set_stores
from services.executor.executor import Executor, set_executor
from services.workflows.client import MemoryDriver, set_driver

REGION = "us-east-1"
BUYER = "sub-buyer-0001"
OTHER = "sub-other-9999"
SELLER = "seller_demo"
SUPERVISOR = "sub-supervisor-0001"
T0 = 1_800_000_000


@pytest.fixture(autouse=True)
def frozen_clock():
    clock.freeze(T0)
    yield
    clock.reset()


@pytest.fixture
def aws():
    with mock_aws():
        reset_cache()
        yield
        reset_cache()


@pytest.fixture
def stores(aws):
    ddb = boto3.client("dynamodb", region_name=REGION)
    create_tables(ddb)
    s = DynamoStores(boto3.resource("dynamodb", region_name=REGION))
    set_stores(s)
    yield s
    set_stores(None)


@pytest.fixture
def kms(aws):
    return boto3.client("kms", region_name=REGION)


@pytest.fixture
def kms_key(kms):
    kid = kms.create_key(KeySpec="ECC_NIST_P256", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]
    os.environ["KMS_SIGNING_KEY_ID"] = kid
    yield kid
    os.environ.pop("KMS_SIGNING_KEY_ID", None)


@pytest.fixture
def policy():
    return Policy.load()


@pytest.fixture
def agentsafe(stores, kms, kms_key, policy):
    svc = AgentSafeService(policy, stores, KmsSigner(kms_key, kms), KmsVerifier(kms_key, kms))
    set_service(svc)
    adapter.set_client(adapter.AgentSafeClient(service=svc))
    yield svc
    set_service(None)
    adapter.set_client(None)


@pytest.fixture
def workflow():
    d = MemoryDriver()
    set_driver(d)
    yield d
    set_driver(None)


@pytest.fixture
def executor(stores, kms, kms_key, workflow):
    ex = Executor(KmsVerifier(kms_key, kms), stores, workflow)
    set_executor(ex)
    yield ex
    set_executor(None)


@pytest.fixture
def make_order(stores):
    def _make(status: str = "DISPUTED", amount: int = 12000, held: int | None = None, buyer: str = BUYER, **extra):
        if held is None:
            held = 0 if status in {"DRAFT", "PAYMENT_PENDING"} else amount
        order = {
            "order_id": new_id("ord"), "buyer_sub": buyer, "buyer_phone": "+15550000001",
            "buyer_email": "buyer@example.com", "seller_id": SELLER, "seller_phone": "+15550000002",
            "seller_email": "seller@example.com", "item_id": "item_bag", "item_name": "Vintage leather camera bag",
            "qty": 1, "list_amount_minor": amount, "discount_minor": 0, "amount_minor": amount, "currency": "USD",
            "status": status, "held_minor": held, "refunded_minor": 0, "credited_minor": 0, "released_minor": 0,
            "tokens": {"payment": "tok-pay", "dispatch": "tok-disp", "delivery": "tok-del",
                       "dispute": "tok-dispute", "approval": "tok-appr"},
            "created_at": str(clock.now()).zfill(12), "version": 0, "correlation_id": "corr-test",
        }
        order.update(extra)
        stores.put_order(order)
        return order

    return _make
