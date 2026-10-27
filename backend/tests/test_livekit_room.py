"""Opt-in real server API test; only a UUID-named test room is touched."""

import os
import unittest
from uuid import uuid4

from aiohttp import ClientTimeout
from livekit import api

from app.core.config import get_settings
from app.services.interviews.livekit import close_room, livekit_credentials


@unittest.skipUnless(os.environ.get("RUN_LIVEKIT_TESTS") == "1", "Set RUN_LIVEKIT_TESTS=1")
class LiveKitRoomTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_room_creation_closure_and_repeated_closure(self):
        settings = get_settings()
        key, secret = livekit_credentials(settings)
        room_name = f"verification-{uuid4()}"
        # If an assertion fails after creation, still clean up this test room.
        self.addAsyncCleanup(close_room, room_name)

        async with api.LiveKitAPI(
            url=settings.livekit_api_url,
            api_key=key,
            api_secret=secret,
            timeout=ClientTimeout(total=settings.livekit_api_timeout_seconds),
        ) as client:
            created = await client.room.create_room(api.CreateRoomRequest(name=room_name))
            self.assertEqual(created.name, room_name)
            await close_room(room_name)
            rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[room_name]))
            self.assertEqual(len(rooms.rooms), 0)
            await close_room(room_name)


if __name__ == "__main__":
    unittest.main()
