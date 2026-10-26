"""The CDK app synthesizes, the state machine mirrors the local runner, and IAM enforces the boundary:
only the executor role may PutItem on the ledger; only AgentSafe may kms:Sign."""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("aws_cdk")
os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Template  # noqa: E402

from infra.app import build  # noqa: E402


@pytest.fixture(scope="module")
def stacks():
    app = cdk.App(context={"aws:cdk:bundling-stacks": []})  # skip pip bundling in tests
    os.environ["STAGE"] = "test"
    return build(app, cdk.Environment(account="123456789012", region="us-east-1"))


@pytest.fixture(scope="module")
def templates(stacks):
    return {k: Template.from_stack(v) for k, v in stacks.items()}


def test_tables_match_specs(templates):
    from services.common.tables import TABLE_SPECS

    t = templates["data"]
    t.resource_count_is("AWS::DynamoDB::Table", len(TABLE_SPECS))
    t.has_resource_properties("AWS::KMS::Key", {"KeySpec": "ECC_NIST_P256", "KeyUsage": "SIGN_VERIFY"})
    t.has_resource_properties("AWS::Cognito::UserPool", {"UsernameAttributes": ["phone_number"]})


def test_state_machine_mirrors_local_runner(templates):
    sm = templates["workflow"].find_resources("AWS::StepFunctions::StateMachine")
    assert len(sm) == 1
    definition = json.dumps(next(iter(sm.values()))["Properties"]["DefinitionString"])
    for state in ("PaymentPending", "FundsHeld", "NotifySellerFundsHeld", "AwaitDispatch", "MarkDispatched", "SendDeliveryCode",
                  "AwaitDelivery", "DeliveryOutcome", "SendReceipts", "Released", "MarkDisputed", "AwaitResolution",
                  "DisputeOutcome", "SendEvidencePack", "Resolved", "MarkEscalated", "SendApprovalEmail", "AwaitApproval",
                  "MarkDisputedAfterRejection"):
        assert state in definition, state
    assert "waitForTaskToken" in definition and "ApprovalRejected" in definition
    for step in ("register_token", "notify_seller_funds_held", "mark_dispatched", "send_delivery_code", "send_receipts",
                 "mark_disputed", "mark_escalated", "send_approval_email", "send_evidence_pack"):
        assert step in definition, step


def _policies_for_role(template: Template, role_logical_prefix: str) -> list[dict]:
    out = []
    for name, res in template.find_resources("AWS::IAM::Policy").items():
        roles = json.dumps(res["Properties"].get("Roles", []))
        if role_logical_prefix in roles:
            out.extend(res["Properties"]["PolicyDocument"]["Statement"])
    return out


def _allows(statements: list[dict], action_substr: str, resource_substr: str) -> bool:
    for st in statements:
        actions = st.get("Action", [])
        actions = [actions] if isinstance(actions, str) else actions
        if not any(action_substr in a for a in actions):
            continue
        res = json.dumps(st.get("Resource", ""))
        if resource_substr in res or '"*"' in res:
            return True
    return False


def test_only_executor_can_write_the_ledger(templates):
    wf = templates["workflow"]
    executor = _policies_for_role(wf, "ExecutorServiceRole")
    tools = _policies_for_role(wf, "ToolsServiceRole")
    tasks_ = _policies_for_role(wf, "WorkflowTasksServiceRole")
    agentsafe = _policies_for_role(wf, "AgentSafeServiceRole")
    assert _allows(executor, "dynamodb:PutItem", "LedgerTable")
    for name, pol in (("tools", tools), ("tasks", tasks_), ("agentsafe", agentsafe)):
        assert not _allows(pol, "dynamodb:PutItem", "LedgerTable"), f"{name} can write the ledger"
        assert not _allows(pol, "dynamodb:UpdateItem", "LedgerTable"), f"{name} can update the ledger"
        assert not _allows(pol, "dynamodb:DeleteItem", "LedgerTable"), f"{name} can delete from the ledger"
    assert not _allows(executor, "dynamodb:DeleteItem", "LedgerTable")  # append-only even for the executor


def test_only_agentsafe_can_sign(templates):
    wf = templates["workflow"]
    assert _allows(_policies_for_role(wf, "AgentSafeServiceRole"), "kms:Sign", "")
    for role in ("ExecutorServiceRole", "ToolsServiceRole", "WorkflowTasksServiceRole"):
        assert not _allows(_policies_for_role(wf, role), "kms:Sign", ""), f"{role} can sign authorizations"
    for role in ("PspServiceRole", "ApprovalsServiceRole"):
        assert not _allows(_policies_for_role(templates["api"], role), "kms:Sign", ""), f"{role} can sign authorizations"


def test_agent_stack_wires_gateway_memory_guardrail(templates):
    a = templates["agent"]
    a.resource_count_is("AWS::BedrockAgentCore::Gateway", 1)
    a.resource_count_is("AWS::BedrockAgentCore::GatewayTarget", 1)
    a.resource_count_is("AWS::BedrockAgentCore::Memory", 1)
    a.resource_count_is("AWS::Bedrock::Guardrail", 1)
    target = next(iter(a.find_resources("AWS::BedrockAgentCore::GatewayTarget").values()))
    tools = target["Properties"]["TargetConfiguration"]["Mcp"]["Lambda"]["ToolSchema"]["InlinePayload"]
    from services.tools.schema import TOOL_SCHEMAS

    assert {t["Name"] for t in tools} == {t["name"] for t in TOOL_SCHEMAS}
    guardrail = next(iter(a.find_resources("AWS::Bedrock::Guardrail").values()))
    types = {p["Type"] for p in guardrail["Properties"]["SensitiveInformationPolicyConfig"]["PiiEntitiesConfig"]}
    assert "CREDIT_DEBIT_CARD_NUMBER" in types and "US_BANK_ACCOUNT_NUMBER" in types


def test_api_routes(templates):
    api = templates["api"]
    routes = {r["Properties"]["RouteKey"] for r in api.find_resources("AWS::ApiGatewayV2::Route").values()}
    assert routes == {"GET /pay/{checkout_id}", "POST /pay/{checkout_id}", "POST /webhooks/psp", "GET /approvals"}
    templates["messaging"].resource_count_is("AWS::SNS::Topic", 2)


def test_rcs_media_upload_role_is_limited_to_its_prefix(templates):
    messaging = templates["messaging"]
    statements = _policies_for_role(messaging, "RcsInboundMediaRole")
    actions = [a for s in statements for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])]
    assert actions == ["s3:PutObject"]
    assert "inbound/rcs/*" in json.dumps(statements)
    assert "inbound/AWS_END_USER_MESSAGING_SETUP_NOTIFICATION" in json.dumps(statements)
    assert "inbound/*" not in json.dumps(statements)
    role = next(v for k, v in messaging.find_resources("AWS::IAM::Role").items() if k.startswith("RcsInboundMediaRole"))
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["Service"] == "sms-voice.amazonaws.com"
    assert trust["Condition"]["StringEquals"]["aws:SourceAccount"] == "123456789012"


def test_load_env_honours_skip_flag(tmp_path, monkeypatch):
    """A developer's .env must never reach the test process through the CDK helper (it did: real demo phones
    leaked into later e2e tests once the synth test had run)."""
    import infra.common as common

    (tmp_path / ".env").write_text("CLASP_LEAK_PROBE=leaked\n")
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.delenv("CLASP_LEAK_PROBE", raising=False)
    monkeypatch.setenv("CLASP_SKIP_DOTENV", "1")
    common.load_env()
    assert "CLASP_LEAK_PROBE" not in os.environ
    monkeypatch.delenv("CLASP_SKIP_DOTENV")
    common.load_env()
    assert os.environ["CLASP_LEAK_PROBE"] == "leaked"


def test_knowledge_stack_holds_the_policy_on_s3_vectors(templates):
    k = templates["knowledge"]
    k.resource_count_is("AWS::Bedrock::KnowledgeBase", 1)
    k.resource_count_is("AWS::Bedrock::DataSource", 1)
    k.resource_count_is("AWS::S3Vectors::VectorBucket", 1)
    k.resource_count_is("AWS::S3Vectors::Index", 1)
    kb = next(iter(k.find_resources("AWS::Bedrock::KnowledgeBase").values()))["Properties"]
    assert kb["StorageConfiguration"]["Type"] == "S3_VECTORS"
    assert kb["KnowledgeBaseConfiguration"]["VectorKnowledgeBaseConfiguration"]["EmbeddingModelConfiguration"]["BedrockEmbeddingModelConfiguration"]["Dimensions"] == 1024
    index = next(iter(k.find_resources("AWS::S3Vectors::Index").values()))["Properties"]
    assert index["Dimension"] == 1024 and index["DistanceMetric"] == "cosine"
    assert set(index["MetadataConfiguration"]["NonFilterableMetadataKeys"]) == {"AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"}
    ds = next(iter(k.find_resources("AWS::Bedrock::DataSource").values()))["Properties"]
    assert ds["DataSourceConfiguration"]["S3Configuration"]["InclusionPrefixes"] == ["policy/"]
    assert ds["VectorIngestionConfiguration"]["ChunkingConfiguration"]["ChunkingStrategy"] == "FIXED_SIZE"
    params = {p["Properties"]["Name"] for p in k.find_resources("AWS::SSM::Parameter").values()}
    assert "/clasp/test/knowledge_base_id" in params


def test_events_stack_routes_lifecycle_events(templates):
    e = templates["events"]
    e.has_resource_properties("AWS::Events::EventBus", {"Name": "clasp-test"})
    e.resource_count_is("AWS::Events::Archive", 1)
    e.resource_count_is("AWS::Events::Rule", 2)
    e.has_resource_properties("AWS::Logs::LogGroup", {"LogGroupName": "/clasp/test/events"})
    e.resource_count_is("AWS::SNS::Topic", 1)
    rules = [r["Properties"] for r in e.find_resources("AWS::Events::Rule").values()]
    delivery = next(r for r in rules if "detail-type" in r["EventPattern"])
    from services.common.events import DELIVERY_EVENT_TYPES

    assert set(delivery["EventPattern"]["detail-type"]) == set(DELIVERY_EVENT_TYPES)
    assert all(r["EventPattern"]["source"] == ["clasp.order"] for r in rules)


def test_publishers_and_retrievers_are_the_right_roles(templates):
    wf = templates["workflow"]
    for role in ("WorkflowTasksServiceRole", "ToolsServiceRole"):
        assert _allows(_policies_for_role(wf, role), "events:PutEvents", "clasp-test"), f"{role} cannot publish events"
    for role in ("ExecutorServiceRole", "AgentSafeServiceRole"):
        assert not _allows(_policies_for_role(wf, role), "events:PutEvents", ""), f"{role} should not publish events"
    assert _allows(_policies_for_role(wf, "ToolsServiceRole"), "bedrock:Retrieve", "knowledge-base")
    assert not _allows(_policies_for_role(wf, "ExecutorServiceRole"), "bedrock:Retrieve", "")
