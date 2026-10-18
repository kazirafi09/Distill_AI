"""LiveKit token generation and room closure."""

from datetime import timedelta

from aiohttp import ClientTimeout
from fastapi import HTTPException
from livekit import api

from app.core.config import Settings, get_settings


def livekit_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise HTTPException(503, "LiveKit is not configured.")

    secret = settings.livekit_api_secret.get_secret_value()
    if not secret:
        raise HTTPException(503, "LiveKit is not configured.")

    return settings.livekit_api_key, secret


def create_join_token(room_name: str, participant_identity: str) -> dict:
    """Call only after the session service authorizes this participant."""
    settings = get_settings()
    key, secret = livekit_credentials(settings)

    token = (
        api.AccessToken(key, secret)
        .with_identity(participant_identity)
        .with_ttl(timedelta(seconds=settings.livekit_token_ttl_seconds))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_publish_sources=["camera", "microphone"],
                can_subscribe=True,
                can_publish_data=False,
                can_update_own_metadata=False,
            )
        )
        .to_jwt()
    )

    return {
        "server_url": settings.livekit_ws_url,
        "participant_token": token,
    }


async def close_room(room_name: str) -> None:
    """Disconnect participants by deleting the interview room."""
    settings = get_settings()
    key, secret = livekit_credentials(settings)

    async with api.LiveKitAPI(
        url=settings.livekit_api_url,
        api_key=key,
        api_secret=secret,
        timeout=ClientTimeout(total=settings.livekit_api_timeout_seconds),
    ) as client:
        try:
            await client.room.delete_room(
                api.DeleteRoomRequest(room=room_name)
            )
        except api.TwirpError as error:
            # Repeated closure is safe if the room is already gone.
            if error.code != "not_found":
                raise
