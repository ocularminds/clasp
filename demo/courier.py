#!/usr/bin/env python
"""Play the courier for a recorded demo (COURIER_MODE=manual).

    make courier ORDER=27e389                  # print the pickup page link: open it on a phone and photograph the parcel
    make courier ORDER=27e389 PHOTO=parcel.jpg # or scan the pickup directly with a photo file

The pickup is an HMAC-signed courier event; the seller never has to message anyone."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.stores import get_stores  # noqa: E402
from services.courier_mock import courier  # noqa: E402


def find(code: str) -> dict | None:
    stores = get_stores()
    order = stores.get_order(code)
    if order:
        return order
    for party in stores.list_parties():
        if party.get("role") != "seller" or not party.get("sub"):
            continue
        for o in stores.orders_for_seller(party["sub"]):
            if o["order_id"].endswith(code.lower()):
                return o
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("order", help="order id or its 6-character code")
    ap.add_argument("--photo", help="scan the pickup now with this JPEG")
    args = ap.parse_args()
    order = find(args.order.strip())
    if not order:
        raise SystemExit(f"no order {args.order}")
    shipment = order.get("shipment") or {}
    if shipment.get("status") != "booked":
        raise SystemExit(f"order {order['order_id'][-6:]} has no pickup waiting (shipment: {shipment.get('status', 'none')})")
    if args.photo:
        status, body = courier.scan_pickup(order, Path(args.photo).read_bytes())
        print(status, body)
        return 0 if status == 200 else 1
    print(courier.pickup_url(order["order_id"], shipment["shipment_id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
