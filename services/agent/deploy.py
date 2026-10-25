#!/usr/bin/env python
"""Deploy the agent to AgentCore Runtime with the starter toolkit, then write AGENT_RUNTIME_ARN to .env.

    make agent-deploy

Requires: `uv pip install bedrock-agentcore-starter-toolkit`, AWS credentials, and `make deploy` done first
(the agent's execution role, Gateway and Memory come from the CDK outputs in .env)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENT_NAME = "clasp_agent"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def toolkit_cli() -> str | None:
    """`make` uses the venv Python without activating its bin directory."""
    installed = Path(sys.executable).parent / "agentcore"
    return str(installed) if installed.is_file() else shutil.which("agentcore")


def stage_source(root: Path = ROOT) -> Path:
    """Package the complete services tree without uploading the checkout or .env."""
    staged = root / "build" / "agentcore"
    staged.mkdir(parents=True, exist_ok=True)
    if (staged / "services").exists():
        shutil.rmtree(staged / "services")
    shutil.copytree(root / "services", staged / "services",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".DS_Store"))
    shutil.copyfile(root / "services" / "agent" / "requirements.txt", staged / "requirements.txt")
    (staged / "agent_entrypoint.py").write_text(
        '"""Packaged entrypoint for the Clasp AgentCore HTTP runtime."""\n'
        'from services.agent.main import app\n\n'
        'if __name__ == "__main__":\n'
        '    app.run()\n'
    )
    return staged


def runtime_arn(staged: Path) -> str:
    """Read machine-readable toolkit state; rich CLI output can wrap the ARN."""
    config = yaml.safe_load((staged / ".bedrock_agentcore.yaml").read_text())
    arn = config["agents"][AGENT_NAME]["bedrock_agentcore"].get("agent_arn") or ""
    if not re.fullmatch(r"arn:aws:bedrock-agentcore:[a-z0-9-]+:\d{12}:runtime/[A-Za-z0-9_-]+", arn):
        raise ValueError("The toolkit did not save a valid runtime ARN; deployment is incomplete.")
    return arn


def configure_runtime_logs(arn: str, key_arn: str) -> None:
    """The toolkit creates runtime logs separately from CDK; apply its managed key and retention."""
    import boto3

    if not arn or not key_arn:
        raise ValueError("Runtime ARN and AGENT_LOG_KMS_KEY_ARN are required to configure runtime logs.")
    region = arn.split(":")[3]
    group = f"/aws/bedrock-agentcore/runtimes/{arn.rsplit('/', 1)[-1]}-DEFAULT"
    logs = boto3.client("logs", region_name=region)
    try:
        logs.create_log_group(logGroupName=group, kmsKeyId=key_arn)
    except logs.exceptions.ResourceAlreadyExistsException:
        logs.associate_kms_key(logGroupName=group, kmsKeyId=key_arn)
    logs.put_retention_policy(logGroupName=group, retentionInDays=14)
    print("Runtime logs use the CDK-managed KMS key and a 14-day retention limit.")


def main() -> int:
    cli = toolkit_cli()
    if not cli:
        print("The AgentCore starter toolkit CLI is not installed. Run:\n"
              "  uv pip install --python .venv/bin/python bedrock-agentcore-starter-toolkit\n"
              "then re-run `make agent-deploy`.")
        return 1
    region = _env("AWS_REGION", "us-east-1")
    role = _env("AGENT_EXECUTION_ROLE_ARN")
    if not role or not _env("GATEWAY_URL") or not _env("MEMORY_ID") or not _env("AGENT_LOG_KMS_KEY_ARN"):
        print("Run `make deploy` first: the runtime needs its execution role, Gateway, Memory and log encryption key.")
        return 1
    staged = stage_source()
    configure = [cli, "configure", "--entrypoint", "agent_entrypoint.py", "--name", AGENT_NAME,
                 "--requirements-file", "requirements.txt", "--region", region, "--non-interactive",
                 "--execution-role", role, "--protocol", "HTTP", "--deployment-type", "container",
                 "--disable-memory"]  # CDK manages the memory resource; do not create a second one.
    # The runtime reads the same env as everything else. Tools go through Gateway in the cloud.
    runtime_env = {k: _env(k) for k in (
        "BEDROCK_MODEL_ID", "BEDROCK_VISION_MODEL_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION", "MEMORY_ID", "GATEWAY_URL",
        "GATEWAY_TOKEN_URL", "GATEWAY_CLIENT_ID", "GATEWAY_CLIENT_SECRET_ARN", "GATEWAY_SCOPE", "STORE_MODE",
        "ORDERS_TABLE", "LEDGER_TABLE", "DECISIONS_TABLE", "NONCES_TABLE", "CHALLENGES_TABLE", "ITEMS_TABLE", "PARTIES_TABLE",
        "MEDIA_BUCKET", "WORKFLOW_MODE", "STATE_MACHINE_ARN", "AGENTSAFE_FUNCTION_ARN", "EXECUTOR_FUNCTION_ARN",
        "NOTIFY_MODE", "WHATSAPP_PHONE_NUMBER_ID", "RCS_POOL_ID", "SMS_ORIGINATION_IDENTITY", "SES_FROM_EMAIL",
        "SUPERVISOR_EMAIL", "API_BASE_URL", "EVIDENCE_MODE", "AWS_REGION") if _env(k)}
    runtime_env["AGENT_TOOLS"] = "gateway"
    launch = [cli, "launch", "--agent", AGENT_NAME, "--auto-update-on-conflict"] + sum(
        (["--env", f"{k}={v}"] for k, v in runtime_env.items()), [])
    for cmd in (configure, launch):
        print(f"Running AgentCore {cmd[1]} from {staged}", flush=True)
        r = subprocess.run(cmd, cwd=staged)
        if r.returncode != 0:
            print(f"command failed ({r.returncode}). Check `agentcore {cmd[1]} --help`; flag names may differ by toolkit version.")
            return r.returncode
    arn = runtime_arn(staged)
    env_path = ROOT / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    text = re.sub(r"^AGENT_RUNTIME_ARN=.*$", f"AGENT_RUNTIME_ARN={arn}", text, flags=re.M) if "AGENT_RUNTIME_ARN=" in text \
        else text + f"\nAGENT_RUNTIME_ARN={arn}\n"
    env_path.write_text(text)
    import boto3

    boto3.client("ssm", region_name=region).put_parameter(Name=f"/clasp/{_env('STAGE', 'dev')}/agent_runtime_arn", Value=arn,
                                                         Type="String", Overwrite=True)
    configure_runtime_logs(arn, _env("AGENT_LOG_KMS_KEY_ARN"))
    print("published to SSM; the deployed ingress Lambda picks it up on its next cold start.")
    print(f"AGENT_RUNTIME_ARN={arn} written to .env. Set AGENT_MODE=remote for ingress to call it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
