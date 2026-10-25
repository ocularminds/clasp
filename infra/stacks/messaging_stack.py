"""Messaging: SNS inbound topics (WhatsApp via End User Messaging Social, SMS/RCS via SMS v2 two-way),
the single ingress Lambda, and the SES sender identity.

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
from aws_cdk import aws_ses as ses
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

        self.ingress = make_function(
            self, "Ingress", "ingress.handler",
            {**COMMON_ENV, **table_env(data), "MEDIA_BUCKET": data.media.bucket_name, "AGENT_MODE": "remote",
             "COGNITO_USER_POOL_ID": data.user_pool.user_pool_id, "SSM_PREFIX": f"/clasp/{stage()}",
             "WHATSAPP_PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"), "RCS_POOL_ID": env("RCS_POOL_ID"),
             "SMS_ORIGINATION_IDENTITY": env("SMS_ORIGINATION_IDENTITY"), "SES_FROM_EMAIL": env("SES_FROM_EMAIL"),
             "AGENT_RUNTIME_ARN": env("AGENT_RUNTIME_ARN")},
            timeout=120, memory=512, description="SNS → normalize → resolve sub → InvokeAgentRuntime → reply",
        )
        self.ingress.add_event_source(sources.SnsEventSource(self.whatsapp_topic))
        self.ingress.add_event_source(sources.SnsEventSource(self.sms_topic))
        read_tables(self.ingress, data, "parties", "orders")
        data.media.grant_read_write(self.ingress)
        self.ingress.add_to_role_policy(iam.PolicyStatement(actions=["cognito-idp:ListUsers"], resources=[data.user_pool.user_pool_arn]))
        self.ingress.add_to_role_policy(iam.PolicyStatement(
            actions=MESSAGING_ACTIONS + ["social-messaging:GetWhatsAppMessageMedia", "bedrock-agentcore:InvokeAgentRuntime",
                                         "ssm:GetParameter"], resources=["*"]))

        sender = env("SES_FROM_EMAIL")
        if sender and not sender.endswith("example.com"):
            # Creates the identity; the verification email still has to be clicked (SES sandbox).
            ses.EmailIdentity(self, "SenderIdentity", identity=ses.Identity.email(sender))
