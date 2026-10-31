"""The API process talks to the model through the Responses API adapter (ТЗ Фазы 3 WP5.7, §4.2)."""

from __future__ import annotations

from quack.api.resources import create_app_resources
from quack.config import Settings
from quack.llm.responses_client import ResponsesClient


async def test_api_resources_use_responses_client(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"meta_api_base_url": "http://meta-mock:8080", "redis_url": settings.redis_url}
    )
    resources = create_app_resources(configured)
    try:
        provider = resources.llm.provider
        assert isinstance(provider, ResponsesClient)
        assert provider.base_url == "http://meta-mock:8080"
    finally:
        await resources.close()
