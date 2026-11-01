"""Seam S1 of stream C (ТЗ Фазы 4 §4.2, §7.2, §8.1): resource fields, client interfaces, doubles."""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from quack.api.email import EmailSender, EmailUnavailable, ResendEmailSender, create_email_sender
from quack.api.resources import AppResources
from quack.config import Settings
from quack.embeddings.client import (
    EmbeddingClient,
    EmbeddingsUnavailable,
    ModalEmbeddingClient,
    create_embedding_client,
)
from quack.storage.deletions import (
    DeletionJournal,
    DeletionJournalUnavailable,
    DeletionRecord,
    R2DeletionJournal,
    create_deletion_journal,
)
from quack.storage.objects import (
    ObjectStore,
    S3ObjectStore,
    StorageUnavailable,
    create_object_store,
    learner_prefix,
)
from tests.support.deletions import FakeDeletionJournal
from tests.support.email import FakeEmailSender
from tests.support.embeddings import FakeEmbeddingClient
from tests.support.storage import BrokenObjectStore, InMemoryObjectStore


def test_app_resources_fields() -> None:
    # §4.2 contract minus `db` (S2, stream A, WP1.2); `engine` and `session_factory` leave in WP1.3.
    names = {f.name for f in dataclasses.fields(AppResources)}
    assert names == {
        "settings",
        "engine",
        "session_factory",
        "neo4j",
        "redis",
        "llm",
        "compute",
        "storage",
        "embeddings",
        "email",
        "deletions",
    }


def test_production_clients_and_doubles_implement_the_interfaces() -> None:
    settings = Settings()
    assert isinstance(create_object_store(settings), S3ObjectStore)
    assert isinstance(create_embedding_client(settings), ModalEmbeddingClient)
    assert isinstance(create_email_sender(settings), ResendEmailSender)
    assert isinstance(create_deletion_journal(settings), R2DeletionJournal)
    for store in (create_object_store(settings), InMemoryObjectStore(), BrokenObjectStore()):
        assert isinstance(store, ObjectStore)
    for client in (create_embedding_client(settings), FakeEmbeddingClient(settings.embedding_dim)):
        assert isinstance(client, EmbeddingClient)
    for sender in (create_email_sender(settings), FakeEmailSender()):
        assert isinstance(sender, EmailSender)
    for journal in (create_deletion_journal(settings), FakeDeletionJournal()):
        assert isinstance(journal, DeletionJournal)


def test_new_secrets_are_masked() -> None:
    settings = Settings(
        s3_access_key=SecretStr("s3-access-value"),
        deletions_s3_secret_key=SecretStr("r2-secret-value"),
        email_api_key=SecretStr("resend-key-value"),
        modal_token_secret=SecretStr("modal-secret-value"),
        google_oidc_client_secret=SecretStr("google-secret-value"),
    )
    masked = settings.secret_values()
    for value in (
        "s3-access-value",
        "r2-secret-value",
        "resend-key-value",
        "modal-secret-value",
        "google-secret-value",
    ):
        assert value in masked


async def test_in_memory_object_store_keeps_objects_immutable() -> None:
    store = InMemoryObjectStore()
    learner = uuid4()
    prefix = learner_prefix(learner)
    await store.put(f"{prefix}materials/m/original.pdf", b"%PDF-1.7", "application/pdf")
    await store.put(f"{prefix}photos/p/original-01J.webp", b"RIFF", "image/webp")
    await store.put(f"learners/{uuid4()}/photos/x/original-01K.webp", b"RIFF", "image/webp")
    with pytest.raises(ValueError):
        await store.put(f"{prefix}materials/m/original.pdf", b"other", "application/pdf")
    assert await store.get(f"{prefix}materials/m/original.pdf") == b"%PDF-1.7"
    assert [o.key for o in await store.list_objects(prefix)] == [
        f"{prefix}materials/m/original.pdf",
        f"{prefix}photos/p/original-01J.webp",
    ]
    assert await store.delete_prefix(prefix) == 2
    assert await store.list_objects(prefix) == []
    assert len(store.objects) == 1


async def test_broken_object_store_is_unavailable() -> None:
    with pytest.raises(StorageUnavailable):
        await BrokenObjectStore().put("learners/x/a", b"", "application/pdf")


async def test_fake_embeddings_are_deterministic_unit_vectors() -> None:
    client = FakeEmbeddingClient(768)
    first, second = await client.embed_documents(["integral", "derivative"])
    assert len(first) == 768
    assert math.isclose(sum(v * v for v in first), 1.0, rel_tol=1e-9)
    assert await client.embed_query("integral") == first
    assert first != second
    assert client.calls == 3
    client.available = False
    with pytest.raises(EmbeddingsUnavailable):
        await client.embed_query("integral")


async def test_fake_email_sender_logs_links_and_can_be_down() -> None:
    sender = FakeEmailSender()
    await sender.send_login_link(to="a@example.com", link="https://api/auth/link/verify?token=1")
    assert sender.last_link("a@example.com").endswith("token=1")
    sender.available = False
    with pytest.raises(EmailUnavailable):
        await sender.send_login_link(to="a@example.com", link="x")


async def test_fake_deletion_journal_lists_since_and_can_be_down() -> None:
    journal = FakeDeletionJournal()
    now = datetime.now(UTC)
    old = DeletionRecord(uuid4(), uuid4(), "topic", uuid4(), now - timedelta(days=2))
    new = DeletionRecord(uuid4(), uuid4(), "account", uuid4(), now)
    await journal.write(new)
    await journal.write(old)
    assert await journal.list_since(now - timedelta(days=1)) == [new]
    journal.available = False
    with pytest.raises(DeletionJournalUnavailable):
        await journal.write(old)
