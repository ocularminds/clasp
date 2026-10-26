"""Messaging: SNS inbound topics (WhatsApp via End User Messaging Social, SMS/RCS via SMS v2 two-way),
the single ingress Lambda, and an RCS media upload role.

TODO(human) after deploy — these bind console-created resources to the topics:
  aws socialmessaging put-whatsapp-business-account-event-destinations --id <WABA_ID> \
      --event-destinations eventDestinationArn=<INBOUND_WHATSAPP_TOPIC_ARN>
  aws pinpoint-sms-voice-v2 update-pool --pool-id <RCS_POOL_ID> --two-way-enabled \
      --two-way-channel-arn <INBOUND_SMS_TOPIC_ARN> --two-way-channel-role <ROLE or omit with topic policy>
"""
from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_sns as sns
from constructs import Construct

from ..common import COMMON_ENV, env, make_function, output, stage
from .data_stack import DataStack
from .workflow_stack import MESSAGING_ACTIONS, read_tables, table_env


class MessagingStack(Stack):
    def __init__(self, scope: Construct, id_: str, data: DataStack, **kwargs):
        super().__init__(scope, id_, **kwargs)
        self.whatsapp_topic = sns.Topic(self, "InboundWhatsApp", topic_name=f"clasp-{stage()}-inbound-whatsapp")
        self.sms_topic = sns.Topic(self, "InboundSms", topic_name=f"clasp-{stage()}-inbound-sms")
        # Let End User Messaging publish inbound events.
        for topic in (self.whatsapp_topic, self.sms_topic):
            topic.add_to_resource_policy(iam.PolicyStatement(
                actions=["sns:Publish"], resources=[topic.topic_arn],
                principals=[iam.ServicePrincipal("social-messaging.amazonaws.com"), iam.ServicePrincipal("sms-voice.amazonaws.com")],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ))
        output(self, "INBOUND_WHATSAPP_TOPIC_ARN", self.whatsapp_topic.topic_arn)
        output(self, "INBOUND_SMS_TOPIC_ARN", self.sms_topic.topic_arn)

        # EUM stores inbound RCS photos before publishing their S3 location to SNS.
        # AWS documents SourceAccount for this service's media-role assumption.
        self.rcs_media_role = iam.Role(
            self, "RcsInboundMediaRole", role_name=f"clasp-{stage()}-rcs-media",
            assumed_by=iam.ServicePrincipal("sms-voice.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": self.account}}),
        )
        self.rcs_media_role.add_to_policy(iam.PolicyStatement(
            # EUM writes a setup notification directly under the configured prefix.
            actions=["s3:PutObject"], resources=[
                data.media.arn_for_objects("inbound/rcs/*"),
                data.media.arn_for_objects("inbound/AWS_END_USER_MESSAGING_SETUP_NOTIFICATION"),
            ]))
        output(self, "RCS_MEDIA_ROLE_ARN", self.rcs_media_role.role_arn)

        self.ingress = make_function(
            self, "Ingress", "ingress.handler",
            {**COMMON_ENV, **table_env(data), "MEDIA_BUCKET": data.media.bucket_name, "AGENT_MODE": "remote",
             "COGNITO_USER_POOL_ID": data.user_pool.user_pool_id, "SSM_PREFIX": f"/clasp/{stage()}",
             "WHATSAPP_PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"), "RCS_POOL_ID": env("RCS_POOL_ID"),
             "SMS_ORIGINATION_IDENTITY": env("SMS_ORIGINATION_IDENTITY"), "SES_FROM_EMAIL": env("SES_FROM_EMAIL"),
             "AGENT_RUNTIME_ARN": env("AGENT_RUNTIME_ARN"), "AUTO_ENROL_CHANNELS": env("AUTO_ENROL_CHANNELS")},
            timeout=120, memory=512, description="SNS → normalize → resolve sub → InvokeAgentRuntime → reply",
        )
        self.ingress.add_event_source(sources.SnsEventSource(self.whatsapp_topic))
        self.ingress.add_event_source(sources.SnsEventSource(self.sms_topic))
        read_tables(self.ingress, data, "parties", "orders")
        data.media.grant_read_write(self.ingress)
        # ListUsers resolves a phone to its sub; AdminCreateUser/AdminGetUser enrol first-contact senders when
        # AUTO_ENROL_CHANNELS is set. Enrolment also writes the parties table (never the ledger).
        self.ingress.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:ListUsers", "cognito-idp:AdminCreateUser", "cognito-idp:AdminGetUser"],
            resources=[data.user_pool.user_pool_arn]))
        self.ingress.add_to_role_policy(iam.PolicyStatement(actions=["dynamodb:PutItem", "dynamodb:UpdateItem"],
                                                            resources=[data.tables["parties"].table_arn]))
        self.ingress.add_to_role_policy(iam.PolicyStatement(
            actions=MESSAGING_ACTIONS + ["social-messaging:GetWhatsAppMessageMedia", "ssm:GetParameter"], resources=["*"]))
        # IAM Policy Autopilot maps the user-bound call to both permissions.
        self.ingress.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:InvokeAgentRuntimeForUser"],
            resources=[self.format_arn(service="bedrock-agentcore", resource="runtime/clasp_agent-*")]))

        # SES identities are verified before deployment and managed outside this stack.
        # Creating the already-verified sender here fails with AlreadyExists.
