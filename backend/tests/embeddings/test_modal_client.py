"""`ModalEmbeddingClient` (ТЗ Ф4 WP20.3; Т-§10.5, Т-§20.7). Modal itself is not called: the
remote method is replaced by a recording double; the names the client looks up are compared
with the Modal application `modal/embeddings.py`."""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path

import modal.exception
import pytest
from pydantic import SecretStr

from quack.config import Settings
from quack.embeddings.client import (
    EMBEDDER_CLASS,
    EMBEDDING_APP,
    EMBEDDING_MODEL,
    EmbeddingsUnavailable,
    ModalEmbeddingClient,
    create_embedding_client,
)

MODAL_APP = Path(__file__).resolve().parents[3] / "modal" / "embeddings.py"
DIM = 4


class Recorder:
    """The remote `embed(texts, prompt, dim)`: records calls, answers unit-ish vectors."""

    def __init__(self, *, dim: int = DIM, error: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], str | None, int]] = []
        self._dim = dim
        self._error = error

    async def __call__(
        self, texts: Sequence[str], prompt: str | None, dim: int
    ) -> list[list[float]]:
        self.calls.append((list(texts), prompt, dim))
        if self._error is not None:
            raise self._error
        return [[float(i)] * self._dim for i, _ in enumerate(texts)]


def _client(remote: Recorder, instruction: str = "Find the passage") -> ModalEmbeddingClient:
    return ModalEmbeddingClient(
        token_id="id", token_secret="secret", dim=DIM, query_instruction=instruction, embed=remote
    )


async def test_query_gets_the_instruction_documents_do_not() -> None:
    """Т-§10.5: «к запросу добавляется инструкция задачи, как рекомендует карточка модели;
    к документам — нет». The card's format: `Instruct: {task}\\nQuery:{query}`."""
    remote = Recorder()
    client = _client(remote, "Find the passage")

    vectors = await client.embed_documents(["Limits", "Derivatives"])
    query = await client.embed_query("what is a limit")

    assert len(vectors) == 2 and all(len(v) == DIM for v in vectors) and len(query) == DIM
    assert remote.calls == [
        (["Limits", "Derivatives"], None, DIM),
        (["what is a limit"], "Instruct: Find the passage\nQuery:", DIM),
    ]


async def test_no_texts_no_call() -> None:
    remote = Recorder()
    assert await _client(remote).embed_documents([]) == []
    assert remote.calls == []


async def test_modal_down_is_unavailable() -> None:
    """Т-§20.7: Modal does not answer → `EmbeddingsUnavailable` (jobs wait, search is off)."""
    for error in (
        modal.exception.ConnectionError("down"),
        modal.exception.FunctionTimeoutError("slow"),
        modal.exception.NotFoundError("not deployed"),
        TimeoutError(),
    ):
        with pytest.raises(EmbeddingsUnavailable):
            await _client(Recorder(error=error)).embed_query("x")


async def test_without_tokens_is_unavailable(settings: Settings) -> None:
    """No `MODAL_TOKEN_*` (dev, CI): the client exists, calls are unavailable."""
    client = create_embedding_client(settings.model_copy(update={"modal_token_id": SecretStr("")}))
    with pytest.raises(EmbeddingsUnavailable):
        await client.embed_documents(["x"])


async def test_wrong_dimension_is_an_error() -> None:
    """Vectors must have `embedding_dim` components (Т-§3.4.6): a deployed app with another
    size is a defect, not an outage."""
    with pytest.raises(ValueError, match="dimension"):
        await _client(Recorder(dim=DIM + 1)).embed_documents(["x"])


def test_client_looks_up_the_names_of_the_modal_app() -> None:
    """The client and `modal/embeddings.py` agree on the app and the class names."""
    spec = importlib.util.spec_from_file_location("quack_modal_embeddings", MODAL_APP)
    assert spec is not None and spec.loader is not None
    app_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app_module)
    assert app_module.app.name == EMBEDDING_APP
    assert hasattr(app_module, EMBEDDER_CLASS)
    assert app_module.MODEL == EMBEDDING_MODEL
