"""Text embeddings behind a small interface.

``HashingEmbedder`` is a deterministic, dependency-free *lexical* embedder (feature
hashing of normalized words, bigrams and identifiers into 384 dimensions). It keeps the
whole platform runnable offline and in CI with byte-identical results.

Trade-off, stated plainly: it matches words, not meaning ("install failed" matches
"installation failure" thanks to light stemming, but not "provisioning error").
Production would plug in a semantic model behind the same ``Embedder`` protocol. The
vector dimension is part of the database schema (vector(384)), so switching models
means a migration plus re-embedding every chunk - worth knowing before a customer
asks to "just swap the embedding model".
"""

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

from packages.core.models import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_STOPWORDS = frozenset(
    """a an the and or but if then so of to in on at by for with from into over under is are was were be
    been being it its this that these those what which who whom how why when where do does did doing have has
    had having i me my we our you your he she they them their there here not no yes can could should would
    will just than too very also about as up down out off again further once all any both each few more most
    other some such only own same s t don now please need needs""".split()
)
_SUFFIXES = ("ations", "ation", "ings", "ing", "edly", "ed", "ers", "er", "s")


def normalize(word: str) -> str:
    """Tiny suffix stripper: 'installation' -> 'install', 'failed' -> 'fail'.

    It only has to be *consistent* (query and document words normalize the same way),
    not linguistically correct.
    """
    if "-" in word or any(ch.isdigit() for ch in word):
        return word  # identifiers like e-act-401 or c-104 stay exact
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def terms(text: str) -> list[str]:
    return [normalize(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    name = "hashing-v1"
    dim = EMBEDDING_DIM

    def _index(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def embed_one(self, text: str) -> list[float]:
        words = terms(text)
        features: Counter[str] = Counter(words)
        features.update(f"{a}_{b}" for a, b in zip(words, words[1:], strict=False))
        vec = [0.0] * self.dim
        for feature, count in features.items():
            idx, sign = self._index(feature)
            weight = 1.0 + math.log(count)
            if "-" in feature:  # identifiers (error codes, customer ids) are strong signals
                weight *= 2.0
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


_default = HashingEmbedder()


def get_embedder() -> Embedder:
    return _default
