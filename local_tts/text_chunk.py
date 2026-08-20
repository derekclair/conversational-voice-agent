"""Split agent text into spoken sentence chunks for incremental TTS."""

from __future__ import annotations

import re

# Split after sentence-ending punctuation when followed by whitespace or end.
_SENTENCE_RE = re.compile(r"(?<=[.!?])(?:[\"')\]]*)(?=\s+|$)")


def split_spoken_sentences(text: str | None) -> list[str]:
    """Split *text* into non-empty spoken chunks.

    Rules (v1, English-first):
    - Split on ``.`` ``!`` ``?`` when followed by whitespace or end of string.
    - Keep trailing quotes/brackets attached to the sentence.
    - Collapse whitespace; drop empty fragments.
    - Merge a trailing fragment shorter than 3 chars into the previous chunk.
    - If no split applies, return a single-element list with the stripped text
      (or ``[]`` if empty/whitespace-only).
    """
    if text is None:
        return []
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return []

    parts: list[str] = []
    start = 0
    for match in _SENTENCE_RE.finditer(cleaned):
        end = match.end()
        chunk = cleaned[start:end].strip()
        if chunk:
            parts.append(chunk)
        start = end
    tail = cleaned[start:].strip()
    if tail:
        parts.append(tail)

    if not parts:
        return [cleaned]

    # Merge tiny trailing leftovers ("A." + "B" already fine; "Ok." + "x" merge)
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < 3:
            merged[-1] = f"{merged[-1]} {part}".strip()
        else:
            merged.append(part)
    return merged
