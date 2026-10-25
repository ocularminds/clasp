"""Workflow + trust core: the AgentSafe, executor, tools and task Lambdas, and the order state machine.

IAM is where the invariant lives in the cloud:
  * only the executor role may PutItem on the ledger table;
  * only the AgentSafe role may kms:Sign with the authorization key;
  * the tools role (what the model reaches through Gateway) can only *invoke* those two."""
from __future__ import annotations

import sys
from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ..common import COMMON_ENV, env, make_function, output, stage  # noqa: E402
from .data_stack import DataStack  # noqa: E402

MESSAGING_ACTIONS = ["sms-voice:SendTextMessage", "sms-voice:SendRcsMessage", "sms-voice:SendMediaMessage",
                     "social-messaging:SendWhatsAppMessage", "social-messaging:PostWhatsAppMessageMedia",
                     "ses:SendEmail", "ses:SendRawEmail"]


def table_env(data: DataStack) -> dict[str, str]:
    return {f"{k.upper()}_TABLE": t.table_name for k, t in data.tables.items()}


def read_tables(grantee: iam.IGrantable, data: DataStack, *names: str) -> None:
    for n in names:
        data.tables[n].grant_read_data(grantee)


class WorkflowStack(Stack):
    def __init__(self, scope: Construct, id_: str, data: DataStack, **kwargs):
        super().__init__(scope, id_, **kwargs)
        ssm_prefix = f"/clasp/{stage()}"
        base_env = {**COMMON_ENV, **table_env(data), "MEDIA_BUCKET": data.media.bucket_name,
                    "KMS_SIGNING_KEY_ID": data.signing_key.key_id, "SSM_PREFIX": ssm_prefix,
                    "SES_FROM_EMAIL": env("SES_FROM_EMAIL"), "SUPERVISOR_EMAIL": env("SUPERVISOR_EMAIL"),
                    "WHATSAPP_PHONE_NUMBER_ID": env("WHATSAPP_PHONE_NUMBER_ID"), "RCS_POOL_ID": env("RCS_POOL_ID"),
                    "SMS_ORIGINATION_IDENTITY": env("SMS_ORIGINATION_IDENTITY"),
                    "EVIDENCE_MODE": env("EVIDENCE_MODE_DEPLOYED", "bedrock"),
                    "BEDROCK_VISION_MODEL_ID": env("BEDROCK_VISION_MODEL_ID", env("BEDROCK_MODEL_ID")),
                    "GUARDRAIL_ID": env("GUARDRAIL_ID"), "GUARDRAIL_VERSION": env("GUARDRAIL_VERSION")}
        ssm_read = iam.PolicyStatement(actions=["ssm:GetParameter"],
                                       resources=[self.format_arn(service="ssm", resource="parameter", resource_name=f"clasp/{stage()}/*")])

        # --- AgentSafe (reference policy service) -------------------------------------------
        self.agentsafe = make_function(self, "AgentSafe", "agentsafe.handler", base_env, timeout=20,
                                       description="AgentSafe reference policy service: decide + approval links")
        data.signing_key.grant(self.agentsafe, "kms:Sign", "kms:Verify", "kms:GetPublicKey")
        read_tables(self.agentsafe, data, "orders", "ledger")
        data.tables["decisions"].grant_read_write_data(self.agentsafe)
        output(self, "AGENTSAFE_FUNCTION_ARN", self.agentsafe.function_arn)

        # --- Executor: the only ledger writer -------------------------------------------------
        self.executor = make_function(self, "Executor", "executor.handler", base_env, timeout=20,
                                      description="Verifies AgentSafe authorizations; the only ledger writer")
        data.signing_key.grant(self.executor, "kms:Verify")
        read_tables(self.executor, data, "orders", "nonces", "ledger")
        self.executor.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"], resources=[data.tables["ledger"].table_arn, data.tables["nonces"].table_arn]))
        self.executor.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"], resources=[data.tables["orders"].table_arn]))
        self.executor.add_to_role_policy(iam.PolicyStatement(
            actions=["states:SendTaskSuccess", "states:SendTaskFailure"], resources=["*"]))
        output(self, "EXECUTOR_FUNCTION_ARN", self.executor.function_arn)

        trust_env = {**base_env, "AGENTSAFE_FUNCTION_ARN": self.agentsafe.function_arn,
                     "EXECUTOR_FUNCTION_ARN": self.executor.function_arn}

        # --- Workflow task bodies ------------------------------------------------------------
        self.tasks_fn = make_function(self, "WorkflowTasks", "workflows.handler", trust_env, timeout=60,
                                      description="Step Functions task bodies (no money moves)")
        read_tables(self.tasks_fn, data, "orders", "ledger", "decisions", "challenges", "items")
        for t in ("orders", "challenges"):
            self.tasks_fn.add_to_role_policy(iam.PolicyStatement(actions=["dynamodb:UpdateItem", "dynamodb:PutItem"],
                                                                 resources=[data.tables[t].table_arn]))
        self.tasks_fn.add_to_role_policy(iam.PolicyStatement(actions=MESSAGING_ACTIONS, resources=["*"]))
        self.tasks_fn.add_to_role_policy(ssm_read)
        data.media.grant_read(self.tasks_fn)
        self.agentsafe.grant_invoke(self.tasks_fn)

        # --- State machine (mirrored by services/workflows/local_runner.py) ---------------------
        def wait(name: str, phase: str, hours: int = 72) -> tasks.LambdaInvoke:
            return tasks.LambdaInvoke(
                self, name, lambda_function=self.tasks_fn,
                integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
                payload=sfn.TaskInput.from_object({
                    "step": "register_token", "phase": phase, "token": sfn.JsonPath.task_token,
                    "order_id": sfn.JsonPath.string_at("$.order_id"),
                    "correlation_id": sfn.JsonPath.string_at("$.correlation_id"),
                }),
                result_path="$.last", task_timeout=sfn.Timeout.duration(Duration.hours(hours)),
            )

        def work(name: str, step: str, last: dict | None = None) -> tasks.LambdaInvoke:
            return tasks.LambdaInvoke(
                self, name, lambda_function=self.tasks_fn, payload_response_only=True,
                payload=sfn.TaskInput.from_object({
                    "step": step, "order_id": sfn.JsonPath.string_at("$.order_id"),
                    "last": last if last is not None else sfn.JsonPath.object_at("$.last"),
                }),
                result_path=sfn.JsonPath.DISCARD,
                retry_on_service_exceptions=True,
            )

        payment_pending = wait("PaymentPending", "payment", hours=24)
        funds_held = sfn.Pass(self, "FundsHeld", comment="HOLD written by the executor on the PSP webhook (AgentSafe-authorized)")
        notify_seller = work("NotifySellerFundsHeld", "notify_seller_funds_held")
        await_dispatch = wait("AwaitDispatch", "dispatch")
        mark_dispatched = work("MarkDispatched", "mark_dispatched")
        send_code = work("SendDeliveryCode", "send_delivery_code")
        await_delivery = wait("AwaitDelivery", "delivery")
        send_receipts = work("SendReceipts", "send_receipts")
        released = sfn.Succeed(self, "Released", comment="RELEASE written by the executor on a verified delivery code")
        mark_disputed = work("MarkDisputed", "mark_disputed")
        await_resolution = wait("AwaitResolution", "dispute")
        send_pack = work("SendEvidencePack", "send_evidence_pack")
        resolved = sfn.Succeed(self, "Resolved", comment="RESOLVED_* written by the executor (refund / credit / replacement)")
        mark_escalated = work("MarkEscalated", "mark_escalated")
        send_approval = work("SendApprovalEmail", "send_approval_email")
        await_approval = wait("AwaitApproval", "approval", hours=48)
        redisputed = work("MarkDisputedAfterRejection", "mark_disputed",
                          last={"after_rejection": True, "reason": "supervisor rejected the escalated refund"})

        delivery_outcome = (sfn.Choice(self, "DeliveryOutcome")
                            .when(sfn.Condition.string_equals("$.last.outcome", "released"), send_receipts.next(released))
                            .otherwise(mark_disputed.next(await_resolution)))
        dispute_outcome = (sfn.Choice(self, "DisputeOutcome")
                           .when(sfn.Condition.string_equals("$.last.outcome", "resolved"), send_pack)
                           .otherwise(mark_escalated.next(send_approval).next(await_approval)))
        send_pack.next(resolved)
        await_approval.add_catch(redisputed, errors=["ApprovalRejected"], result_path="$.error")
        redisputed.next(await_resolution)
        await_approval.next(send_pack)
        await_resolution.next(dispute_outcome)
        await_delivery.next(delivery_outcome)

        definition = (payment_pending.next(funds_held).next(notify_seller).next(await_dispatch).next(mark_dispatched)
                      .next(send_code).next(await_delivery))
        self.state_machine = sfn.StateMachine(
            self, "OrderWorkflow", state_machine_name=f"clasp-{stage()}-order",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.days(14), tracing_enabled=True,
        )
        output(self, "STATE_MACHINE_ARN", self.state_machine.state_machine_arn)

        # --- Tools (what the model reaches through Gateway) ------------------------------------
        self.tools = make_function(self, "Tools", "tools.handler", {**trust_env, "STATE_MACHINE_ARN": self.state_machine.state_machine_arn},
                                   timeout=60, memory=768, description="Agent tools (AgentCore Gateway Lambda target)")
        read_tables(self.tools, data, "orders", "ledger", "decisions", "challenges", "items", "parties")
        for t in ("orders", "challenges", "items"):
            self.tools.add_to_role_policy(iam.PolicyStatement(actions=["dynamodb:UpdateItem", "dynamodb:PutItem"],
                                                              resources=[data.tables[t].table_arn]))
        # NOTE: no dynamodb:PutItem on the ledger or nonces table, no kms:Sign. Tests assert this.
        self.tools.add_to_role_policy(iam.PolicyStatement(actions=["states:StartExecution"], resources=[self.state_machine.state_machine_arn]))
        self.tools.add_to_role_policy(iam.PolicyStatement(actions=["states:SendTaskSuccess", "states:SendTaskFailure"], resources=["*"]))
        self.tools.add_to_role_policy(iam.PolicyStatement(actions=MESSAGING_ACTIONS, resources=["*"]))
        self.tools.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel", "bedrock:ApplyGuardrail"], resources=["*"]))
        self.tools.add_to_role_policy(ssm_read)
        data.media.grant_read_write(self.tools)
        self.agentsafe.grant_invoke(self.tools)
        self.executor.grant_invoke(self.tools)
        output(self, "TOOLS_FUNCTION_ARN", self.tools.function_arn)
