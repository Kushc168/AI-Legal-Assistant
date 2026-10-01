"""Verification of model output before anything is stored or shown (SPEC section 3.6).

The model may only point at evidence; this module checks that the evidence is real.
"""

from __future__ import annotations

import re

import jsonschema

_QUOTE_CHARS = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\s?%)?")
_TRIM = " \t\n\"'.,;:()[]"


class SchemaError(Exception):
    pass


def validate_schema(data: dict, schema: dict) -> None:
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path)
        raise SchemaError(f"{path or '<root>'}: {exc.message}") from exc


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-case, unify quote characters and collapse whitespace; keep a map to original indices."""
    out, index_map, prev_space = [], [], False
    for i, ch in enumerate(text):
        ch = _QUOTE_CHARS.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch.lower())
        index_map.append(i)
    return "".join(out), index_map


def locate_quote(quote: str, source: str) -> tuple[int, int] | None:
    """Find a verbatim quote in source text (whitespace/quote-style tolerant).

    Returns (start, end) offsets into `source`, or None if the quote is not there.
    """
    q = quote.strip(_TRIM)
    if len(q) < 8 or "..." in q or "…" in q:
        return None
    norm_q, _ = _normalise_with_map(q)
    norm_s, index_map = _normalise_with_map(source)
    pos = norm_s.find(norm_q)
    if pos < 0:
        return None
    return index_map[pos], index_map[pos + len(norm_q) - 1] + 1


def numbers_in(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group(0)) for m in _NUMBER.finditer(text or "")}


def ungrounded_numbers(text: str, *sources: str, allowed: set[str] | None = None) -> set[str]:
    grounded = set(allowed or set())
    for source in sources:
        grounded |= numbers_in(source)
    # Treat "90" as grounded if "90%" is (and vice versa), and the parts of a clause number like "5.2".
    grounded |= {n.rstrip("%") for n in grounded}
    grounded |= {part for n in grounded for part in re.split(r"[.,]", n.rstrip("%")) if part}
    return {n for n in numbers_in(text) if n not in grounded and n.rstrip("%") not in grounded}


def strip_ungrounded_sentences(text: str, *sources: str, allowed: set[str] | None = None) -> tuple[str, bool]:
    """Remove sentences that state numbers not present in the sources. Returns (text, changed)."""
    if not text:
        return text, False
    # Split on sentence ends and line breaks, keeping the separators so formatting survives.
    parts = re.split(r"((?<=[.!?])\s+|\n+)", text)
    kept, removed = [], False
    for i in range(0, len(parts), 2):
        sentence, sep = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
        if ungrounded_numbers(sentence, *sources, allowed=allowed):
            removed = True
            continue
        kept.append(sentence + sep)
    return "".join(kept).strip(), removed


def clamp_severity(proposed: str, default: str, order: list[str]) -> str:
    if proposed not in order:
        return default
    d, p = order.index(default), order.index(proposed)
    return order[max(d - 1, min(d + 1, p))]


def citations_in_context(chunk_ids: list[str], allowed_ids: set[str]) -> bool:
    return all(cid in allowed_ids for cid in chunk_ids)
