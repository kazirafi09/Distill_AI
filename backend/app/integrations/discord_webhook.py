import json
import logging
import re

import httpx

from app.core.config import get_settings


# HTTPX's INFO request logs include URLs. Webhook URLs contain secrets.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class DiscordWebhookError(Exception):
    def __init__(self, code: str, *, uncertain: bool = False):
        self.code = code
        self.uncertain = uncertain
        super().__init__(code)


def get_webhook_url() -> str:
    settings = get_settings()
    secret = settings.discord_webhook_url

    if secret is None or not secret.get_secret_value().strip():
        raise DiscordWebhookError("DISCORD_NOT_CONFIGURED")

    url = secret.get_secret_value().strip()

    if not re.fullmatch(
        r"https://discord\.com/api(?:/v\d+)?/webhooks/"
        r"\d+/[A-Za-z0-9._-]+",
        url,
    ):
        raise DiscordWebhookError("DISCORD_INVALID_WEBHOOK_URL")

    return url


def is_snowflake(value: object) -> bool:
    return isinstance(value, str) and value.isdigit()


def send_discord_job(
    *,
    number: int,
    title: str,
    content: str,
) -> dict[str, str]:
    """Send a saved JD. Posting records are handled by the calling service."""
    settings = get_settings()
    url = get_webhook_url()

    if not content.strip():
        raise DiscordWebhookError("DISCORD_EMPTY_JOB_DESCRIPTION")

    heading = f"**Job #{number}: {title}**"
    full_content = f"{heading}\n\n{content}"

    payload = {
        "content": full_content,
        "allowed_mentions": {"parse": []},
    }

    # Count UTF-16 units conservatively for Discord's content limit.
    attach_document = (
        len(full_content.encode("utf-16-le")) // 2 > 2000
    )

    if attach_document:
        payload["content"] = (
            f"{heading}\n\n"
            "The full job description is attached."
        )

    document_bytes = content.encode("utf-8")

    if len(document_bytes) > 7 * 1024 * 1024:
        raise DiscordWebhookError("DISCORD_DOCUMENT_TOO_LARGE")

    with httpx.Client(
        timeout=settings.discord_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "DistillAI/1.0"},
    ) as client:
        # Read server/channel metadata before attempting to send.
        try:
            metadata_response = client.get(url)
        except httpx.RequestError:
            raise DiscordWebhookError(
                "DISCORD_WEBHOOK_LOOKUP_FAILED"
            ) from None

        if metadata_response.status_code != 200:
            raise DiscordWebhookError(
                f"DISCORD_WEBHOOK_HTTP_{metadata_response.status_code}"
            )

        try:
            metadata = metadata_response.json()
            guild_id = metadata["guild_id"]
            channel_id = metadata["channel_id"]

            if (
                metadata.get("type") != 1
                or not is_snowflake(guild_id)
                or not is_snowflake(channel_id)
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise DiscordWebhookError(
                "DISCORD_INVALID_WEBHOOK_METADATA"
            ) from None

        try:
            if attach_document:
                response = client.post(
                    url,
                    params={"wait": "true"},
                    data={
                        "payload_json": json.dumps(
                            payload,
                            ensure_ascii=False,
                        ),
                    },
                    files={
                        "files[0]": (
                            f"job-{number}.md",
                            document_bytes,
                            "text/markdown",
                        ),
                    },
                )
            else:
                response = client.post(
                    url,
                    params={"wait": "true"},
                    json=payload,
                )
        except httpx.RequestError:
            # Discord may have received the message before the error.
            raise DiscordWebhookError(
                "DISCORD_SEND_OUTCOME_UNKNOWN",
                uncertain=True,
            ) from None

        if not 200 <= response.status_code < 300:
            raise DiscordWebhookError(
                f"DISCORD_SEND_HTTP_{response.status_code}",
                uncertain=response.status_code >= 500,
            )

        try:
            message = response.json()
            message_id = message["id"]

            if (
                not is_snowflake(message_id)
                or message.get("channel_id") != channel_id
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise DiscordWebhookError(
                "DISCORD_INVALID_SEND_RESPONSE",
                uncertain=True,
            ) from None

    return {
        "channel_id": channel_id,
        "message_id": message_id,
        "message_url": (
            f"https://discord.com/channels/"
            f"{guild_id}/{channel_id}/{message_id}"
        ),
    }