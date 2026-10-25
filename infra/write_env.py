#!/usr/bin/env python
"""Merge `cdk deploy --outputs-file` results into .env (keys = output logical ids in UPPER_SNAKE)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEPLOYED_MODES = {"STORE_MODE": "dynamodb", "WORKFLOW_MODE": "stepfunctions", "NOTIFY_MODE": "aws",
                  "AGENT_MODE": "remote", "EVIDENCE_MODE": "bedrock"}


def env_key(logical: str) -> str:
    logical = logical[3:] if logical.startswith("Out") else logical
    return re.sub(r"(?<!^)(?=[A-Z])", "_", logical).upper()


def main(outputs_path: str, env_path: str) -> int:
    outputs = json.loads(Path(outputs_path).read_text())
    values: dict[str, str] = {}
    for _stack, outs in outputs.items():
        for k, v in outs.items():
            if k.startswith("Out"):
                values[env_key(k)] = v
    values.update(DEPLOYED_MODES)
    p = Path(env_path)
    lines = p.read_text().splitlines() if p.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in values:
                lines[i] = f"{k}={values[k]}"
                seen.add(k)
    missing = [k for k in values if k not in seen]
    if missing:
        lines.append("")
        lines.append("# --- written by infra/write_env.py from CDK outputs ---")
        lines.extend(f"{k}={values[k]}" for k in missing)
    p.write_text("\n".join(lines) + "\n")
    print(f"updated {env_path}: {', '.join(sorted(values))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "infra/cdk-outputs.json", sys.argv[2] if len(sys.argv) > 2 else ".env"))
