#!/usr/bin/env python
"""Local stand-in for the HTTP API (hosted checkout, PSP webhook, courier pickup page + webhook, approvals, AgentSafe /decide).
Builds API Gateway v2-shaped events and calls the same Lambda handlers that run in AWS.

    make api-local   → http://localhost:8080"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("STORE_MODE", "local")
os.environ.setdefault("WORKFLOW_MODE", "local")
os.environ.setdefault("NOTIFY_MODE", "console")

from services.agentsafe import handler as agentsafe_h  # noqa: E402
from services.approvals import handler as approvals_h  # noqa: E402
from services.courier_mock import handler as courier_h  # noqa: E402
from services.psp_mock import handler as psp_h  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _event(self, method: str) -> dict:
        u = urlparse(self.path)
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        path_params = {}
        if u.path.startswith("/pay/"):
            path_params["checkout_id"] = u.path.rsplit("/", 1)[-1]
        return {"version": "2.0", "rawPath": u.path, "rawQueryString": u.query, "routeKey": f"{method} {u.path}",
                "headers": {k.lower(): v for k, v in self.headers.items()}, "queryStringParameters": params,
                "pathParameters": path_params, "body": body, "isBase64Encoded": False,
                "requestContext": {"http": {"method": method, "path": u.path}}}

    def _route(self, method: str) -> None:
        ev = self._event(method)
        path = ev["rawPath"]
        if path.startswith("/pay/") or path.startswith("/webhooks/psp"):
            out = psp_h.lambda_handler(ev, None)
        elif path.startswith("/courier/") or path.startswith("/webhooks/courier"):
            out = courier_h.lambda_handler(ev, None)
        elif path.startswith("/approvals"):
            out = approvals_h.lambda_handler(ev, None)
        elif path in {"/decide", "/approval-links"}:
            out = agentsafe_h.lambda_handler(ev, None)
        elif path == "/healthz":
            out = {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"ok": True})}
        else:
            out = {"statusCode": 404, "headers": {"content-type": "text/plain"}, "body": "not found"}
        self.send_response(out.get("statusCode", 200))
        for k, v in (out.get("headers") or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(out.get("body", "").encode())

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def log_message(self, fmt, *args):  # quieter
        print(f"  http {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"local API on http://localhost:{port}  (GET /pay/<checkout_id>, POST /webhooks/psp, GET /courier/<order>/<shipment>, "
          "GET|POST /approvals, POST /decide)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
