"""Hybrid memory: ChromaDB (semantic recall) + Markdown vault (persistent log).

Two ChromaDB collections:
  jarvis_memory — conversation turns (default EF, local ONNX)
  jarvis_docs   — indexed documents for RAG (Gemini text-embedding-004)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb

if TYPE_CHECKING:
    from jarvis.config import Settings


def _build_gemini_ef(api_key: str):
    """Return a ChromaDB-compatible embedding function using Gemini text-embedding-004.

    Falls back to None (ChromaDB default ONNX EF) if api_key is empty or import fails.
    """
    if not api_key:
        return None
    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        embedder = GoogleGenerativeAIEmbeddings(
            model="models/text-embedding-004",
            google_api_key=api_key,
            task_type="retrieval_document",
        )

        class _GeminiEF:
            def __call__(self, input: list[str]) -> list[list[float]]:
                return embedder.embed_documents(input)

        return _GeminiEF()
    except Exception:
        return None


class Memory:
    def __init__(self, settings: "Settings") -> None:
        self._vault = settings.vault_dir
        self._chroma_dir = settings.chroma_dir

        self._vault.mkdir(parents=True, exist_ok=True)
        (self._vault / "conversations").mkdir(exist_ok=True)
        (self._vault / "notes").mkdir(exist_ok=True)
        (self._vault / "reports").mkdir(exist_ok=True)

        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_dir))

        # jarvis_memory: conversation recall — default ONNX EF (local, lightweight)
        self._collection = self._client.get_or_create_collection("jarvis_memory")

        # jarvis_docs: document RAG — Gemini embeddings for higher semantic quality
        gemini_ef = _build_gemini_ef(settings.gemini_api_key)
        doc_kwargs = {"embedding_function": gemini_ef} if gemini_ef else {}
        self._docs_collection = self._client.get_or_create_collection(
            "jarvis_docs", **doc_kwargs
        )
        self._gemini_ef_active = gemini_ef is not None

    # ------------------------------------------------------------------
    # Semantic memory (conversations)
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
        """Return a formatted block of relevant past memories, or empty string."""
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
            lines.append(f"  - {snippet}")
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    def count(self) -> int:
        return self._collection.count()

    # ------------------------------------------------------------------
    # Document RAG (Faz 6)
    # ------------------------------------------------------------------

    def index_document(
        self,
        source_path: str,
        chunks: list[str],
        doc_type: str = "document",
    ) -> int:
        """Store text chunks in jarvis_docs. Re-indexes if source was already indexed."""
        if not chunks:
            return 0
        ts = datetime.now().isoformat()
        safe_src = source_path.replace("\\", "/")

        # Remove stale chunks for this source
        try:
            existing = self._docs_collection.get(where={"source": safe_src})
            if existing["ids"]:
                self._docs_collection.delete(ids=existing["ids"])
        except Exception:
            pass

        ids = [f"{safe_src}::chunk{i}" for i in range(len(chunks))]
        metas = [
            {
                "source": safe_src,
                "chunk": i,
                "total": len(chunks),
                "doc_type": doc_type,
                "ts": ts,
            }
            for i in range(len(chunks))
        ]
        self._docs_collection.add(documents=chunks, ids=ids, metadatas=metas)
        return len(chunks)

    def search_vault(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search over indexed documents.

        Returns list of {content, source, score} dicts sorted by relevance.
        """
        count = self._docs_collection.count()
        if count == 0:
            return []
        results = self._docs_collection.query(
            query_texts=[query],
            n_results=min(n, count),
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        return [
            {"content": doc, "source": meta.get("source", ""), "score": 1.0 - dist}
            for doc, meta, dist in zip(docs, metas, distances)
        ]

    def list_indexed(self) -> list[str]:
        """Return unique source paths currently in jarvis_docs."""
        if self._docs_collection.count() == 0:
            return []
        all_metas = self._docs_collection.get(include=["metadatas"])["metadatas"] or []
        return sorted({m["source"] for m in all_metas if m})

    def count_docs(self) -> int:
        return self._docs_collection.count()

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
        entry = f"\n### {ts} --- {header}\n{content}\n"
        with path.open("a", encoding="utf-8") as f:
            if not path.exists() or path.stat().st_size == 0:
                f.write(f"# Session --- {date.today().isoformat()}\n")
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
