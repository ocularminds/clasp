"""Lambda behind every Step Functions task state.
Wait states: {"step":"register_token","order_id":..,"phase":..,"token":"<$$.Task.Token>"}
Work states: {"step":"<name>","order_id":..,"last":{...previous callback output...}}"""
from __future__ import annotations

from . import tasks


def lambda_handler(event, context):
    """Raises tasks.DecionisUnavailable for the state machine's retry policy (its errorType is the class name)."""
    step = event["step"]
    order_id = event["order_id"]
    if step == "register_token":
        return tasks.register_token(order_id, event["phase"], event["token"], event.get("correlation_id", ""))
    fn = tasks.STEPS.get(step)
    if fn is None:
        raise ValueError(f"unknown step {step}")
    return fn(order_id, event.get("last") or {})
