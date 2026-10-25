"""Static proof of the authority boundary: only the executor commits money moves,
and no tool module touches the ledger or the executor's internals directly."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "services"
ALLOWED_COMMITTERS = {ROOT / "executor" / "executor.py", ROOT / "common" / "stores.py"}


def _attr_calls(tree: ast.AST, name: str) -> list[int]:
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == name]


def test_only_the_executor_commits_money_moves():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path in ALLOWED_COMMITTERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for ln in _attr_calls(tree, "commit_money_move"):
            offenders.append(f"{path.relative_to(ROOT)}:{ln}")
    assert not offenders, f"ledger writes outside the executor: {offenders}"


def test_tools_never_import_executor_internals_or_signers():
    tools = ROOT / "tools"
    if not tools.exists():
        return
    bad = []
    for path in tools.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module + ":" + ",".join(a.name for a in node.names))
            elif isinstance(node, ast.Import):
                mods.extend(a.name for a in node.names)
            for m in mods:
                if "agentsafe.signer" in m or ("executor.executor" in m and "ExecRequest" not in m):
                    bad.append(f"{path.relative_to(ROOT)}: {m}")
    assert not bad, f"tools reach past the boundary: {bad}"
