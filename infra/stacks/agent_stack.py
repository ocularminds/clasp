"""Agent: Bedrock Guardrail (sensitive-information masking), AgentCore Memory, AgentCore Gateway with a
Cognito JWT authorizer and the tools Lambda as its target, and the Runtime execution role.
The Runtime itself is deployed by the starter toolkit (`make agent-deploy`)."""
from __future__ import annotations

import sys
from pathlib import Path

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.tools.schema import TOOL_SCHEMAS  # noqa: E402

from ..common import output, stage  # noqa: E402
from .data_stack import DataStack  # noqa: E402
from .workflow_stack import WorkflowStack  # noqa: E402

PII_TO_MASK = ["CREDIT_DEBIT_CARD_NUMBER", "CREDIT_DEBIT_CARD_CVV", "CREDIT_DEBIT_CARD_EXPIRY", "US_BANK_ACCOUNT_NUMBER",
               "US_BANK_ROUTING_NUMBER", "INTERNATIONAL_BANK_ACCOUNT_NUMBER", "SWIFT_CODE", "PIN", "US_SOCIAL_SECURITY_NUMBER",
               "US_PASSPORT_NUMBER", "DRIVER_ID", "PASSWORD", "AWS_ACCESS_KEY", "AWS_SECRET_KEY"]


def _schema(js: dict) -> agentcore.CfnGatewayTarget.SchemaDefinitionProperty:
    """JSON Schema → Gateway SchemaDefinition (no enum/min/max support; those are dropped)."""
    kwargs = {"type": js.get("type", "string")}
    if js.get("description"):
        kwargs["description"] = js["description"]
    if js.get("type") == "object":
        kwargs["properties"] = {k: _schema(v) for k, v in (js.get("properties") or {}).items()}
        if js.get("required"):
            kwargs["required"] = list(js["required"])
    if js.get("type") == "array" and js.get("items"):
        kwargs["items"] = _schema(js["items"])
    return agentcore.CfnGatewayTarget.SchemaDefinitionProperty(**kwargs)


class AgentStack(Stack):
    def __init__(self, scope: Construct, id_: str, data: DataStack, wf: WorkflowStack, **kwargs):
        super().__init__(scope, id_, **kwargs)

        # --- Guardrail: masking only. It is not the injection defense; AgentSafe is. ----------------
        guardrail = bedrock.CfnGuardrail(
            self, "Guardrail", name=f"clasp-{stage()}-pii-mask",
            description="Masks card and account numbers in agent input and output",
            blocked_input_messaging="I can't take card or account details here. Please use the secure payment link.",
            blocked_outputs_messaging="[redacted]",
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="ANONYMIZE") for t in PII_TO_MASK],
            ),
        )
        version = bedrock.CfnGuardrailVersion(self, "GuardrailVersion", guardrail_identifier=guardrail.attr_guardrail_id,
                                              description="v1")
        output(self, "GUARDRAIL_ID", guardrail.attr_guardrail_id)
        output(self, "GUARDRAIL_VERSION", version.attr_version)

        # --- Memory: one thread per buyer across channels --------------------------------------------
        memory_role = iam.Role(self, "MemoryRole", assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        memory_role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], resources=["*"]))
        memory = agentcore.CfnMemory(
            self, "Memory", name=f"clasp_{stage()}_memory", event_expiry_duration=30,
            description="Clasp conversation memory keyed by the buyer's Cognito sub",
            memory_execution_role_arn=memory_role.role_arn,
            memory_strategies=[
                agentcore.CfnMemory.MemoryStrategyProperty(summary_memory_strategy=agentcore.CfnMemory.SummaryMemoryStrategyProperty(
                    name="sessionSummary", namespaces=["/summaries/{actorId}/{sessionId}"])),
                agentcore.CfnMemory.MemoryStrategyProperty(semantic_memory_strategy=agentcore.CfnMemory.SemanticMemoryStrategyProperty(
                    name="facts", namespaces=["/facts/{actorId}"])),
            ],
        )
        output(self, "MEMORY_ID", memory.attr_memory_id)

        # --- Gateway inbound auth: Cognito client-credentials ------------------------------------------
        gw_pool = cognito.UserPool(self, "GatewayAuthPool", user_pool_name=f"clasp-{stage()}-gateway-auth",
                                   self_sign_up_enabled=False, removal_policy=RemovalPolicy.DESTROY)
        domain = gw_pool.add_domain("GatewayAuthDomain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"clasp-gw-{stage()}-{self.account}"))
        scope_invoke = cognito.ResourceServerScope(scope_name="invoke", scope_description="Invoke Clasp tools")
        rs = gw_pool.add_resource_server("GatewayResourceServer", identifier="clasp-gateway", scopes=[scope_invoke])
        gw_client = gw_pool.add_client(
            "GatewayClient", generate_secret=True,
            o_auth=cognito.OAuthSettings(flows=cognito.OAuthFlows(client_credentials=True),
                                         scopes=[cognito.OAuthScope.resource_server(rs, scope_invoke)]),
        )
        client_secret = sm.Secret(self, "GatewayClientSecret", secret_name=f"clasp/{stage()}/gateway-client-secret",
                                  secret_string_value=gw_client.user_pool_client_secret)
        discovery_url = f"https://cognito-idp.{self.region}.amazonaws.com/{gw_pool.user_pool_id}/.well-known/openid-configuration"
        output(self, "GATEWAY_TOKEN_URL", f"https://{domain.domain_name}.auth.{self.region}.amazoncognito.com/oauth2/token")
        output(self, "GATEWAY_CLIENT_ID", gw_client.user_pool_client_id)
        output(self, "GATEWAY_CLIENT_SECRET_ARN", client_secret.secret_arn)
        output(self, "GATEWAY_SCOPE", "clasp-gateway/invoke")

        # --- Gateway + Lambda target -----------------------------------------------------------------------
        gw_role = iam.Role(self, "GatewayRole", assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"))
        wf.tools.grant_invoke(gw_role)
        gateway = agentcore.CfnGateway(
            self, "Gateway", name=f"clasp-{stage()}-tools", role_arn=gw_role.role_arn, protocol_type="MCP",
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=discovery_url, allowed_clients=[gw_client.user_pool_client_id])),
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(
                    instructions="Clasp marketplace tools. Money actions are proposals decided by AgentSafe.",
                    supported_versions=["2025-03-26"])),
            description="Clasp agent tools (Lambda target)",
        )
        target = agentcore.CfnGatewayTarget(
            self, "ToolsTarget", gateway_identifier=gateway.attr_gateway_identifier, name="clasp-tools",
            description="services/tools via Lambda",
            credential_provider_configurations=[agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                credential_provider_type="GATEWAY_IAM_ROLE")],
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=wf.tools.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(inline_payload=[
                            agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                                name=t["name"], description=t["description"], input_schema=_schema(t["inputSchema"]))
                            for t in TOOL_SCHEMAS])))),
        )
        target.add_dependency(gateway)
        output(self, "GATEWAY_URL", gateway.attr_gateway_url)
        output(self, "GATEWAY_ID", gateway.attr_gateway_identifier)

        # --- Runtime execution role (used by `agentcore configure --execution-role`) -----------------------
        runtime_role = iam.Role(
            self, "AgentRuntimeRole", role_name=f"clasp-{stage()}-agent-runtime",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com",
                                            conditions={"StringEquals": {"aws:SourceAccount": self.account}}),
        )
        log_key = kms.Key(self, "RuntimeLogKey", alias=f"alias/clasp/{stage()}/runtime-logs",
                          enable_key_rotation=True, removal_policy=RemovalPolicy.DESTROY)
        log_service = f"logs.{self.region}.amazonaws.com"
        log_context = {"kms:EncryptionContext:aws:logs:arn":
                       f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/clasp_agent-*"}
        log_key_actions = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        log_key.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal(log_service)], actions=log_key_actions, resources=["*"],
            conditions={"ArnLike": log_context}))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=log_key_actions, resources=[log_key.key_arn],
            conditions={"StringEquals": {"kms:ViaService": log_service}, "ArnLike": log_context}))
        output(self, "AGENT_LOG_KMS_KEY_ARN", log_key.key_arn)
        runtime_role.add_to_policy(iam.PolicyStatement(actions=[
            "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail"], resources=["*"]))
        runtime_role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:*Memory*", "bedrock-agentcore:*Event*",
                                                                 "bedrock-agentcore:RetrieveMemoryRecords",
                                                                 "bedrock-agentcore:GetWorkloadAccessToken*",
                                                                 "bedrock-agentcore:InvokeGateway"], resources=["*"]))
        runtime_role.add_to_policy(iam.PolicyStatement(actions=["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                                                                 "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                                                                 "logs:DescribeLogStreams", "logs:DescribeLogGroups",
                                                                 "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                                                 "cloudwatch:PutMetricData", "ssm:GetParameter"], resources=["*"]))
        client_secret.grant_read(runtime_role)
        output(self, "AGENT_EXECUTION_ROLE_ARN", runtime_role.role_arn)
