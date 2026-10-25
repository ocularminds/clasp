"""SNS → one ingress Lambda. Every channel's inbound topic subscribes this function."""
from __future__ import annotations

from .dispatch import handle_inbound
from .normalize import parse_sns_record, record_shape
from ..common.log import Log

log = Log("ingress.sns")


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        messages = parse_sns_record(record)
        log.info("ingress.record", parsed=len(messages), shape=record_shape(record))
        for inbound in messages:
            if not inbound.sender:
                continue
            results.append(handle_inbound(inbound))
    return {"handled": len(results), "results": results}
