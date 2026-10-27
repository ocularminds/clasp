#!/usr/bin/env python
"""Decionis AgentSafe hosted gateway helpers. Read-only: neither command creates a decision or a dossier.

    make upstream-proof TENANT=clasp ORG=<workspace organization uuid>
        prints the token Clasp's API must serve at /.well-known/agentsafe-upstream; put it in .env as
        AGENTSAFE_UPSTREAM_PROOF and `make deploy`

    make gateway-check
        1. GET  {AGENTSAFE_URL}/_agentsafe/readyz                         mode and authority (no key needed)
        2. GET  {AGENTSAFE_URL}/.well-known/agentsafe-upstream + tenant key  a GET is never evaluated: it proves the
           key is accepted, the upstream is reachable through the gateway and the origin proof is served."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.agentsafe.upstream_proof import origin_of, token  # noqa: E402
from services.common.config import settings  # noqa: E402


def proof(tenant: str, org: str) -> int:
    base = settings.api_base_url
    if not base.startswith("https://"):
        raise SystemExit(f"API_BASE_URL must be the deployed https API, not {base!r}")
    origin = origin_of(base)
    print(f"# tenant {tenant}, organization {org}, origin {origin}")
    print(token(tenant, org, origin))
    return 0


def check() -> int:
    import requests

    url = (settings.agentsafe_url or "").rstrip("/")
    if not url:
        raise SystemExit("AGENTSAFE_URL is not set")
    r = requests.get(f"{url}/_agentsafe/readyz", timeout=10)
    ready = r.json() if r.ok else {}
    print(f"readyz: HTTP {r.status_code} mode={ready.get('mode')} authority={ready.get('authority')} ready={ready.get('ready')}")
    configured = settings.agentsafe_gateway_mode
    if ready.get("mode") and ready["mode"].lower() != configured:
        print(f"  ! the gateway reports {ready['mode']} but AGENTSAFE_GATEWAY_MODE is {configured}")
    key = settings.agentsafe_tenant_key
    if not key:
        print("tenant key: missing (AGENTSAFE_TENANT_KEY or AGENTSAFE_TENANT_KEY_SECRET_ARN)")
        return 1
    r = requests.get(f"{url}/.well-known/agentsafe-upstream", headers={"AgentSafe-Tenant-Key": key}, timeout=20)
    execution = r.headers.get("agentsafe-execution")
    print(f"relay:  HTTP {r.status_code} agentsafe-execution={execution} proof-header={r.headers.get('agentsafe-upstream-proof')}")
    if r.status_code == 401:
        print("  ! the gateway rejected the tenant key")
    elif r.status_code == 503:
        print(f"  ! {r.text[:200]}")
    elif r.ok:
        served = {line.strip() for line in r.text.splitlines() if line.strip().startswith("v1.")}
        expected = {line.strip() for line in settings.agentsafe_upstream_proof.splitlines() if line.strip()}
        print("  proof served through the gateway" + (" and matches .env" if expected and served & expected else ""))
    return 0 if r.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("proof")
    p.add_argument("--tenant", required=True)
    p.add_argument("--organization", required=True)
    sub.add_parser("check")
    args = ap.parse_args()
    return proof(args.tenant, args.organization) if args.cmd == "proof" else check()


if __name__ == "__main__":
    raise SystemExit(main())
