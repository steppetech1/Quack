"""`EmbeddingClient` (Т-§10.5, ТЗ Фазы 4 WP20.3).

Seam S1: interface and `EmbeddingsUnavailable`; seam S12: `embed_query` for free-text search.
Vectors have `Settings.embedding_dim` components. A query gets the task instruction of the
model card, documents do not (Т-§10.5). The test double is
`tests.support.embeddings.FakeEmbeddingClient`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from quack.config import Settings


class EmbeddingsUnavailable(Exception):
    """Modal does not answer: jobs wait, free-text search is off (Т-§20.7)."""


@runtime_checkable
class EmbeddingClient(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def aclose(self) -> None: ...


class ModalEmbeddingClient:
    """`EmbeddingClient` calling the Modal application of `modal/` (WP20.3)."""

    def __init__(self, *, token_id: str, token_secret: str, dim: int) -> None:
        self._token_id = token_id
        self._token_secret = token_secret
        self._dim = dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def create_embedding_client(settings: Settings) -> EmbeddingClient:
    """`EmbeddingClient` on Modal (Т-§10.5); for `api` and `workers`."""
    return ModalEmbeddingClient(
        token_id=settings.modal_token_id.get_secret_value(),
        token_secret=settings.modal_token_secret.get_secret_value(),
        dim=settings.embedding_dim,
    )
