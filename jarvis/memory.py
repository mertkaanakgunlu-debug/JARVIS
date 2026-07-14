"""Hybrid memory: ChromaDB (semantic recall) + Markdown vault (persistent log).

Three ChromaDB collections:
  jarvis_memory    — conversation turns (default EF, local ONNX)
  jarvis_docs      — indexed documents for RAG
  jarvis_summaries — session summaries for RAG

jarvis_docs/jarvis_summaries prefer, in order (Faz 1 — local-first):
  1. Ollama nomic-embed-text (local, free) if reachable
  2. Gemini text-embedding-004 (cloud, higher quality) if an API key is set
  3. ChromaDB's default ONNX EF (same as jarvis_memory already uses)
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb

if TYPE_CHECKING:
    from jarvis.config import Settings


def _build_ollama_ef(settings: "Settings"):
    """ChromaDB-compatible EF using Ollama's nomic-embed-text (local, free).

    Probes reachability once at construction (short timeout) rather than
    per-embed-call, so an absent/stopped Ollama server fails fast here and
    falls through to Gemini/default instead of stalling every recall.
    """
    try:
        import httpx

        base_url = settings.ollama_base_url
        model = settings.embed_model
        httpx.get(f"{base_url}/api/tags", timeout=1.5).raise_for_status()

        class _OllamaEF:
            def __call__(self, input: list[str]) -> list[list[float]]:
                out = []
                with httpx.Client(timeout=30) as client:
                    for text in input:
                        r = client.post(
                            f"{base_url}/api/embeddings",
                            json={"model": model, "prompt": text},
                        )
                        r.raise_for_status()
                        out.append(r.json()["embedding"])
                return out

            def name(self) -> str:
                return f"ollama-{model}"

        return _OllamaEF()
    except Exception:
        return None


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

            def name(self) -> str:
                return "gemini-text-embedding-004"

        return _GeminiEF()
    except Exception:
        return None


def _build_embedding_function(settings: "Settings"):
    """Preferred embedding function + a short label, local-first (see module docstring)."""
    ef = _build_ollama_ef(settings)
    if ef is not None:
        return ef, "ollama"
    ef = _build_gemini_ef(settings.gemini_api_key)
    if ef is not None:
        return ef, "gemini"
    return None, "default"


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

        # jarvis_docs / jarvis_summaries: RAG collections — local-first EF choice
        # (Ollama nomic-embed-text -> Gemini -> ChromaDB default; see _build_embedding_function)
        ef, self._embedding_backend = _build_embedding_function(settings)
        doc_kwargs = {"embedding_function": ef} if ef else {}
        try:
            self._docs_collection = self._client.get_or_create_collection(
                "jarvis_docs", **doc_kwargs
            )
        except ValueError:
            # EF conflict: collection exists with a different EF — open without custom EF
            self._docs_collection = self._client.get_or_create_collection("jarvis_docs")
            self._embedding_backend = "default"

        try:
            self._summaries_collection = self._client.get_or_create_collection(
                "jarvis_summaries", **doc_kwargs
            )
        except ValueError:
            self._summaries_collection = self._client.get_or_create_collection("jarvis_summaries")

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
    # Session summary RAG (Faz 13-A)
    # ------------------------------------------------------------------

    def store_summary(self, session_id: str, summary: str, topic_hint: str | None,
                      last_active: str) -> None:
        """Embed and store a session summary. Idempotent — overwrites same session_id."""
        if not summary.strip():
            return
        # Remove existing entry for this session (idempotent)
        try:
            existing = self._summaries_collection.get(ids=[session_id])
            if existing["ids"]:
                self._summaries_collection.delete(ids=[session_id])
        except Exception:
            pass
        self._summaries_collection.add(
            documents=[summary],
            ids=[session_id],
            metadatas=[{
                "session_id": session_id,
                "topic_hint": topic_hint or "",
                "last_active": last_active,
            }],
        )

    def recall_summaries(self, query: str, n: int = 3,
                         distance_max: float = 0.55) -> list[dict]:
        """Return semantically relevant past session summaries for a query."""
        count = self._summaries_collection.count()
        if count == 0:
            return []
        results = self._summaries_collection.query(
            query_texts=[query],
            n_results=min(n, count),
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]
        out = []
        for doc, meta, dist in zip(docs, metas, dists):
            if dist > distance_max:
                continue
            out.append({
                "session_id": meta.get("session_id", ""),
                "topic_hint": meta.get("topic_hint", ""),
                "last_active": meta.get("last_active", ""),
                "summary": doc,
                "score": round(1.0 - dist, 3),
            })
        return out

    def count_summaries(self) -> int:
        return self._summaries_collection.count()

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
