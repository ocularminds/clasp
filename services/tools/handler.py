"""AgentCore Gateway Lambda target. Gateway sends the tool input as the event and the tool
name in context.client_context.custom['bedrockAgentCoreToolName'] ("<target>___<tool>")."""
from __future__ import annotations

from .impl import call


def lambda_handler(event, context):
    name = None
    cc = getattr(context, "client_context", None)
    custom = getattr(cc, "custom", None) or {}
    if isinstance(custom, dict):
        name = custom.get("bedrockAgentCoreToolName")
    if not name and isinstance(event, dict):
        name = event.pop("tool_name", None) or event.pop("name", None)
    if not name:
        return {"error": "tool name missing"}
    name = name.split("___")[-1]
    args = dict(event) if isinstance(event, dict) else {}
    return call(name, args)
