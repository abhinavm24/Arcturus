# P11 Mnemo: Neo4j Knowledge Graph Design

> **Status:** Implemented. See memory/knowledge_graph.py, memory/entity_extractor.py, scripts/migrate_memories_to_neo4j.py.
> **Context:** Phase 3 of Mnemo — Neo4j layer for Remme memories (entities, relationships, User/Session nodes).

---

## 1. Overview

Neo4j stores extracted entities and relationships from Remme memories. It ties to Qdrant via `memory_id` (Qdrant point id) and `entity_ids` (Neo4j entity ids in Qdrant payload).

---

## 2. Neo4j Schema

### Nodes

| Node Label | Properties | Purpose |
|------------|------------|---------|
| **User** | `id`, `user_id` | Central node; multi-tenant; anchor for derived facts |
| **Memory** | `id` (Qdrant point id), `category`, `source`, `created_at` | Bridge to Qdrant |
| **Session** | `id`, `session_id`, `original_query`, `created_at` | Provenance; temporal grouping |
| **Entity** | `id`, `type`, `name`, `created_at` | Person, Company, Concept, City, Date, etc. |

### Relationships

| Relationship | From → To | Properties | Purpose |
|--------------|-----------|------------|---------|
| **HAS_MEMORY** | User → Memory | — | Ownership; multi-tenant |
| **FROM_SESSION** | Memory → Session | — | Provenance; "which session produced this memory" |
| **CONTAINS_ENTITY** | Memory → Entity | — | Memory mentions this entity |
| **RELATED_TO** | Entity → Entity | `type`, `value`, `confidence`, `source_memory_ids` | e.g. Person -[:WORKS_AT]-> Company |
| **LIVES_IN** | User → Entity | `source_memory_ids` | Derived: user lives in City |
| **WORKS_AT** | User → Entity | `source_memory_ids` | Derived: user works at Company |
| **KNOWS** | User → Entity | `source_memory_ids` | Derived: user knows Person |
| **PREFERS** | User → Entity | `source_memory_ids` | Derived: user prefers Concept (e.g. dietary) |
| **CONTRADICTS** | (Phase 5) | — | Mark conflicting facts |

### Example Graph

```
(User {user_id: "abc"})
  -[:HAS_MEMORY]-> (Memory {id: "qdrant-123"})
  -[:FROM_SESSION]-> (Session {session_id: "run_456"})
  -[:CONTAINS_ENTITY]-> (Entity {type: "Person", name: "John"})
  -[:CONTAINS_ENTITY]-> (Entity {type: "Company", name: "Google"})

(Entity {name: "John"}) -[:RELATED_TO {type: "works_at", source_memory_ids: ["qdrant-123"]}]-> (Entity {name: "Google"})

(User) -[:LIVES_IN {source_memory_ids: ["qdrant-123"]}]-> (Entity {type: "City", name: "Morrisville"})
```

---

## 3. Qdrant Changes

### Payload additions for `arcturus_memories`

| Field | Type | Purpose |
|-------|------|---------|
| `user_id` | string | Already exists (multi-tenant) |
| `session_id` | string | Run/session id; link to Session node |
| `entity_ids` | list[string] | Neo4j entity ids; enables Qdrant ↔ Neo4j link |

### Config

- Add `session_id` and `entity_ids` to `indexed_payload_fields` in `config/qdrant_config.yaml` if filtered search is needed.

---

## 4. Ingestion Flow

1. New memory added to Qdrant (with `user_id`, `session_id` in payload).
2. Create or get **User** and **Session** nodes in Neo4j.
3. Create **Memory** node; link `(User)-[:HAS_MEMORY]->(Memory)` and `(Memory)-[:FROM_SESSION]->(Session)`.
4. Extract entities and relationships from memory text (LLM or NER).
5. Create **Entity** nodes; link `(Memory)-[:CONTAINS_ENTITY]->(Entity)`.
6. Create `(Entity)-[:RELATED_TO]->(Entity)` with `source_memory_ids`.
7. Infer user-centric facts → create `(User)-[:LIVES_IN|WORKS_AT|KNOWS|PREFERS]->(Entity)` with `source_memory_ids`.
8. Update Qdrant memory payload with `entity_ids`.

---

## 5. Retrieval Flow

```
Query: "What do I know about John and his work?"
         │
         ├─► Qdrant: semantic search → top-k memories (memory_ids)
         │
         └─► Neo4j: 
             - Find Entity(name="John")
             - Traverse RELATED_TO, LIVES_IN, WORKS_AT, etc.
             - Get Memory nodes via CONTAINS_ENTITY or HAS_MEMORY
             - Optionally: (User)-[:LIVES_IN]->(City) for user context
         │
         ▼
    Fused context for agent
```

---

## 6. Implementation Order

1. Add Neo4j client + schema (User, Memory, Session, Entity, relationships).
2. Entity extraction pipeline (LLM or NER).
3. Ingestion: on memory add → extract → write Neo4j → update Qdrant `entity_ids`.
4. Ensure Qdrant payload includes `user_id`, `session_id`, `entity_ids`.
5. Retrieval: Qdrant search + Neo4j expansion.
6. Migration script: backfill existing Qdrant memories → Neo4j.

---

## 7. Files to Create/Modify

| File | Action |
|------|--------|
| `memory/knowledge_graph.py` | New: Neo4j client, schema, CRUD |
| `memory/entity_extractor.py` | New: LLM/NER extraction |
| `config/qdrant_config.yaml` | Add `session_id`, `entity_ids` to indexed fields |
| `memory/backends/qdrant_store.py` | Ensure add() accepts `session_id`, `entity_ids` |
| `remme/extractor.py` or ingestion path | Call knowledge graph on memory add |
| `scripts/migrate_memories_to_neo4j.py` | New: backfill script |

---

## 8. How to Continue in a New Chat

Start a new chat and say:

> "Continue the Neo4j knowledge graph implementation from @CAPSTONE/project_charters/P11_NEO4J_KNOWLEDGE_GRAPH_DESIGN.md"

Or attach the file and ask to implement the design.
