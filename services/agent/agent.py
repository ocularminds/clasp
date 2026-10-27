"""The Clasp agent: Strands Agent on a multimodal Claude model (Bedrock), optional Guardrail,
tools local or via AgentCore Gateway, memory keyed by the buyer's sub, and the actor-binding hook.

handle_turn(payload) -> {"text", "buttons", "tools_called", "order_id"}"""
from __future__ import annotations

from typing import Any

from strands import Agent
from strands.models import BedrockModel

from ..common.config import env, settings
from ..common.log import Log
from .hooks import ActorBindingHook
from .memory import session_manager
from .prompt import SYSTEM_PROMPT
from .tools import gateway_client, local_tools

log = Log("agent")


def build_model(model=None):
    if model is not None:
        return model
    kwargs: dict[str, Any] = {"model_id": settings.model_id, "region_name": settings.region, "temperature": 0.2,
                              "max_tokens": 1024, "streaming": False}
    if settings.guardrail_id:  # sensitive-information masking on input and output (not the injection defense)
        kwargs.update(guardrail_id=settings.guardrail_id, guardrail_version=settings.guardrail_version,
                      guardrail_redact_input=True, guardrail_redact_output=True, guardrail_trace="enabled")
    return BedrockModel(**kwargs)


def turn_prompt(payload: dict) -> str:
    lines = [f"[channel={payload.get('channel')} role={payload.get('actor_role', 'buyer')}"
             + (f" name={payload['actor_name']}" if payload.get("actor_name") else "") + "]"]
    notices = payload.get("notices") or []
    if notices:
        lines.append("(Since their last message, Clasp sent them, outside this chat: "
                     + " | ".join(f"order {n.get('order')} by {str(n.get('channel', '')).upper()}: {n.get('memo')}" for n in notices) + ")")
    if payload.get("text"):
        lines.append(payload["text"])
    if payload.get("button"):
        b = payload["button"]
        lines.append(f"(the person tapped the button id={b.get('id')} title={b.get('title')!r})")
    if payload.get("media_keys"):
        lines.append("(attached media_keys: " + ", ".join(payload["media_keys"]) + ")")
    return "\n".join(lines)


def _text_of(result) -> str:
    msg = getattr(result, "message", None) or {}
    return "".join(c.get("text", "") for c in (msg.get("content") or []) if isinstance(c, dict)).strip()


def handle_turn(payload: dict, model=None, tools=None, use_session: bool = True) -> dict:
    actor = payload["actor_sub"]
    corr = payload.get("correlation_id", "")
    hook = ActorBindingHook(actor, corr)
    tlog = log.bind(correlation_id=corr or None, actor_sub=actor, channel=payload.get("channel"))
    mcp = None
    if tools is None:
        if env("AGENT_TOOLS", "local") == "gateway":
            mcp = gateway_client()
            mcp.__enter__()
            tools = mcp.list_tools_sync()
        else:
            tools = local_tools()
    try:
        agent = Agent(
            model=build_model(model), system_prompt=SYSTEM_PROMPT, tools=tools, hooks=[hook],
            session_manager=session_manager(payload.get("session_id") or actor, actor) if use_session else None,
            callback_handler=None, agent_id="clasp",
        )
        result = agent(turn_prompt(payload))
        text = _text_of(result)
    finally:
        if mcp is not None:
            mcp.__exit__(None, None, None)
    tlog.info("agent.turn", tools_called=hook.tools_called, stop_reason=getattr(result, "stop_reason", None), text=text[:300])
    return {"text": text, "buttons": hook.buttons, "tools_called": hook.tools_called, "order_id": hook.order_id}
