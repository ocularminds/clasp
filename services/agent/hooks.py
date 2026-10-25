"""The actor-binding hook: for every tool call, the runtime writes `actor_sub` and
`correlation_id` from the inbound turn over whatever the model produced. AgentSafe then checks
that sub against the order owner. A prompt injection can make the model *ask* for anything;
it cannot make the request come from someone else."""
from __future__ import annotations

import json
from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from ..common.log import Log

log = Log("agent.hooks")


class ActorBindingHook(HookProvider):
    def __init__(self, actor_sub: str, correlation_id: str):
        self.actor_sub = actor_sub
        self.correlation_id = correlation_id
        self.tools_called: list[str] = []
        self.buttons: list[dict] | None = None
        self.order_id: str | None = None
        self.last_results: list[dict] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        tool_input = event.tool_use.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        spoofed = tool_input.get("actor_sub") not in (None, "", self.actor_sub)
        tool_input["actor_sub"] = self.actor_sub
        tool_input["correlation_id"] = self.correlation_id
        event.tool_use["input"] = tool_input
        name = event.tool_use.get("name", "")
        self.tools_called.append(name.split("___")[-1])
        log.info("agent.tool_call", correlation_id=self.correlation_id, tool=name, actor_sub=self.actor_sub,
                 spoof_attempt=spoofed or None,
                 args={k: v for k, v in tool_input.items() if k not in {"actor_sub", "correlation_id"}})

    def after(self, event: AfterToolCallEvent) -> None:
        result = getattr(event, "result", None) or {}
        payload: dict | None = None
        for block in (result.get("content") or []):
            if isinstance(block, dict) and "json" in block and isinstance(block["json"], dict):
                payload = block["json"]
            elif isinstance(block, dict) and "text" in block:
                try:
                    cand = json.loads(block["text"])
                    payload = cand if isinstance(cand, dict) else payload
                except (ValueError, TypeError):
                    pass
        if not payload:
            return
        self.last_results.append(payload)
        if isinstance(payload.get("buttons"), list):
            self.buttons = payload["buttons"][:3]
        if payload.get("order_id"):
            self.order_id = payload["order_id"]
