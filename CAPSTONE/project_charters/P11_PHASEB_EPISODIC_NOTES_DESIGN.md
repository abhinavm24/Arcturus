# Phase B: Episodic + Notes — Design (Locked)

**Status:** Locked. Use this document as the implementation spec.

---

## 1. Episodic Memory

### Storage

| Component | DB | Collection / Node | Purpose |
|-----------|-----|-------------------|---------|
| Episodic skeletons | **Qdrant** | `arcturus_episodic` | Semantic search, space/user scoping |
| Session→Space link | **Neo4j** | Session–IN_SPACE→Space (existing) | Space provenance (no schema change) |

### Qdrant Collection: `arcturus_episodic`

```
dimension: 768
distance: cosine
is_tenant: true
tenant_keyword_field: user_id
indexed_payload_fields: [session_id, space_id, original_query]
```

**Payload schema:**

```json
{
  "user_id": "uuid",
  "space_id": "__global__" | "space-uuid",
  "session_id": "run-uuid",
  "original_query": "str",
  "outcome": "completed|failed|...",
  "skeleton_json": "str",
  "created_at": "ISO8601"
}
```

**Searchable text (embedding source):** `original_query` + condensed node descriptions (e.g., `task_goal`, `instruction` from skeleton nodes).

### Source of `space_id`

- **Runs:** `save_episode` is called from `core/loop.py` and `routers/ide_agent.py`. Session is already created with `space_id` via `get_or_create_session(run_id, space_id)` in runs.
- **Flow:** Caller must pass `space_id` into `save_episode(session_data, space_id)` (or include it in `session_data.graph`). Resolve from Neo4j Session→Space when `space_id` is missing.

### Write path

1. `EpisodicMemory.save_episode(session_data, space_id=None)` — build skeleton, embed searchable text, upsert to Qdrant with `user_id`, `space_id`, `session_id`.
2. `space_id` default: `__global__` when absent.

### Read path

1. `search_episodes(query, limit, user_id, space_id)` — vector search in `arcturus_episodic` with filters.
2. `get_recent_episodes(limit, user_id, space_id)` — scroll/list with filters.

### Backward compatibility

- **Migration:** Run `migrate_episodic_to_qdrant.py` (or integrate into `migrate_all_memories.py`). Reads `memory/episodic_skeletons/*.json`, writes to Qdrant with `user_id` and `space_id` (default `__global__`).
- **No dual read:** After migration, episodic reads come only from Qdrant. Local `episodic_skeletons/` is no longer read at runtime (only used for migration).
- **Fallback:** If Qdrant episodic store is disabled or empty, `search_episodes` / `get_recent_episodes` return `[]`.

---

## 2. Notes

### Storage

- **Qdrant:** `arcturus_rag_chunks` (existing).
- **Payload:** Add `user_id` and `space_id` to RAG chunks (already done in Phase A).
- **Association:** Notes (files in `data/Notes/`) get `space_id` when indexed. Option: `Notes/{space_id}/` for space-specific notes, or metadata mapping file path → space_id.

### Space association

- **Convention:** `data/Notes/__global__/` = global notes; `data/Notes/{space_id}/` = notes in that space.
- **Indexing:** When RAG indexes a note, derive `space_id` from path. Files under `Notes/` (root) → `__global__`.

### Backward compatibility

- **Migration:** Use existing RAG migration with `user_id` + `space_id` (Phase A). Notes under `Notes/` root get `space_id=__global__`.
- **Sync:** Notes are covered by RAG sync (chunks in Qdrant with `user_id`, `space_id`).

---

## 3. Sync Protocol

### New change type: `episodic`

- **EpisodicDelta:** `episodic_id`, `session_id`, `user_id`, `space_id`, `skeleton_json`, `version`, `device_id`, `updated_at`, `deleted`.
- **Notes:** Already synced via RAG chunks (no new sync entity).
- **Change tracker:** Include episodic in push/pull when sync engine is enabled; use same LWW semantics as memories.

---

## 4. Config

### `config/qdrant_config.yaml`

Add:

```yaml
  arcturus_episodic:
    dimension: 768
    distance: cosine
    is_tenant: true
    tenant_keyword_field: user_id
    indexed_payload_fields: [session_id, space_id, original_query]
```

---

## 5. Migration Order

1. Run existing migrations (memories, RAG, hubs) as today.
2. Run `migrate_episodic_to_qdrant.py` — reads `episodic_skeletons/`, writes to `arcturus_episodic` with `user_id`, `space_id=__global__` for legacy.
3. Notes: Reindex RAG with `user_id` and `space_id` (Phase A migration already covers this for documents under `data/`; notes under `data/Notes/` get `space_id=__global__` unless path convention is used).

---

## 6. Implementation Checklist

- [x] Add `arcturus_episodic` to qdrant_config.yaml
- [x] Create `memory/backends/episodic_qdrant_store.py` — Qdrant CRUD for episodic
- [x] Update `EpisodicMemory.save_episode` to write to Qdrant + accept `space_id`
- [x] Update `search_episodes`, `get_recent_episodes` to read from Qdrant with `user_id`, `space_id` filter
- [x] Thread `space_id` from loop/ide_agent into `save_episode`
- [x] Add `migrate_episodic_to_qdrant.py` (in `migrate_all_memories.py` sequence)
- [x] Add `EpisodicDelta` + episodic to sync change_tracker and protocol
- [x] Notes: Path-derived `space_id` in RAG indexing (`_derive_space_id_for_notes` in server_rag)
