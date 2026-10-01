"""Hybrid retrieval (lexical embedding cosine + BM25) over one or more contracts' chunks."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .. import db
from . import embeddings

BM25_K1 = 1.4
BM25_B = 0.75
COSINE_WEIGHT = 0.5


@dataclass
class Hit:
    chunk: dict
    score: float       # hybrid score used for ranking (0-1)
    similarity: float  # cosine similarity of the query and chunk embeddings (0-1)


class _Index:
    def __init__(self, contract_id: str):
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE contract_id = ? ORDER BY idx", (contract_id,)
            ).fetchall()
        self.chunks = [{k: r[k] for k in r.keys() if k != "embedding"} for r in rows]
        self.matrix = (
            np.vstack([embeddings.from_blob(r["embedding"]) for r in rows])
            if rows
            else np.zeros((0, embeddings.DIM), dtype=np.float32)
        )
        self.tokens = [embeddings.tokenize(c["text"]) for c in self.chunks]
        self.tf = [Counter(t) for t in self.tokens]
        self.doc_len = [len(t) for t in self.tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        df: Counter = Counter()
        for t in self.tokens:
            df.update(set(t))
        n = len(self.tokens)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def bm25(self, query_tokens: list[str]) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        for i, tf in enumerate(self.tf):
            s = 0.0
            for term in query_tokens:
                f = tf.get(term)
                if not f:
                    continue
                denom = f + BM25_K1 * (1 - BM25_B + BM25_B * self.doc_len[i] / (self.avg_len or 1))
                s += self.idf.get(term, 0.0) * f * (BM25_K1 + 1) / denom
            scores[i] = s
        return scores


_cache: dict[str, _Index] = {}
_lock = threading.Lock()


def index_for(contract_id: str) -> _Index:
    with _lock:
        if contract_id not in _cache:
            _cache[contract_id] = _Index(contract_id)
        return _cache[contract_id]


def invalidate(contract_id: str) -> None:
    with _lock:
        _cache.pop(contract_id, None)


def search(contract_ids: list[str], query: str, top_k: int = 6) -> list[Hit]:
    query_vec = embeddings.embed_text(query)
    query_tokens = embeddings.tokenize(query)
    hits: list[Hit] = []
    for cid in contract_ids:
        index = index_for(cid)
        if not index.chunks:
            continue
        cosine = np.clip(index.matrix @ query_vec, 0, 1)
        bm25 = index.bm25(query_tokens)
        bm25_norm = bm25 / (bm25 + 4.0)  # saturating: comparable across queries and contracts
        hybrid = COSINE_WEIGHT * cosine + (1 - COSINE_WEIGHT) * bm25_norm
        for i in np.argsort(-hybrid)[:top_k]:
            if hybrid[i] <= 0:
                continue
            hits.append(Hit(chunk=index.chunks[i], score=float(hybrid[i]), similarity=float(cosine[i])))
    hits.sort(key=lambda h: -h.score)
    return hits[:top_k]


def multi_search(contract_ids: list[str], queries: list[str], top_k: int = 6) -> list[Hit]:
    """Run several queries and merge, keeping each chunk's best score."""
    best: dict[str, Hit] = {}
    for q in queries:
        for hit in search(contract_ids, q, top_k=top_k):
            cid = hit.chunk["id"]
            if cid not in best or hit.score > best[cid].score:
                best[cid] = hit
    return sorted(best.values(), key=lambda h: -h.score)[:top_k]


def keyword_scan(text: str, patterns: list[str]) -> list[re.Match]:
    matches = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, text, re.IGNORECASE))
    return matches


def format_excerpts(hits: list[Hit] | list[dict]) -> str:
    """Render chunks as <excerpt> blocks for prompts (see PROMPTS.md)."""
    blocks = []
    for item in hits:
        chunk = item.chunk if isinstance(item, Hit) else item
        clause = chunk.get("clause_ref") or ""
        text = chunk["text"].replace("</excerpt>", "</ excerpt>")
        blocks.append(
            f'<excerpt chunk_id="{chunk["id"]}" page="{chunk["page_start"]}" clause="{clause}">\n'
            f"{text}\n</excerpt>"
        )
    return "\n\n".join(blocks) if blocks else "(no excerpts retrieved)"
