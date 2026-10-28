"""Embedding backends. The default is a small multilingual ONNX model, CPU only."""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from pathlib import Path
from typing import Protocol

from localkb.errors import LocalKBError
from localkb.textutil import cjk_index_text, split_window

DEFAULT_EMBEDDER = "fastembed"
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# MiniLM truncates at 128 tokens. Windows stay under that for both English and CJK,
# then the chunk vector is the normalized mean so longer excerpts still embed.
_CJK_CHAR = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

_MODELS: dict[tuple[str, str], object] = {}
_MODEL_LOCK = threading.Lock()


class Embedder(Protocol):
    name: str
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Deterministic offline embedder for tests. Not for real retrieval quality."""

    name = "hash"
    model = "hash-v1"
    dim = 256
    documents_embedded = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        type(self).documents_embedded += len(texts)
        return [_hash_vector(text, self.dim) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _hash_vector(text, self.dim)


class FastEmbedEmbedder:
    name = "fastembed"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self.dim = lookup_dim(model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        windows: list[str] = []
        spans: list[tuple[int, int]] = []
        for text in texts:
            parts = _embedding_windows(text)
            start = len(windows)
            windows.extend(parts)
            spans.append((start, len(windows)))
        raw = self._embed(windows)
        vectors: list[list[float]] = []
        for start, end in spans:
            block = raw[start:end]
            mean = [sum(values) / len(block) for values in zip(*block, strict=True)]
            vectors.append(_normalize(mean))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else _normalize([1.0] + [0.0] * (self.dim - 1))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            embedded = list(model.embed(texts))  # type: ignore[attr-defined]
        except Exception as exc:
            raise LocalKBError(
                f"failed to embed with {self.model}: {exc}. "
                "The ONNX model is downloaded once into the local cache "
                f"({cache_dir()}). Check access to Hugging Face, or retry."
            ) from exc
        vectors = [_as_float_list(item) for item in embedded]
        if len(vectors) != len(texts):
            raise LocalKBError("embedder returned an unexpected number of vectors")
        return vectors

    def _load(self) -> object:
        key = (self.model, str(cache_dir()))
        with _MODEL_LOCK:
            cached = _MODELS.get(key)
            if cached is None:
                from fastembed import TextEmbedding

                cached = TextEmbedding(
                    model_name=self.model,
                    cache_dir=str(cache_dir()),
                    cuda=False,
                )
                _MODELS[key] = cached
            return cached


def create_embedder(kind: str, model: str | None = None) -> Embedder:
    normalized = kind.strip().lower()
    if normalized == "hash":
        if model not in (None, "", "hash", "hash-v1"):
            raise LocalKBError("--model is only used with --embedder fastembed")
        return HashEmbedder()
    if normalized == "ollama":
        raise LocalKBError(
            "Ollama embeddings are not implemented in localkb 0.1. "
            "The ollama extra is reserved for a later release. "
            "Use the default CPU embedder (fastembed); it does not need an API key."
        )
    if normalized == "fastembed":
        return FastEmbedEmbedder(model or DEFAULT_MODEL)
    raise LocalKBError(f"unknown embedder {kind!r}; choose fastembed, hash, or ollama")


def lookup_dim(model: str) -> int:
    from fastembed import TextEmbedding

    for item in TextEmbedding.list_supported_models():
        if item.get("model") == model:
            return int(item["dim"])
    raise LocalKBError(
        f"unknown fastembed model {model!r}. "
        f"The default is {DEFAULT_MODEL}. "
        "A stronger Chinese-English option is jinaai/jina-embeddings-v2-base-zh."
    )


def cache_dir() -> Path:
    override = os.environ.get("FASTEMBED_CACHE_PATH") or os.environ.get("LOCALKB_CACHE")
    if override:
        path = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
        path = base / "localkb" / "fastembed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _embedding_windows(text: str) -> list[str]:
    cleaned = text.strip() or " "
    cjk = len(_CJK_CHAR.findall(cleaned))
    if cjk >= 8 and cjk / max(len(cleaned), 1) >= 0.2:
        size, overlap = 96, 24
    else:
        size, overlap = 320, 64
    return split_window(cleaned, size, overlap) or [cleaned[:size]]


def _hash_vector(text: str, dim: int) -> list[float]:
    tokens = re.findall(r"\w+", cjk_index_text(text).lower(), flags=re.UNICODE)
    vector = [0.0] * dim
    if not tokens:
        vector[0] = 1.0
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    return _normalize(vector)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        zero = [0.0] * len(vector)
        if zero:
            zero[0] = 1.0
        return zero
    return [value / norm for value in vector]


def _as_float_list(value: object) -> list[float]:
    return [float(item) for item in value]  # type: ignore[union-attr]
