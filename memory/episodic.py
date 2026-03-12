"""Episodic memory module: stores and retrieves session skeleton recipes.

Phase B: Reads from Qdrant (arcturus_episodic). Local episodic_skeletons/ is used
only by migration script. If Qdrant is unavailable, returns [].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from shared.state import PROJECT_ROOT

# Directory for legacy migration only (not read at runtime)
MEMORY_DIR = PROJECT_ROOT / "memory" / "episodic_skeletons"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_episodic_store = None


def _get_episodic_store():
    """Lazy init episodic Qdrant store."""
    global _episodic_store
    if _episodic_store is None:
        try:
            from memory.backends.episodic_qdrant_store import EpisodicQdrantStore
            _episodic_store = EpisodicQdrantStore()
        except Exception as e:
            from core.utils import log_error
            log_error(f"Episodic Qdrant store init failed: {e}")
            return None
    return _episodic_store


def search_episodes(
    query: str,
    limit: int = 5,
    user_id: Optional[str] = None,
    space_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Search relevant past episodes by semantic similarity. Phase B: Qdrant with user/space filters."""
    store = _get_episodic_store()
    if not store:
        return []
    try:
        from remme.utils import get_embedding
        emb = get_embedding(query, task_type="search_query")
        results = store.search(emb, limit=limit, user_id=user_id, space_id=space_id)
        return results
    except Exception as e:
        from core.utils import log_error
        log_error(f"search_episodes failed: {e}")
        return []


def get_recent_episodes(
    limit: int = 10,
    user_id: Optional[str] = None,
    space_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return most recent episodes. Phase B: Qdrant with user/space filters."""
    store = _get_episodic_store()
    if not store:
        return []
    try:
        return store.get_recent(limit=limit, user_id=user_id, space_id=space_id)
    except Exception as e:
        from core.utils import log_error
        log_error(f"get_recent_episodes failed: {e}")
        return []
