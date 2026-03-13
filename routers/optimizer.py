from fastapi import APIRouter
from pydantic import BaseModel
from core.query_optimizer import QueryOptimizer
import json
import asyncio

router = APIRouter(prefix="/optimizer", tags=["optimizer"])

class OptimizeRequest(BaseModel):
    query: str


def _get_user_id():
    """Current user for episodic scope. None if unavailable."""
    try:
        from memory.user_id import get_user_id
        return get_user_id()
    except Exception:
        return None


@router.post("/preview")
async def preview_optimization(req: OptimizeRequest):
    """
    Generate an optimized version of the query for preview.
    """
    opt = QueryOptimizer()
    return await opt.optimize_query(req.query)


@router.get("/skeletons")
async def get_skeletons(limit: int = 10):
    """
    Retrieve recent session skeletons (recipes).
    Uses Qdrant episodic store when available (Phase B); falls back to local episodic_skeletons/*.json.
    """
    # Prefer Qdrant episodic store (Phase B)
    try:
        from memory.episodic import get_recent_episodes

        user_id = _get_user_id()
        episodes = get_recent_episodes(limit=limit, user_id=user_id, space_id=None)
        if episodes:
            skeletons = []
            for ep in episodes:
                sk_json = ep.get("skeleton_json") or "{}"
                try:
                    sk = json.loads(sk_json) if isinstance(sk_json, str) else sk_json
                except Exception:
                    sk = {}
                sk.setdefault("id", ep.get("id") or ep.get("session_id"))
                skeletons.append(sk)
            return skeletons
    except Exception:
        pass

    # Fallback: local episodic_skeletons (legacy)
    from core.episodic_memory import MEMORY_DIR

    if not MEMORY_DIR.exists():
        return []

    files = sorted(MEMORY_DIR.glob("skeleton_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    skeletons = []
    for f in files[:limit]:
        try:
            content = await asyncio.to_thread(f.read_text)
            data = json.loads(content)
            skeletons.append(data)
        except Exception as e:
            print(f"Failed to load skeleton {f.name}: {e}")
    return skeletons
