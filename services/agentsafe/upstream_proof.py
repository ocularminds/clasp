"""The token an upstream serves at /.well-known/agentsafe-upstream so a Decionis hosted gateway in enforcement will
forward to it. It holds no secret: it binds the tenant id, its Decionis organization and the upstream origin.

    v1.<issued>.<base64url(SHA-256("agentsafe-upstream\\nv1\\n" + id + "\\n" + organization + "\\n" + origin + "\\n" + issued)[0:16])>
"""
from __future__ import annotations

import base64
import hashlib
import time
from urllib.parse import urlsplit


def origin_of(url: str) -> str:
    """WHATWG origin: https, lower-case host, no default port, no path."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def token(tenant_id: str, organization: str, origin: str, issued: int | None = None) -> str:
    issued = int(issued if issued is not None else time.time())
    stamp = f"{issued:010d}"
    digest = hashlib.sha256(f"agentsafe-upstream\nv1\n{tenant_id}\n{organization}\n{origin}\n{stamp}".encode()).digest()[:16]
    return f"v1.{stamp}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"
