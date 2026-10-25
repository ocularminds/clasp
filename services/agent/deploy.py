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

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def main() -> int:
    if not shutil.which("agentcore"):
        print("The AgentCore starter toolkit CLI is not installed. Run:\n"
              "  uv pip install --python .venv/bin/python bedrock-agentcore-starter-toolkit\n"
              "then re-run `make agent-deploy`.")
        return 1
    region = _env("AWS_REGION", "us-east-1")
    role = _env("AGENT_EXECUTION_ROLE_ARN")
    configure = ["agentcore", "configure", "--entrypoint", "services/agent/main.py", "--name", "clasp_agent",
                 "--requirements-file", "services/agent/requirements.txt", "--region", region, "--non-interactive"]
    if role:
        configure += ["--execution-role", role]
    # The runtime reads the same env as everything else. Tools go through Gateway in the cloud.
    runtime_env = {k: _env(k) for k in (
        "BEDROCK_MODEL_ID", "BEDROCK_VISION_MODEL_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION", "MEMORY_ID", "GATEWAY_URL",
        "GATEWAY_TOKEN_URL", "GATEWAY_CLIENT_ID", "GATEWAY_CLIENT_SECRET_ARN", "GATEWAY_SCOPE", "STORE_MODE",
        "ORDERS_TABLE", "LEDGER_TABLE", "DECISIONS_TABLE", "NONCES_TABLE", "CHALLENGES_TABLE", "ITEMS_TABLE", "PARTIES_TABLE",
        "MEDIA_BUCKET", "WORKFLOW_MODE", "STATE_MACHINE_ARN", "AGENTSAFE_FUNCTION_ARN", "EXECUTOR_FUNCTION_ARN",
        "NOTIFY_MODE", "WHATSAPP_PHONE_NUMBER_ID", "RCS_POOL_ID", "SMS_ORIGINATION_IDENTITY", "SES_FROM_EMAIL",
        "SUPERVISOR_EMAIL", "API_BASE_URL", "EVIDENCE_MODE", "AWS_REGION") if _env(k)}
    runtime_env.setdefault("AGENT_TOOLS", "gateway" if _env("GATEWAY_URL") else "local")
    launch = ["agentcore", "launch"] + sum((["--env", f"{k}={v}"] for k, v in runtime_env.items()), [])
    for cmd in (configure, launch):
        print("$", " ".join(cmd))
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print(f"command failed ({r.returncode}). Check `agentcore {cmd[1]} --help`; flag names may differ by toolkit version.")
            return r.returncode
    status = subprocess.run(["agentcore", "status"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"arn:aws:bedrock-agentcore:[^\s\"']+runtime/[^\s\"']+", status.stdout + status.stderr)
    if not m:
        print("Deployed, but could not find the runtime ARN in `agentcore status`; paste it into .env as AGENT_RUNTIME_ARN.")
        return 0
    arn = m.group(0)
    env_path = ROOT / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    text = re.sub(r"^AGENT_RUNTIME_ARN=.*$", f"AGENT_RUNTIME_ARN={arn}", text, flags=re.M) if "AGENT_RUNTIME_ARN=" in text \
        else text + f"\nAGENT_RUNTIME_ARN={arn}\n"
    env_path.write_text(text)
    try:
        import boto3

        boto3.client("ssm", region_name=region).put_parameter(Name=f"/clasp/{_env('STAGE', 'dev')}/agent_runtime_arn", Value=arn,
                                                             Type="String", Overwrite=True)
        print("published to SSM; the deployed ingress Lambda picks it up on its next cold start.")
    except Exception as e:  # noqa: BLE001
        print(f"could not publish to SSM ({e}); set AGENT_RUNTIME_ARN in .env and re-run `make deploy`.")
    print(f"AGENT_RUNTIME_ARN={arn} written to .env. Set AGENT_MODE=remote for ingress to call it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
