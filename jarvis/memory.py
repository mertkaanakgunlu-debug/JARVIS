"""Hybrid memory: ChromaDB (semantic recall) + Markdown vault (persistent log)."""

from __future__ import annotations

import uuid
from datetime import datetime, date
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

if TYPE_CHECKING:
    from jarvis.config import Settings


class Memory:
    def __init__(self, settings: "Settings") -> None:
        self._vault = settings.vault_dir
        self._chroma_dir = settings.chroma_dir
        self._embed_model = settings.embed_model
        self._ollama_url = settings.ollama_base_url

        self._vault.mkdir(parents=True, exist_ok=True)
        (self._vault / "conversations").mkdir(exist_ok=True)
        (self._vault / "notes").mkdir(exist_ok=True)
        (self._vault / "reports").mkdir(exist_ok=True)

        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_dir))

        try:
            ef = OllamaEmbeddingFunction(
                url=f"{self._ollama_url}/api/embeddings",
                model_name=self._embed_model,
            )
            self._collection = self._client.get_or_create_collection(
                "jarvis_memory", embedding_function=ef
            )
            self._embed_ok = True
        except Exception:
            # Ollama not running yet; fall back to default embeddings so app still starts
            self._collection = self._client.get_or_create_collection("jarvis_memory")
            self._embed_ok = False

    # ------------------------------------------------------------------
    # Semantic memory
    # ------------------------------------------------------------------

    def store(self, role: str, content: str, session_id: str) -> None:
        """Add a conversation turn to the vector store."""
        if not content.strip():
            return
        self._collection.add(
            documents=[f"[{role}] {content}"],
            ids=[str(uuid.uuid4())],
            metadatas=[{"role": role, "session": session_id, "ts": datetime.now().isoformat()}],
        )

    def recall(self, query: str, n: int = 5, max_doc_chars: int = 200) -> str:
        """Return a formatted block of relevant past memories, or empty string.

        Skips results that are semantically distant (distance > 0.6) and truncates
        each document to max_doc_chars to keep token injection small.
        """
        count = self._collection.count()
        if count == 0:
            return ""
        results = self._collection.query(
            query_texts=[query],
            n_results=min(n, count),
            include=["documents", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0]
        if not docs:
            return ""
        lines = ["Relevant context from memory:"]
        for doc, dist in zip(docs, distances):
            if dist > 0.6:
                continue
            snippet = doc[:max_doc_chars] + ("..." if len(doc) > max_doc_chars else "")
            lines.append(f"  • {snippet}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Vault (markdown log)
    # ------------------------------------------------------------------

    def _today_file(self) -> Path:
        return self._vault / "conversations" / f"{date.today().isoformat()}.md"

    def log_turn(self, role: str, content: str) -> None:
        """Append a conversation turn to today's vault file."""
        path = self._today_file()
        ts = datetime.now().strftime("%H:%M")
        header = "**You**" if role == "user" else "**JARVIS**"
        entry = f"\n### {ts} — {header}\n{content}\n"
        with path.open("a", encoding="utf-8") as f:
            if not path.exists() or path.stat().st_size == 0:
                f.write(f"# Session — {date.today().isoformat()}\n")
            f.write(entry)

    def save_note(self, topic: str, body: str) -> Path:
        """Write or append to vault/notes/{topic}.md."""
        safe_topic = "".join(c if c.isalnum() or c in "-_ " else "_" for c in topic).strip()
        path = self._vault / "notes" / f"{safe_topic}.md"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        is_new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# {topic}\n")
            f.write(f"\n## {ts}\n{body}\n")
        return path

    def count(self) -> int:
        return self._collection.count()
