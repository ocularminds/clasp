"""explain_policy: quote the marketplace policy. Retrieves from the Bedrock Knowledge Base when
KNOWLEDGE_BASE_ID is set (falling back if retrieval fails), otherwise searches the generated policy
document and FAQ locally. Read-only; it can never change an order or a decision."""
from __future__ import annotations

import re
from typing import Any

from ..agentsafe import policy_doc
from ..common.aws import client
from ..common.config import settings
from ..common.log import Log

log = Log("tools.knowledge")

_STOP = {"the", "a", "an", "is", "are", "was", "my", "me", "i", "to", "of", "for", "in", "on", "it", "do", "does", "can",
         "what", "why", "how", "and", "or", "be", "did", "get", "this", "that", "with", "you", "your", "will", "when"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%$.]+", text.lower()) if w not in _STOP and len(w) > 1}


def local_search(question: str, k: int = 4) -> list[dict]:
    q = _tokens(question)
    scored = []
    for name, text in policy_doc.documents().items():
        for sec in policy_doc.sections(text):
            words = _tokens(sec["title"] + " " + sec["text"])
            overlap = len(q & words)
            title_bonus = 2 * len(q & _tokens(sec["title"]))
            score = overlap + title_bonus
            if score:
                scored.append({"text": f"{sec['title']}\n{sec['text']}"[:1200], "score": round(score / max(len(q), 1), 3), "source": name})
    scored.sort(key=lambda s: s["score"], reverse=True)
    if not scored:  # nothing matched: the overview is the safest answer
        first = policy_doc.sections(policy_doc.render())[0]
        scored = [{"text": f"{first['title']}\n{first['text']}"[:1200], "score": 0.0, "source": "compensation-policy.md"}]
    return scored[:k]


def retrieve(question: str, kb_id: str, k: int = 4) -> list[dict]:
    r = client("bedrock-agent-runtime").retrieve(
        knowledgeBaseId=kb_id, retrievalQuery={"text": question[:1000]},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": k}},
    )
    out = []
    for x in r.get("retrievalResults", []):
        loc = x.get("location") or {}
        out.append({"text": ((x.get("content") or {}).get("text") or "")[:1200], "score": round(float(x.get("score") or 0), 3),
                    "source": (loc.get("s3Location") or {}).get("uri") or loc.get("type") or ""})
    return out


def explain_policy(question: str, actor_sub: str = "", correlation_id: str = "", **_: Any) -> dict:
    from .impl import ToolError

    q = (question or "").strip()
    if not q:
        raise ToolError("question is required")
    version = policy_doc.load_policy().get("version")
    kb = settings.knowledge_base_id
    if kb:
        try:
            passages = retrieve(q, kb)
            if passages:
                log.info("knowledge.retrieved", knowledge_base_id=kb, passages=len(passages), correlation_id=correlation_id or None)
                return {"source": "knowledge_base", "knowledge_base_id": kb, "policy_version": version, "passages": passages,
                        "note": "Quote the limits from these passages. AgentSafe decides each proposal; do not promise outcomes."}
            log.warn("knowledge.empty", knowledge_base_id=kb, correlation_id=correlation_id or None)
        except Exception as e:  # noqa: BLE001
            log.warn("knowledge.retrieve_failed", knowledge_base_id=kb, error=str(e), correlation_id=correlation_id or None)
    return {"source": "local_policy", "policy_version": version, "passages": local_search(q),
            "note": "Quote the limits from these passages. AgentSafe decides each proposal; do not promise outcomes."}
