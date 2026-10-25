"""Strands tools for the agent.

* local:   PythonAgentTool wrappers built from the shared TOOL_SCHEMAS, calling services.tools.impl
           in-process (simulate, tests, `agentcore dev`).
* gateway: the same tools served by AgentCore Gateway (Lambda target) over MCP, discovered at runtime.
"""
from __future__ import annotations

import json
from typing import Any

from strands.tools.tools import PythonAgentTool

from ..common.config import env, settings
from ..tools.schema import TOOL_SCHEMAS


def _make_local_tool(schema: dict) -> PythonAgentTool:
    name = schema["name"]

    def run(tool_use: dict, **_: Any) -> dict:
        from ..tools.impl import call

        args = dict(tool_use.get("input") or {})
        out = call(name, args)
        status = "error" if isinstance(out, dict) and out.get("error") else "success"
        return {"toolUseId": tool_use["toolUseId"], "status": status, "content": [{"json": _jsonable(out)}]}

    spec = {"name": name, "description": schema["description"], "inputSchema": {"json": schema["inputSchema"]}}
    return PythonAgentTool(name, spec, run)


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


def local_tools() -> list[PythonAgentTool]:
    return [_make_local_tool(s) for s in TOOL_SCHEMAS]


def gateway_client():
    """MCP client for the AgentCore Gateway. Inbound auth is a Cognito client-credentials bearer token."""
    from strands.tools.mcp import MCPClient

    url = settings.gateway_url
    if not url:
        raise RuntimeError("GATEWAY_URL is not set")
    return MCPClient(url=url, headers={"Authorization": f"Bearer {_gateway_token()}"})


def _gateway_token() -> str:
    import requests

    token_url = env("GATEWAY_TOKEN_URL")
    client_id = env("GATEWAY_CLIENT_ID")
    secret = env("GATEWAY_CLIENT_SECRET")
    if not secret and env("GATEWAY_CLIENT_SECRET_ARN"):
        from ..common.aws import client

        secret = client("secretsmanager").get_secret_value(SecretId=env("GATEWAY_CLIENT_SECRET_ARN"))["SecretString"]
    if not (token_url and client_id and secret):
        raise RuntimeError("GATEWAY_TOKEN_URL / GATEWAY_CLIENT_ID / GATEWAY_CLIENT_SECRET(_ARN) are required for gateway tools")
    r = requests.post(token_url, data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
                                       **({"scope": env("GATEWAY_SCOPE")} if env("GATEWAY_SCOPE") else {})}, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]
