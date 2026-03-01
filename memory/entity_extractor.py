"""
Entity Extractor — Extracts entities and relationships from memory text for the knowledge graph.

Uses LLM (Ollama) to produce structured entities, entity-entity relationships,
and user-centric facts (LIVES_IN, WORKS_AT, KNOWS, PREFERS).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings_loader import get_ollama_url, get_model, get_timeout

import requests


class EntityExtractor:
    """
    Extracts entities, relationships, and user facts from memory text.
    Output format matches KnowledgeGraph.ingest_memory() expectations.
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or get_model("memory_extraction")
        self.api_url = get_ollama_url("chat")
        self._prompt: Optional[str] = None

    def _load_prompt(self) -> str:
        if self._prompt is not None:
            return self._prompt
        prompt_path = Path(__file__).parent.parent / "prompts" / "entity_extraction.md"
        if prompt_path.exists():
            self._prompt = prompt_path.read_text().strip()
        else:
            self._prompt = (
                "Extract entities (Person, Company, City, Concept, etc.), "
                "entity_relationships (from_type, from_name, to_type, to_name, type), "
                "and user_facts (rel_type: LIVES_IN|WORKS_AT|KNOWS|PREFERS, type, name). "
                "Return JSON: {entities: [...], entity_relationships: [...], user_facts: [...]}"
            )
        return self._prompt

    def extract(self, text: str) -> Dict[str, Any]:
        """
        Extract entities and relationships from memory text.

        Returns:
            {
                "entities": [{"type": "Person", "name": "John"}, ...],
                "entity_relationships": [{"from_type", "from_name", "to_type", "to_name", "type", "value?"}, ...],
                "user_facts": [{"rel_type": "LIVES_IN", "type": "City", "name": "X"}, ...]
            }
        """
        prompt = self._load_prompt()
        try:
            response = requests.post(
                self.api_url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": f"Extract entities and relationships from this memory:\n\n{text}",
                        },
                    ],
                    "stream": False,
                    "options": {"temperature": 0.1},
                    "format": "json",
                },
                timeout=get_timeout(),
            )
            response.raise_for_status()
            result = response.json()
            content = result.get("message", {}).get("content", "{}")
            return self._parse(content)
        except requests.exceptions.RequestException as e:
            print(f"[EntityExtractor] Ollama request failed: {e}")
            return {"entities": [], "entity_relationships": [], "user_facts": []}
        except Exception as e:
            print(f"[EntityExtractor] Extraction error: {e}")
            return {"entities": [], "entity_relationships": [], "user_facts": []}

    def _parse(self, content: str) -> Dict[str, Any]:
        """Parse LLM JSON output into expected structure."""
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return {"entities": [], "entity_relationships": [], "user_facts": []}
            return {
                "entities": self._normalize_entities(parsed.get("entities", [])),
                "entity_relationships": self._normalize_relationships(
                    parsed.get("entity_relationships", [])
                ),
                "user_facts": self._normalize_user_facts(parsed.get("user_facts", [])),
            }
        except json.JSONDecodeError:
            return {"entities": [], "entity_relationships": [], "user_facts": []}

    def _normalize_entities(self, raw: List[Any]) -> List[Dict[str, str]]:
        out = []
        for e in raw:
            if isinstance(e, dict) and e.get("name"):
                out.append({
                    "type": str(e.get("type", "Concept")).strip(),
                    "name": str(e.get("name", "")).strip(),
                })
        return out

    def _normalize_relationships(self, raw: List[Any]) -> List[Dict[str, Any]]:
        out = []
        for r in raw:
            if isinstance(r, dict) and r.get("from_name") and r.get("to_name"):
                out.append({
                    "from_type": str(r.get("from_type", "Entity")),
                    "from_name": str(r.get("from_name", "")).strip(),
                    "to_type": str(r.get("to_type", "Entity")),
                    "to_name": str(r.get("to_name", "")).strip(),
                    "type": str(r.get("type", "related_to")).strip(),
                    "value": r.get("value"),
                    "confidence": float(r.get("confidence", 1.0)),
                })
        return out

    def _normalize_user_facts(self, raw: List[Any]) -> List[Dict[str, str]]:
        valid_rel = {"LIVES_IN", "WORKS_AT", "KNOWS", "PREFERS"}
        out = []
        for f in raw:
            if isinstance(f, dict) and f.get("name"):
                rel = str(f.get("rel_type", "KNOWS")).upper()
                if rel not in valid_rel:
                    rel = "KNOWS"
                out.append({
                    "rel_type": rel,
                    "type": str(f.get("type", "Concept")),
                    "name": str(f.get("name", "")).strip(),
                })
        return out
