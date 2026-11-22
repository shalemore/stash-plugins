"""Text normalization and character n-gram similarity for clustering descriptions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

DEFAULT_NGRAM_SIZE = 3


def normalize_description(text: str) -> str:
    """Strip whitespace, punctuation, symbols, and control characters, then casefold."""
    kept: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("Z", "C", "P", "S"):
            continue
        kept.append(ch)
    return "".join(kept).casefold()


def char_ngrams(normalized: str, n: int = DEFAULT_NGRAM_SIZE) -> frozenset[str]:
    """Build character n-gram set from an already normalized string."""
    if not normalized:
        return frozenset()
    if len(normalized) <= n:
        return frozenset({normalized})
    return frozenset(normalized[i : i + n] for i in range(len(normalized) - n + 1))


def jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two n-gram sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # Quick upper-bound check: |A & B| / |A | B| <= min(|A|, |B|) / max(|A|, |B|)
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


@dataclass(frozen=True)
class IndexedCandidate:
    """Precomputed normalized text and n-grams for fast matching."""

    key: str
    normalized: str
    ngrams: frozenset[str]


class DescriptionMatcher:
    """Matches incoming descriptions against indexed candidates above a Jaccard threshold."""

    def __init__(self, threshold: float, ngram_size: int = DEFAULT_NGRAM_SIZE) -> None:
        self.threshold: float = threshold
        self.ngram_size: int = ngram_size
        self._candidates: list[IndexedCandidate] = []
        self._exact: dict[str, IndexedCandidate] = {}

    def add(self, key: str, description: str) -> None:
        normalized = normalize_description(description)
        if not normalized:
            return
        candidate = IndexedCandidate(
            key=key,
            normalized=normalized,
            ngrams=char_ngrams(normalized, self.ngram_size),
        )
        self._candidates.append(candidate)
        # Keep the earliest candidate for exact-match lookups.
        _ = self._exact.setdefault(normalized, candidate)

    def find_best(self, description: str) -> tuple[str, float] | None:
        """Return `(candidate_key, score)` for the best match >= threshold, or None."""
        normalized = normalize_description(description)
        if not normalized:
            return None

        if exact := self._exact.get(normalized):
            return exact.key, 1.0

        if self.threshold >= 1.0:
            return None

        query_grams = char_ngrams(normalized, self.ngram_size)
        query_len = len(query_grams)
        if query_len == 0:
            return None

        best_key: str | None = None
        best_score = self.threshold

        for candidate in self._candidates:
            cand_len = len(candidate.ngrams)
            if cand_len == 0:
                continue
            # Necessary condition for Jaccard(A, B) >= threshold.
            min_len = min(query_len, cand_len)
            max_len = max(query_len, cand_len)
            if min_len < best_score * max_len:
                continue

            score = jaccard_similarity(query_grams, candidate.ngrams)
            if score >= best_score and (best_key is None or score > best_score):
                best_score = score
                best_key = candidate.key

        if best_key is None:
            return None
        return best_key, best_score
