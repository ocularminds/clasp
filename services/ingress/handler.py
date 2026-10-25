"""SNS → one ingress Lambda. Every channel's inbound topic subscribes this function."""
from __future__ import annotations

from .dispatch import handle_inbound
from .normalize import parse_sns_record


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        for inbound in parse_sns_record(record):
            if not inbound.sender:
                continue
            results.append(handle_inbound(inbound))
    return {"handled": len(results), "results": results}
