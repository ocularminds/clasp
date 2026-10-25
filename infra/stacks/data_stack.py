"""Data: DynamoDB tables (from services/common/tables.py), media bucket, the AgentSafe signing key,
the end-user Cognito pool (phone attribute), and the PSP webhook secret."""
from __future__ import annotations

import sys
from pathlib import Path

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.common.tables import TABLE_SPECS  # noqa: E402

from ..common import output, stage, table_name  # noqa: E402


class DataStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs):
        super().__init__(scope, id_, **kwargs)
        self.tables: dict[str, ddb.Table] = {}
        for logical, spec in TABLE_SPECS.items():
            t = ddb.Table(
                self, f"{logical.capitalize()}Table",
                table_name=table_name(logical),
                partition_key=ddb.Attribute(name=spec["pk"], type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name=spec["sk"], type=ddb.AttributeType.STRING) if spec.get("sk") else None,
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute=spec.get("ttl"),
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=logical == "ledger"),
                removal_policy=RemovalPolicy.RETAIN if logical in {"ledger", "decisions"} else RemovalPolicy.DESTROY,
            )
            for name, g in spec.get("gsis", {}).items():
                t.add_global_secondary_index(
                    index_name=name,
                    partition_key=ddb.Attribute(name=g["pk"], type=ddb.AttributeType.STRING),
                    sort_key=ddb.Attribute(name=g["sk"], type=ddb.AttributeType.STRING) if g.get("sk") else None,
                )
            self.tables[logical] = t
            output(self, f"{logical.upper()}_TABLE", t.table_name)

        self.media = s3.Bucket(
            self, "MediaBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90))],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        output(self, "MEDIA_BUCKET", self.media.bucket_name)

        # ECC P-256 asymmetric key: AgentSafe signs ES256 authorizations; the executor verifies with kms:Verify.
        self.signing_key = kms.Key(
            self, "AgentSafeSigningKey",
            key_spec=kms.KeySpec.ECC_NIST_P256,
            key_usage=kms.KeyUsage.SIGN_VERIFY,
            alias=f"alias/clasp-{stage()}-agentsafe",
            description="Clasp AgentSafe authorization signing key (ES256)",
            removal_policy=RemovalPolicy.DESTROY,
            pending_window=Duration.days(7),
        )
        output(self, "KMS_SIGNING_KEY_ID", self.signing_key.key_id)

        # End users: phone number is the identity ingress resolves to a sub.
        self.user_pool = cognito.UserPool(
            self, "Users",
            user_pool_name=f"clasp-{stage()}-users",
            sign_in_aliases=cognito.SignInAliases(phone=True),
            self_sign_up_enabled=False,
            standard_attributes=cognito.StandardAttributes(
                phone_number=cognito.StandardAttribute(required=True, mutable=True),
                email=cognito.StandardAttribute(required=False, mutable=True),
            ),
            custom_attributes={"role": cognito.StringAttribute(mutable=True)},
            removal_policy=RemovalPolicy.DESTROY,
        )
        output(self, "COGNITO_USER_POOL_ID", self.user_pool.user_pool_id)

        self.psp_secret = sm.Secret(
            self, "PspWebhookSecret",
            secret_name=f"clasp/{stage()}/psp-webhook-secret",
            description="HMAC secret shared by the mock PSP and the webhook receiver",
            generate_secret_string=sm.SecretStringGenerator(password_length=48, exclude_punctuation=True),
        )
        output(self, "PSP_WEBHOOK_SECRET_ARN", self.psp_secret.secret_arn)
