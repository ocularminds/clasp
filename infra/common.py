"""Shared helpers for the stacks: env reading, the Lambda factory, env-key outputs."""
from __future__ import annotations

import os
import re
from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lam
from aws_cdk import aws_logs as logs
from constructs import Construct

from .bundling import lambda_code

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Read .env so human-provided IDs (WhatsApp phone number id, pool id, SES sender) reach the templates.
    Honours CLASP_SKIP_DOTENV like services.common.config does, so the test suite never inherits a developer's .env."""
    if os.environ.get("CLASP_SKIP_DOTENV"):
        return
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def stage() -> str:
    return env("STAGE", "dev")


def table_name(logical: str) -> str:
    return env(f"{logical.upper()}_TABLE", f"clasp-{logical}")


def output(scope: Stack, env_key: str, value: str, description: str = "") -> CfnOutput:
    """CfnOutput whose logical id maps back to the .env key (infra/write_env.py does the reverse)."""
    logical = "Out" + "".join(part.capitalize() for part in env_key.lower().split("_"))
    return CfnOutput(scope, logical, value=value, description=description or env_key, export_name=f"clasp-{stage()}-{logical}")


def env_key_from_output(logical: str) -> str:
    logical = logical[3:] if logical.startswith("Out") else logical
    return re.sub(r"(?<!^)(?=[A-Z])", "_", logical).upper()


def make_function(scope: Construct, id_: str, handler_module: str, environment: dict[str, str], *,
                  timeout: int = 30, memory: int = 512, role: iam.Role | None = None, description: str = "") -> lam.Function:
    return lam.Function(
        scope, id_,
        function_name=f"clasp-{stage()}-{id_.lower()}",
        runtime=lam.Runtime.PYTHON_3_12,
        architecture=lam.Architecture.X86_64,
        code=lambda_code(scope),
        handler=f"services.{handler_module}.lambda_handler",
        timeout=Duration.seconds(timeout),
        memory_size=memory,
        environment={k: v for k, v in environment.items() if v},
        role=role,
        log_retention=logs.RetentionDays.TWO_WEEKS,
        description=description,
    )


COMMON_ENV = {
    "STORE_MODE": "dynamodb",
    "WORKFLOW_MODE": "stepfunctions",
    "NOTIFY_MODE": env("NOTIFY_MODE_DEPLOYED", "aws"),
    "POLICY_PATH": "services/agentsafe/policy.yaml",
    "META_API_VERSION": env("META_API_VERSION", "v20.0"),
    "LOG_LEVEL": env("LOG_LEVEL", "INFO"),
    "STAGE": stage(),
    "EVENTS_MODE": env("EVENTS_MODE_DEPLOYED", "eventbridge"),
    "EVENT_BUS_NAME": f"clasp-{stage()}",
}
