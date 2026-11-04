"""Team chat webhook for alerts (Д-§9.3, ТЗ Фазы 4 WP9.5).

The documents do not choose the chat (Д-§14.3: "канал не выбран"), so the body shape is a setting
`alert_webhook_format`, not a constant: `slack` (also Mattermost, Rocket.Chat) sends `{"text"}`,
`discord` sends `{"content"}`, `telegram` sends `{"chat_id", "text"}` to the `sendMessage` URL.
"""

from __future__ import annotations

from typing import Any

import httpx

from quack.config import Settings
from quack.logging import get_logger

log = get_logger(__name__)

HTTP_TIMEOUT_S = 10.0


def resolve_format(settings: Settings) -> str:
    fmt = settings.alert_webhook_format
    url = settings.alert_webhook_url.get_secret_value()
    token = settings.telegram_bot_token.get_secret_value()
    if (token or "api.telegram.org" in url) and fmt == "slack":
        return "telegram"
    return fmt


def alert_url(settings: Settings) -> str:
    url = settings.alert_webhook_url.get_secret_value()
    if not url:
        token = settings.telegram_bot_token.get_secret_value()
        if token:
            return f"https://api.telegram.org/bot{token}/sendMessage"
    return url


def format_message(message: str, fmt: str) -> str:
    if fmt == "telegram":
        if message.startswith("CRITICAL:") and not message.startswith("🚨"):
            message = "🚨 " + message
        elif message.startswith("WARNING:") and not message.startswith("⚠️"):
            message = "⚠️ " + message
        if len(message) > 4000:
            message = message[:3997] + "..."
    return message


def alert_payload(settings: Settings, message: str) -> dict[str, Any]:
    fmt = resolve_format(settings)
    formatted = format_message(message, fmt)
    match fmt:
        case "slack":
            return {"text": formatted}
        case "discord":
            return {"content": formatted}
        case "telegram":
            chat_id = settings.alert_webhook_chat_id or settings.telegram_chat_id
            if not chat_id:
                raise ValueError("ALERT_WEBHOOK_CHAT_ID is required for the telegram format")
            return {"chat_id": chat_id, "text": formatted}
        case _:
            return {"text": formatted}


async def post_alert(settings: Settings, message: str) -> None:
    """Send `message` to the team chat; no-op without alert URL or bot token."""
    url = alert_url(settings)
    if not url:
        return
    payload = alert_payload(settings, message)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
        try:
            resp = await http.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error(
                "alert_webhook_http_error",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:500],
            )
            raise
