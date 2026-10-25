"""AgentCore Runtime entrypoint (also `make agent-local` → http://localhost:8080/invocations).
Payload: the dict built by services/ingress/dispatch.build_agent_payload."""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from .agent import handle_turn

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict, context=None) -> dict:
    if not isinstance(payload, dict) or not payload.get("actor_sub"):
        return {"text": "", "error": "payload must include actor_sub (set by ingress)"}
    payload.setdefault("session_id", getattr(context, "session_id", None) or payload["actor_sub"])
    return handle_turn(payload)


if __name__ == "__main__":
    app.run()
