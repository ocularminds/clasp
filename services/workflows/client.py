"""Workflow drivers. The order state machine is Step Functions when deployed; the same
transitions run in-process for offline simulation; a recorder stands in for tests.

The executor calls `advance_after(...)` once money has moved; the driver completes
whichever task token the order is waiting on."""
from __future__ import annotations

import json
from typing import Any, Protocol

from ..common.config import settings
from ..common.log import Log

log = Log("workflow")

# Which wait the order is parked at after each money action, and what the callback says.
_AFTER: dict[str, tuple[str, str]] = {
    "hold": ("payment", "paid"),
    "release": ("delivery", "released"),
    "refund": ("dispute", "resolved"),
    "credit": ("dispute", "resolved"),
    "replacement": ("dispute", "resolved"),
}


def callback_for(action: str, order_before: dict, result: dict) -> tuple[str | None, dict]:
    """Return (task_token, output) for the wait this action completes, or (None, {})."""
    if action not in _AFTER:
        return None, {}
    phase, outcome = _AFTER[action]
    if action in {"refund", "credit", "replacement"} and order_before.get("status") == "ESCALATED":
        phase = "approval"
    token = (order_before.get("tokens") or {}).get(phase)
    output = {"outcome": outcome, "order_id": order_before["order_id"], **result}
    return token, output


class WorkflowDriver(Protocol):
    def start_order(self, order: dict) -> str | None: ...
    def task_success(self, token: str, output: dict) -> None: ...
    def task_failure(self, token: str, error: str, cause: str) -> None: ...
    def advance_after(self, action: str, order_before: dict, order_after: dict, result: dict) -> None: ...


class _Base:
    def advance_after(self, action: str, order_before: dict, order_after: dict, result: dict) -> None:
        token, output = callback_for(action, order_before, result)
        if token:
            self.task_success(token, output)  # type: ignore[attr-defined]
        else:
            log.debug("workflow.no_token", action=action, order_id=order_before.get("order_id"))


class StepFunctionsDriver(_Base):
    def __init__(self, state_machine_arn: str | None = None, sfn=None):
        from ..common.aws import client

        self.arn = state_machine_arn or settings.state_machine_arn
        self._sfn = sfn or client("stepfunctions")

    def start_order(self, order: dict) -> str | None:
        r = self._sfn.start_execution(
            stateMachineArn=self.arn, name=order["order_id"],
            input=json.dumps({"order_id": order["order_id"], "correlation_id": order.get("correlation_id", "")}),
        )
        return r["executionArn"]

    def task_success(self, token: str, output: dict) -> None:
        self._sfn.send_task_success(taskToken=token, output=json.dumps(output, default=str))

    def task_failure(self, token: str, error: str, cause: str) -> None:
        self._sfn.send_task_failure(taskToken=token, error=error[:256], cause=cause[:32768])


class MemoryDriver(_Base):
    """Records calls; tests assert on them."""

    def __init__(self):
        self.started: list[dict] = []
        self.successes: list[tuple[str, dict]] = []
        self.failures: list[tuple[str, str, str]] = []

    def start_order(self, order: dict) -> str | None:
        self.started.append(order)
        return f"arn:aws:states:local:000000000000:execution:clasp:{order['order_id']}"

    def task_success(self, token: str, output: dict) -> None:
        self.successes.append((token, output))

    def task_failure(self, token: str, error: str, cause: str) -> None:
        self.failures.append((token, error, cause))


_driver: Any = None


def get_driver():
    global _driver
    if _driver is None:
        mode = settings.workflow_mode
        if mode == "stepfunctions":
            _driver = StepFunctionsDriver()
        elif mode == "memory":
            _driver = MemoryDriver()
        else:
            from .local_runner import LocalDriver

            _driver = LocalDriver()
    return _driver


def set_driver(driver) -> None:
    global _driver
    _driver = driver
