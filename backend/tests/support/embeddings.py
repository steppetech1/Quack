"""`FakeEmbeddingClient` (ТЗ Фазы 4 §8.1, seam S1): deterministic vectors by text hash."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from quack.embeddings.client import EmbeddingsUnavailable


class FakeEmbeddingClient:
    """Unit vectors of `dim` components from SHA-256 of the text; the same text gives the same
    vector. `available = False` makes every call raise `EmbeddingsUnavailable` (`T-MT4`).
    `calls` counts texts sent, so a test can prove rebuild does not call Modal (`T-MT2`)."""

    def __init__(self, dim: int, *, available: bool = True) -> None:
        self.dim = dim
        self.available = available
        self.calls = 0

    def vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        values = values[: self.dim]
        norm = math.sqrt(sum(v * v for v in values))
        return [v / norm for v in values]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.available:
            raise EmbeddingsUnavailable("fake embeddings are down")
        self.calls += len(texts)
        return [self.vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def aclose(self) -> None:
        return None
