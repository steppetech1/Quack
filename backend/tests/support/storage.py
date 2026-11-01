"""Object storage doubles (ТЗ Фазы 4 §8.1, seam S1): `InMemoryObjectStore` and the broken one."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from quack.storage.objects import ObjectInfo, StorageUnavailable


class InMemoryObjectStore:
    """`ObjectStore` in a dict. Keys are immutable as in Т-§3.3: `put` on an existing key fails."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, datetime]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if key in self.objects:
            raise ValueError(f"object key already exists: {key}")
        self.objects[key] = (data, content_type, datetime.now(UTC))

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def presigned_get_url(self, key: str, ttl_s: int) -> str:
        if key not in self.objects:
            raise KeyError(key)
        return f"memory://quack-learners/{key}?ttl={ttl_s}"

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return [
            ObjectInfo(key=key, size=len(data), last_modified=modified)
            for key, (data, _, modified) in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    async def delete_keys(self, keys: Sequence[str]) -> None:
        for key in keys:
            self.objects.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.objects if key.startswith(prefix)]
        await self.delete_keys(keys)
        return len(keys)

    async def aclose(self) -> None:
        return None


class BrokenObjectStore:
    """`ObjectStore` whose every call raises `StorageUnavailable` (Т-§20.7, `T-ST3`)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise StorageUnavailable("broken object store")

    async def get(self, key: str) -> bytes:
        raise StorageUnavailable("broken object store")

    async def presigned_get_url(self, key: str, ttl_s: int) -> str:
        raise StorageUnavailable("broken object store")

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        raise StorageUnavailable("broken object store")

    async def delete_keys(self, keys: Sequence[str]) -> None:
        raise StorageUnavailable("broken object store")

    async def delete_prefix(self, prefix: str) -> int:
        raise StorageUnavailable("broken object store")

    async def aclose(self) -> None:
        return None
