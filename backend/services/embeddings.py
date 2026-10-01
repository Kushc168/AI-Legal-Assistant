"""Local embeddings: no network, no model download.

A hashed bag of stemmed unigrams and bigrams, log-scaled and L2-normalised. It is lexical rather
than neural, which suits contract language (exact legal terms matter), and the retrieval layer
combines it with BM25. To use a neural embedder, replace `embed_texts` - nothing else changes.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

DIM = 1024

STOPWORDS = frozenset(
    """a an and are as at be been being but by for from has have if in into is it its of on or
    such that the their then there these this those to was were will with which who whom shall
    may any all each other than under upon herein hereof hereto thereof hereby what how when where
    why does do can about""".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")
# Longest first, so "termination" and "terminate" both stem to "termin".
_SUFFIXES = ("ations", "ation", "ments", "ment", "ated", "ates", "ate", "ings", "ing", "ions", "ion", "ies", "ied",
             "es", "ed", "ly", "s")


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def _features(tokens: list[str]) -> list[str]:
    return tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def embed_text(text: str) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    for feature in _features(tokenize(text)):
        h = zlib.crc32(feature.encode("utf-8"))
        vec[h % DIM] += 1.0 if (h >> 16) & 1 else -1.0
    vec = np.sign(vec) * np.log1p(np.abs(vec))
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def embed_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    return np.vstack([embed_text(t) for t in texts])


def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


if __name__ == "__main__":
    a = embed_text("The supplier's liability shall not be limited")
    b = embed_text("limitation of liability cap")
    c = embed_text("payment of invoices within 30 days")
    print("related", float(a @ b), "unrelated", float(a @ c))
