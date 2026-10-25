"""HTTP API: hosted checkout + PSP webhook, approval links. Publishes its own base URL to SSM so
Lambdas in other stacks can build links without a cross-stack cycle."""
from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from ..common import COMMON_ENV, env, make_function, output, stage
from .data_stack import DataStack
from .workflow_stack import WorkflowStack, read_tables, table_env


class ApiStack(Stack):
    def __init__(self, scope: Construct, id_: str, data: DataStack, wf: WorkflowStack, **kwargs):
        super().__init__(scope, id_, **kwargs)
        base_env = {**COMMON_ENV, **table_env(data), "KMS_SIGNING_KEY_ID": data.signing_key.key_id,
                    "AGENTSAFE_FUNCTION_ARN": wf.agentsafe.function_arn, "EXECUTOR_FUNCTION_ARN": wf.executor.function_arn,
                    "PSP_WEBHOOK_SECRET_ARN": data.psp_secret.secret_arn, "SSM_PREFIX": f"/clasp/{stage()}",
                    "SUPERVISOR_EMAIL": env("SUPERVISOR_EMAIL"), "PSP": env("PSP", "mock")}

        self.psp = make_function(self, "Psp", "psp_mock.handler", base_env, timeout=20,
                                 description="Mock PSP: hosted checkout page + HMAC-signed webhook receiver")
        read_tables(self.psp, data, "orders")
        data.psp_secret.grant_read(self.psp)
        wf.agentsafe.grant_invoke(self.psp)
        wf.executor.grant_invoke(self.psp)

        self.approvals = make_function(self, "Approvals", "approvals.handler", base_env, timeout=20,
                                       description="SES approval link handler (verifies the signed token first)")
        read_tables(self.approvals, data, "orders", "challenges")
        data.signing_key.grant(self.approvals, "kms:Verify")
        wf.agentsafe.grant_invoke(self.approvals)
        wf.executor.grant_invoke(self.approvals)
        self.approvals.add_to_role_policy(iam.PolicyStatement(actions=["states:SendTaskFailure", "states:SendTaskSuccess"], resources=["*"]))

        self.api = apigw.HttpApi(self, "HttpApi", api_name=f"clasp-{stage()}", description="Clasp: checkout, webhooks, approvals")
        psp_int = integrations.HttpLambdaIntegration("PspIntegration", self.psp)
        self.api.add_routes(path="/pay/{checkout_id}", methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST], integration=psp_int)
        self.api.add_routes(path="/webhooks/psp", methods=[apigw.HttpMethod.POST], integration=psp_int)
        self.api.add_routes(path="/approvals", methods=[apigw.HttpMethod.GET],
                            integration=integrations.HttpLambdaIntegration("ApprovalsIntegration", self.approvals))

        self.base_url_param = ssm.StringParameter(self, "ApiBaseUrlParam", parameter_name=f"/clasp/{stage()}/api_base_url",
                                                  string_value=self.api.api_endpoint)
        for fn in (self.psp, self.approvals):
            fn.add_environment("API_BASE_URL", self.api.api_endpoint)
        output(self, "API_BASE_URL", self.api.api_endpoint)
