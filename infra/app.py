#!/usr/bin/env python
"""Clasp CDK app: one stack per concern. `make deploy` deploys all and writes outputs to .env."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aws_cdk as cdk  # noqa: E402

from infra.common import load_env, stage  # noqa: E402
from infra.stacks.agent_stack import AgentStack  # noqa: E402
from infra.stacks.api_stack import ApiStack  # noqa: E402
from infra.stacks.data_stack import DataStack  # noqa: E402
from infra.stacks.events_stack import EventsStack  # noqa: E402
from infra.stacks.knowledge_stack import KnowledgeStack  # noqa: E402
from infra.stacks.messaging_stack import MessagingStack  # noqa: E402
from infra.stacks.workflow_stack import WorkflowStack  # noqa: E402


def build(app: cdk.App | None = None, env: cdk.Environment | None = None) -> dict[str, cdk.Stack]:
    load_env()
    app = app or cdk.App()
    env = env or cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=os.environ.get("CDK_DEFAULT_REGION", os.environ.get("AWS_REGION", "us-east-1")))
    prefix = f"Clasp{stage().capitalize()}"
    data = DataStack(app, f"{prefix}Data", env=env, description="Clasp: tables, media bucket, signing key, Cognito, secrets")
    wf = WorkflowStack(app, f"{prefix}Workflow", data, env=env, description="Clasp: AgentSafe, executor, tools, order state machine")
    api = ApiStack(app, f"{prefix}Api", data, wf, env=env, description="Clasp: checkout, PSP webhook, approval links")
    msg = MessagingStack(app, f"{prefix}Messaging", data, env=env, description="Clasp: inbound SNS topics, ingress, SES identity")
    agent = AgentStack(app, f"{prefix}Agent", data, wf, env=env, description="Clasp: Guardrail, AgentCore Memory + Gateway, runtime role")
    knowledge = KnowledgeStack(app, f"{prefix}Knowledge", env=env, description="Clasp: Bedrock Knowledge Base (S3 Vectors) holding the compensation policy")
    evts = EventsStack(app, f"{prefix}Events", env=env, description="Clasp: EventBridge bus for order lifecycle events, archive, audit log, delivery fan-out")
    for s in (data, wf, api, msg, agent, knowledge, evts):
        cdk.Tags.of(s).add("project", "clasp")
    return {"data": data, "workflow": wf, "api": api, "messaging": msg, "agent": agent, "knowledge": knowledge, "events": evts}


if __name__ == "__main__":
    application = cdk.App()
    build(application)
    application.synth()
