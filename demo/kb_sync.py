#!/usr/bin/env python
"""Sync the policy documents into the Bedrock Knowledge Base.

    make kb-sync                 # render, upload, ingest, wait, smoke-retrieve
    make kb-sync ARGS="--render-only"   # just regenerate services/agentsafe/knowledge/compensation-policy.md

Needs KNOWLEDGE_BASE_ID, KNOWLEDGE_DATA_SOURCE_ID and POLICY_DOCS_BUCKET (written to .env by `make deploy`)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.agentsafe import policy_doc  # noqa: E402
from services.common.config import env  # noqa: E402


def render_to_disk() -> Path:
    policy_doc.KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    policy_doc.POLICY_DOC.write_text(policy_doc.render())
    print(f"rendered {policy_doc.POLICY_DOC.relative_to(Path.cwd()) if policy_doc.POLICY_DOC.is_relative_to(Path.cwd()) else policy_doc.POLICY_DOC}")
    return policy_doc.POLICY_DOC


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--timeout", type=int, default=900, help="seconds to wait for the ingestion job")
    args = ap.parse_args()
    render_to_disk()
    if args.render_only:
        return 0

    kb_id, ds_id, bucket = env("KNOWLEDGE_BASE_ID"), env("KNOWLEDGE_DATA_SOURCE_ID"), env("POLICY_DOCS_BUCKET")
    if not (kb_id and ds_id and bucket):
        print("KNOWLEDGE_BASE_ID, KNOWLEDGE_DATA_SOURCE_ID and POLICY_DOCS_BUCKET must be set (run `make deploy` first).")
        return 1
    from services.common.aws import client

    s3 = client("s3")
    for name, text in policy_doc.documents().items():
        s3.put_object(Bucket=bucket, Key=f"policy/{name}", Body=text.encode(), ContentType="text/markdown")
        print(f"uploaded s3://{bucket}/policy/{name} ({len(text)} chars)")

    agent = client("bedrock-agent")
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, description="make kb-sync")["ingestionJob"]
    job_id = job["ingestionJobId"]
    print(f"ingestion job {job_id} started")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id)["ingestionJob"]
        status = job["status"]
        if status in {"COMPLETE", "FAILED", "STOPPED"}:
            break
        time.sleep(5)
    stats = job.get("statistics", {})
    print(f"ingestion {job['status']}: scanned={stats.get('numberOfDocumentsScanned')} indexed={stats.get('numberOfNewDocumentsIndexed')} "
          f"modified={stats.get('numberOfModifiedDocumentsIndexed')} failed={stats.get('numberOfDocumentsFailed')}")
    for reason in job.get("failureReasons", []) or []:
        print("  failure:", reason)
    if job["status"] != "COMPLETE":
        return 2

    from services.tools.knowledge import retrieve

    hits = retrieve("How much can be refunded automatically, and when does a supervisor get involved?", kb_id, k=2)
    for h in hits:
        print(f"\n[{h['score']}] {h['source']}\n{h['text'][:400]}")
    return 0 if hits else 3


if __name__ == "__main__":
    raise SystemExit(main())
