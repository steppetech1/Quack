"""The API process talks to the model through the Responses API adapter (ТЗ Фазы 3 WP5.7, §4.2)."""

from __future__ import annotations

from pydantic import SecretStr

from quack.api.resources import create_app_resources
from quack.config import Settings
from quack.llm.responses_client import ResponsesClient
from quack.llm.tracing import LangSmithTracer, NullTracer


async def test_api_resources_use_responses_client(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"meta_api_base_url": "http://meta-mock:8080", "redis_url": settings.redis_url}
    )
    resources = await create_app_resources(configured)
    try:
        provider = resources.llm.provider
        assert isinstance(provider, ResponsesClient)
        assert provider.base_url == "http://meta-mock:8080"
    finally:
        await resources.close()


async def test_api_gateway_gets_the_tracer_of_the_settings(settings: Settings) -> None:
    """WP10.6: the key reaches the gateway as a tracer in `api`; without a key it is off."""
    traced = settings.model_copy(
        update={
            "env": "dev",
            "process_role": "api",
            "langsmith_api_key": SecretStr("ls-test-key"),
        }
    )
    resources = await create_app_resources(traced)
    try:
        assert isinstance(resources.llm.tracer, LangSmithTracer)
    finally:
        await resources.close()

    resources = await create_app_resources(
        traced.model_copy(update={"langsmith_api_key": SecretStr("")})
    )
    try:
        assert isinstance(resources.llm.tracer, NullTracer)
    finally:
        await resources.close()
