"""jarvis/memory.py -- embed_query() regression for both local EF wrappers.

This project's installed chromadb calls embedding_function() for add() but
embedding_function.embed_query() for query() -- unconditionally, no hasattr
fallback. _OllamaEF/_GeminiEF only implemented __call__, so any semantic
recall (recall_facts/recall_procedures/find_similar_fact/...) against an
Ollama- or Gemini-backed collection raised AttributeError the moment
.query() ran, even though .add() always looked fine -- confirmed live via
chromadb/api/models/CollectionCommon.py's _embed(is_query=True) path.

Both classes' HTTP/SDK calls are mocked here -- this is about the
embed_query delegation the fix adds, not the underlying Ollama/Gemini
plumbing.

Also covers a second, unrelated bug found live while verifying the above:
_build_gemini_ef's hardcoded model id ("text-embedding-004") had been
retired server-side, 404ing on every real call. Fixed with a corrected
model id ("gemini-embedding-2") plus a construction-time smoke test so a
future bad model id fails fast instead of crashing every real recall.
"""
from __future__ import annotations

from types import SimpleNamespace

import jarvis.memory as memory


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeOllamaClient:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json_body=None, **kw):
        json_body = json_body if json_body is not None else kw.get("json", {})
        return _FakeResponse({"embedding": [float(len(json_body["prompt"]))]})


def test_ollama_ef_embed_query_delegates_to_call(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse({}))
    monkeypatch.setattr(httpx, "Client", _FakeOllamaClient)

    settings = SimpleNamespace(ollama_base_url="http://localhost:11434", embed_model="nomic-embed-text")
    ef = memory._build_ollama_ef(settings)
    assert ef is not None

    # BUG: ef used to have no embed_query at all -- chromadb's query() path
    # would raise AttributeError, even though ef(...) (add()'s path) worked.
    assert ef.embed_query(["ab", "abcd"]) == ef(["ab", "abcd"])


class _FakeGeminiEmbedder:
    def __init__(self, *a, **kw):
        pass

    def embed_documents(self, texts):
        return [[float(len(t))] for t in texts]


def test_gemini_ef_embed_query_delegates_to_call(monkeypatch):
    import langchain_google_genai
    monkeypatch.setattr(langchain_google_genai, "GoogleGenerativeAIEmbeddings", _FakeGeminiEmbedder)

    ef = memory._build_gemini_ef("fake-api-key")
    assert ef is not None
    assert ef.embed_query(["ab", "abcd"]) == ef(["ab", "abcd"])


def test_gemini_ef_is_none_without_api_key():
    assert memory._build_gemini_ef("") is None


class _FailingGeminiEmbedder:
    def __init__(self, *a, **kw):
        pass

    def embed_documents(self, texts):
        raise RuntimeError("404 model not found")  # e.g. a retired/renamed model id


def test_gemini_ef_falls_back_to_none_when_smoke_test_fails(monkeypatch):
    """Live-found 2026-07-15: the hardcoded model id ("text-embedding-004" at the
    time) had been retired server-side -- every real call 404'd. _build_gemini_ef
    now smoke-tests one embed call at construction time (same reasoning as
    _build_ollama_ef's reachability probe) so a bad/retired model id falls
    through to the next tier (default ONNX EF) instead of returning an EF
    object that crashes on every real recall later."""
    import langchain_google_genai
    monkeypatch.setattr(langchain_google_genai, "GoogleGenerativeAIEmbeddings", _FailingGeminiEmbedder)

    assert memory._build_gemini_ef("fake-api-key") is None
