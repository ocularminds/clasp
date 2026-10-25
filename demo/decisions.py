#!/usr/bin/env python
"""Tail the AgentSafe decision log.  make decisions  (adds --follow)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common import clock  # noqa: E402
from services.common.money import fmt  # noqa: E402
from services.common.stores import get_stores  # noqa: E402

COLOUR = {"allow": "32", "deny": "31", "escalate": "33"}


def render(d: dict) -> str:
    ts = time.strftime("%H:%M:%S", time.gmtime(int(str(d["ts"])[:12])))
    amount = fmt(int(d["amount_minor"]), d.get("currency", "USD")) if str(d.get("amount_minor", "")).lstrip("-").isdigit() else str(d.get("amount_minor"))
    return (f"{ts} \033[{COLOUR.get(d['decision'], '0')}m{d['decision']:<8}\033[0m {d['action']:<11} {amount:>10}  "
            f"{d.get('order_id', '-')[-8:]}  actor={str(d.get('actor_sub', ''))[:14]:<14} {d['reason'][:80]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follow", "-f", action="store_true")
    ap.add_argument("--since", type=int, default=3600, help="seconds back (default 1h)")
    args = ap.parse_args()
    stores = get_stores()
    seen: set[str] = set()
    since = clock.now() - args.since
    print(f"{'time':<8} {'decision':<8} {'action':<11} {'amount':>10}  order     actor          reason")
    while True:
        for d in stores.decisions_since(since, limit=200):
            if d["decision_id"] in seen:
                continue
            seen.add(d["decision_id"])
            print(render(d))
        if not args.follow:
            return 0
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
