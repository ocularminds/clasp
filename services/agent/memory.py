"""One conversation per buyer across channels. The session is keyed by the Cognito sub:
AgentCore Memory when MEMORY_ID is set, a file-backed session locally."""
from __future__ import annotations

from pathlib import Path

from ..common.config import env, repo_root, settings


def session_manager(session_id: str, actor_id: str):
    if settings.memory_id:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

        cfg = AgentCoreMemoryConfig(
            memory_id=settings.memory_id, session_id=session_id, actor_id=actor_id,
            retrieval_config={f"/summaries/{actor_id}/{session_id}": RetrievalConfig(top_k=3, relevance_score=0.3),
                              f"/facts/{actor_id}": RetrievalConfig(top_k=5, relevance_score=0.3)},
        )
        return AgentCoreMemorySessionManager(agentcore_memory_config=cfg, region_name=settings.region)
    from strands.session.file_session_manager import FileSessionManager

    storage = Path(env("SESSION_DIR") or (repo_root() / "demo" / "state" / "sessions"))
    storage.mkdir(parents=True, exist_ok=True)
    return FileSessionManager(session_id=session_id, storage_dir=str(storage))
