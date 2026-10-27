"""In-process stand-in for the Step Functions state machine (WORKFLOW_MODE=local).
Mirrors infra/stacks/workflow_stack.py state for state so `simulate.py` and the e2e test
run the whole order with no AWS account. Task tokens are stored on the order, which makes
the runner restart-safe across CLI invocations."""
from __future__ import annotations

from ..common.ids import new_id
from ..common.log import Log
from ..common.stores import get_stores
from . import tasks
from .client import _Base

log = Log("workflow.local")


class LocalDriver(_Base):
    def _wait(self, order_id: str, phase: str) -> str:
        token = new_id("tok")
        tasks.register_token(order_id, phase, token)
        return token

    # -- API ----------------------------------------------------------------
    def start_order(self, order: dict) -> str | None:
        self._wait(order["order_id"], "payment")
        return f"local:{order['order_id']}"

    def task_success(self, token: str, output: dict) -> None:
        found = get_stores().find_order_by_token(token)
        if not found:
            log.warn("workflow.unknown_token", token=token)
            return
        order, phase = found
        tasks.clear_token(order["order_id"], phase)
        self._continue(order["order_id"], phase, output or {})

    def task_failure(self, token: str, error: str, cause: str) -> None:
        found = get_stores().find_order_by_token(token)
        if not found:
            log.warn("workflow.unknown_token", token=token)
            return
        order, phase = found
        tasks.clear_token(order["order_id"], phase)
        if phase == "approval" and error in {"ApprovalRejected", "ApprovalRejectedFinal", "States.Timeout"}:
            self._approval_failed(order, error, cause)
        else:
            log.error("workflow.failed", order_id=order["order_id"], phase=phase, error=error, cause=cause)

    def timeout(self, order_id: str, phase: str) -> None:
        """Stand-in for a Step Functions wait timing out (tests, simulate)."""
        token = ((get_stores().get_order(order_id) or {}).get("tokens") or {}).get(phase)
        if token:
            self.task_failure(token, "States.Timeout", f"{phase} wait timed out")

    def _approval_failed(self, order: dict, error: str, cause: str) -> None:
        # AwaitSellerApproval catches ApprovalRejected / States.Timeout → NextApprovalStage → AwaitSupervisorApproval;
        # anything at the supervisor stage (or ApprovalRejectedFinal) → MarkDisputedAfterRejection → AwaitResolution.
        oid = order["order_id"]
        at_seller = (order.get("escalation") or {}).get("stage") == "seller"
        if at_seller and error in {"ApprovalRejected", "States.Timeout"}:
            tasks.next_approval_stage(oid, {"Error": error, "Cause": cause})
            self._wait(oid, "approval")
        else:
            tasks.mark_disputed(oid, {"after_rejection": True})
            self._wait(oid, "dispute")

    # -- the state machine, state for state ------------------------------------
    def _continue(self, order_id: str, phase: str, out: dict) -> None:
        if phase == "payment":                       # PaymentPending -> FundsHeld
            self._ship(order_id, out)
        elif phase == "dispatch":
            tasks.mark_dispatched(order_id, out)      # MarkDispatched
            tasks.send_delivery_code(order_id, out)   # SendDeliveryCode
            self._wait(order_id, "delivery")         # AwaitDelivery
        elif phase == "delivery":                    # DeliveryOutcome (Choice)
            if out.get("outcome") == "released":
                tasks.send_receipts(order_id, out)    # SendReceipts -> Released
            else:
                tasks.mark_disputed(order_id, out)    # MarkDisputed
                self._wait(order_id, "dispute")      # AwaitResolution
        elif phase == "dispute":                     # DisputeOutcome (Choice)
            if out.get("outcome") == "resolved":
                self._resolved(order_id, out)
            else:
                tasks.mark_escalated(order_id, out)      # MarkEscalated
                tasks.send_approval_email(order_id, out) # SendApprovalEmail (seller first)
                self._wait(order_id, "approval")         # AwaitApproval
        elif phase == "approval":
            tasks.notify_outcome(order_id, out)          # NotifyOutcome
            self._resolved(order_id, out)

    def _ship(self, order_id: str, out: dict) -> None:
        tasks.book_courier(order_id, out)            # BookCourier
        self._wait(order_id, "dispatch")             # AwaitDispatch (courier pickup scan)

    def _resolved(self, order_id: str, out: dict) -> None:
        tasks.settle_remainder(order_id, out)        # SettleRemainder
        tasks.send_evidence_pack(order_id, out)      # SendEvidencePack
        if out.get("status") == "RESOLVED_REPLACEMENT":  # AfterResolution (Choice)
            self._ship(order_id, out)                # the replacement ships; its delivery releases the payout
