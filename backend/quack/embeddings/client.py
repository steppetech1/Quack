"""`EmbeddingClient` (Т-§10.5, ТЗ Фазы 4 WP20.3).

Seam S1: interface and `EmbeddingsUnavailable`; seam S12: `embed_query` for free-text search.
Vectors have `Settings.embedding_dim` components. A query gets the task instruction of the
model card, documents do not (Т-§10.5). The test double is
`tests.support.embeddings.FakeEmbeddingClient`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from quack.config import Settings

# The Modal application of `modal/embeddings.py` and its class (D-C-117).
EMBEDDING_APP = "quack-embeddings"
EMBEDDER_CLASS = "Embedder"
# Т-§10.5: the model; `embedding.ready.model` and the idempotency key carry it.
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"

type RemoteEmbed = Callable[[Sequence[str], str | None, int], Awaitable[list[list[float]]]]


class EmbeddingsUnavailable(Exception):
    """Modal does not answer: jobs wait, free-text search is off (Т-§20.7)."""


@runtime_checkable
class EmbeddingClient(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def aclose(self) -> None: ...


def query_prompt(instruction: str) -> str:
    """The prefix of a query by the model card: `Instruct: {task}\\nQuery:{query}`."""
    return f"Instruct: {instruction}\nQuery:"


class ModalEmbeddingClient:
    """`EmbeddingClient` calling the Modal application of `modal/` (WP20.3). `embed` replaces
    the remote method in tests; by default it is `Embedder.embed` of the deployed app, looked
    up with the `MODAL_TOKEN_*` credentials on the first call."""

    def __init__(
        self,
        *,
        token_id: str,
        token_secret: str,
        dim: int,
        query_instruction: str,
        embed: RemoteEmbed | None = None,
    ) -> None:
        self._token_id = token_id
        self._token_secret = token_secret
        self._dim = dim
        self._prompt = query_prompt(query_instruction)
        self._embed = embed

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._call(texts, None)

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self._call([text], self._prompt)
        return vector

    async def aclose(self) -> None:
        return None

    async def _call(self, texts: Sequence[str], prompt: str | None) -> list[list[float]]:
        import modal.exception

        try:
            vectors = await self._remote()(list(texts), prompt, self._dim)
        except (modal.exception.Error, ConnectionError, TimeoutError) as exc:
            raise EmbeddingsUnavailable(f"Modal embeddings: {exc!r}") from exc
        if len(vectors) != len(texts) or any(len(v) != self._dim for v in vectors):
            raise ValueError(f"Modal answered vectors of another dimension than {self._dim}")
        return vectors

    def _remote(self) -> RemoteEmbed:
        if self._embed is None:
            if not (self._token_id and self._token_secret):
                raise EmbeddingsUnavailable("MODAL_TOKEN_ID / MODAL_TOKEN_SECRET are not set")
            self._embed = _modal_embed(self._token_id, self._token_secret)
        return self._embed


def _modal_embed(token_id: str, token_secret: str) -> RemoteEmbed:
    import modal

    client = modal.Client.from_credentials(token_id, token_secret)
    embedder: Any = modal.Cls.from_name(EMBEDDING_APP, EMBEDDER_CLASS, client=client)()

    async def embed(texts: Sequence[str], prompt: str | None, dim: int) -> list[list[float]]:
        return await embedder.embed.remote.aio(list(texts), prompt, dim)

    return embed


def create_embedding_client(settings: Settings) -> EmbeddingClient:
    """`EmbeddingClient` on Modal (Т-§10.5); for `api` and `workers`."""
    return ModalEmbeddingClient(
        token_id=settings.modal_token_id.get_secret_value(),
        token_secret=settings.modal_token_secret.get_secret_value(),
        dim=settings.embedding_dim,
        query_instruction=settings.embedding_query_instruction,
    )
