# Phase C: BM25 → Qdrant & Hybrid Search for Memories — Design

**Status:** Locked. Use this document as the implementation spec.

---

## Locked Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Sparse vectors vs full-text index** | Sparse vectors | True RRF fusion at DB layer; Qdrant v1.10+ has stable support; no app-side fusion logic. |
| **Sparse vector generation** | Client-side FastEmbed | Decouples from Qdrant; works with any deployment; same stack as dense embeddings; pluggable upgrade to SPLADE. |
| **SPLADE option** | Optional upgrade path | Start with BM25-style sparse embedding; can test SPLADE (e.g. `prithivida/Splade_PP_en_v1`) later for context-aware retrieval. |

---

## Overview

Phase C improves search quality and consistency by:
1. **3.1** Moving RAG keyword search from local BM25 (`rank_bm25` + `bm25_index.pkl`) into Qdrant via sparse vectors
2. **3.2** Adding hybrid search (vector + keyword) for memories in `arcturus_memories`

Both items use Qdrant sparse vectors + prefetch + RRF fusion. Sparse vectors are generated client-side with FastEmbed (BM25-style; SPLADE optional).

---

## 3.1 Move BM25 to Qdrant (RAG)

### Current State

| Component | Location | Role |
|-----------|----------|------|
| BM25 corpus | `metadata` from Qdrant `get_metadata()` or `faiss_index/metadata.json` | Chunk text for keyword search |
| BM25 index | `mcp_servers/faiss_index/bm25_index.pkl` | Local rank_bm25 index |
| Search flow | `server_rag.search_stored_documents_rag()` | Vector search → BM25 search → RRF fuse → entity gate |

**Limitations:** Local BM25 is user-specific only when Qdrant is tenant-scoped (`get_metadata(user_id)`); `bm25_index.pkl` is rebuilt/loaded per user or at reindex time; no single source of truth; sync/multi-device concerns.

### Target Architecture

Use Qdrant sparse vectors for keyword search on `arcturus_rag_chunks`:

- Add sparse vector `chunk-bm25` to each point, generated **client-side** via FastEmbed (BM25-style; optional SPLADE).
- Use Qdrant Query API with prefetch (dense + sparse) and RRF fusion.

### Sparse Vectors + Hybrid Search

#### Collection Changes

- `arcturus_rag_chunks` remains the single collection.
- Add a sparse vector `chunk-bm25` with `modifier: idf` (IDF for BM25-style scoring).
- Dense vector stays as the default/named vector; sparse is a second named vector.

#### Config (qdrant_config.yaml)

```yaml
arcturus_rag_chunks:
  dimension: 768
  distance: cosine
  is_tenant: true
  tenant_keyword_field: user_id
  indexed_payload_fields: [doc, doc_type, session_id, space_id]
  # Phase C: sparse vector for BM25 hybrid search
  sparse_vectors:
    chunk-bm25:
      modifier: idf   # for BM25 / IDF-based sparse scoring
```

#### Ingest Path

- When adding chunks, generate sparse vectors from chunk text using **FastEmbed** client-side (e.g. `SparseTextEmbedding` with BM25-style model; optional SPLADE `prithivida/Splade_PP_en_v1` for context-aware retrieval).
- Each point has: `vector` (dense) + `chunk-bm25` (sparse).
- No change to `user_id` / `space_id` payload fields.

#### Search Path

- Use Query API `prefetch`:
  - Prefetch 1: dense vector search (existing embedding) with `user_id` / `space_id` filter
  - Prefetch 2: sparse search on `chunk-bm25` with query text (same filters)
- Fuse with `query: FusionQuery(fusion=Fusion.RRF)`.
- Apply entity gate on fused results as today.

**Qdrant requirement:** v1.10.x or newer for sparse vectors and prefetch/RRF support.

### Removals (After Migration)

- `BM25Index` class, `_bm25_index`, and `bm25_index.pkl` build/load in `server_rag.py`
- `rank_bm25` dependency (optional: keep only if used elsewhere)
- Any logic that reads `faiss_index/metadata.json` solely for BM25

### Migration

- Reindex all RAG chunks so new points include sparse vectors.
- Migration script should pass chunk text to the BM25/sparse vector pipeline.
- No backfill of `bm25_index.pkl`; it is deprecated.

---

## 3.2 Hybrid Search for Memories

### Current State

| Component | Role |
|-----------|------|
| `memory_retriever.retrieve()` | Orchestrates semantic + entity + graph recall |
| `QdrantVectorStore.search()` | Vector search; optional `_apply_keyword_boosting` on results |
| Entity recall | NER → Neo4j → memory IDs |
| Graph expansion | From semantic results’ `entity_ids` |

**Gap:** Memory retrieval is vector + entity + graph. There is no true keyword/BM25 path; `_apply_keyword_boosting` only re-ranks vector results by keyword overlap.

### Target Architecture

Add a keyword/lexical search path for memories and fuse it with vector search via RRF, mirroring the RAG pattern.

- Add sparse vector `text-bm25` on memory text; generate **client-side** via FastEmbed; prefetch hybrid + RRF.

### Sparse Vectors for Memories

#### Collection Changes

- Add sparse vector `text-bm25` to `arcturus_memories` with `modifier: idf`.
- Dense vector remains the default vector.

#### Config (qdrant_config.yaml)

```yaml
arcturus_memories:
  dimension: 768
  distance: cosine
  is_tenant: true
  tenant_keyword_field: user_id
  indexed_payload_fields: [category, source, session_id, entity_labels, space_id, archived, visibility]
  sparse_vectors:
    text-bm25:
      modifier: idf
```

#### Ingest Path

- On `add()` (and during migration): generate sparse vector from memory `text` via FastEmbed client-side; store as named vector `text-bm25`.

#### Search Path

- In `_semantic_recall` (or equivalent), use prefetch:
  - Dense vector search (current flow)
  - Sparse search on `text-bm25` with `query_text`
- Fuse with RRF.
- Then run entity recall and graph expansion as today.
- Space/session filters apply to both prefetches.

### Integration with Existing Flow

- `memory_retriever.retrieve()` already passes `query_text` into `store.search()`.
- Extend `QdrantVectorStore.search()` to:
  - If `query_text` and sparse vector available: run hybrid prefetch, RRF, return fused list.
  - Else: current vector-only search (and optional `_apply_keyword_boosting` as fallback).
- Entity recall and graph expansion stay unchanged; they consume the fused semantic results.

### Fallback

- If sparse vectors are not configured: skip keyword prefetch; use vector-only search + `_apply_keyword_boosting` (current behavior).

---

## Dependencies and Order

| Step | Item | Depends On |
|------|------|------------|
| 1 | 3.1 BM25 → Qdrant (RAG) | None |
| 2 | 3.2 Hybrid search for memories | 3.1 (reuse pattern, config, and tooling) |

Implement 3.1 first; then apply the same pattern for memories in 3.2.

---

## Qdrant Version and Dependencies

- **Qdrant:** v1.10.x or newer (sparse vectors, prefetch, RRF).
- **Sparse vector generation:** Client-side FastEmbed (`SparseTextEmbedding`); no Qdrant inference required.

---

## Config Summary

### qdrant_config.yaml Additions

```yaml
# arcturus_rag_chunks - add:
  sparse_vectors:
    chunk-bm25:
      modifier: idf

# arcturus_memories - add:
  sparse_vectors:
    text-bm25:
      modifier: idf
```

---

## Implementation Checklist

### 3.1 BM25 to Qdrant (RAG)

- [x] Add `chunk-bm25` sparse vector config to `arcturus_rag_chunks` in qdrant_config
- [x] Add FastEmbed sparse embedding; update `QdrantRAGStore.add_chunks()` to generate and store sparse vectors from chunk text
- [x] Extend `QdrantRAGStore.search()` with prefetch + RRF using prefetch + RRF
- [x] Update server_rag to use Qdrant hybrid; remove BM25Index to use Qdrant hybrid search when provider is Qdrant; remove BM25Index / bm25_index.pkl
- [x] New chunks get sparse on add; reindex populates sparse for existing RAG chunks
- [ ] Optional: remove `rank-bm25` from pyproject.toml if unused

### 3.2 Hybrid Search for Memories

- [x] Add `text-bm25` sparse vector config to `arcturus_memories` in qdrant_config
- [x] Add FastEmbed; update `QdrantVectorStore.add()` to generate and store sparse vector from memory text
- [x] Extend `QdrantVectorStore.search()` to use hybrid prefetch + RRF when `query_text` is provided
- [ ] Add migration/backfill for existing memories (sparse vectors)
- [x] Skip `_apply_keyword_boosting` when hybrid active when hybrid is active

---

## Other Notes

- **Entity gate:** Keep entity gate in RAG search after fusion; no change to current behavior.
- **Backward compatibility:** FAISS RAG backend remains vector-only; hybrid requires `RAG_VECTOR_STORE_PROVIDER=qdrant`.
