"""Regression test for the dashboard's schedule commands in Home Assistant."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest


def test_dashboard_schedule_lifecycle(tmp_path: Path) -> None:
    """Dispatch the card's payloads through Home Assistant's WebSocket validators."""
    pytest.importorskip("homeassistant")
    from homeassistant.components.websocket_api import ActiveConnection
    from homeassistant.core import HomeAssistant

    from custom_components.plant_light_scheduler.const import DATA_SCHEDULER, DOMAIN
    from custom_components.plant_light_scheduler.scheduler import PlantLightScheduler
    from custom_components.plant_light_scheduler.websocket_api import (
        async_register_websocket_commands,
    )

    async def exercise_commands() -> None:
        hass = HomeAssistant(str(tmp_path))
        scheduler = PlantLightScheduler(hass)
        hass.data[DOMAIN] = {DATA_SCHEDULER: scheduler}
        async_register_websocket_commands(hass)
        replies: asyncio.Queue = asyncio.Queue()
        connection = ActiveConnection(
            logging.getLogger(__name__),
            hass,
            replies.put_nowait,
            SimpleNamespace(name="Test admin", is_admin=True),
            None,
            None,
        )

        async def send(command: str, *, error: str | None = None, **fields: Any) -> Any:
            msg_id = connection.last_id + 1
            connection.async_handle(
                {"id": msg_id, "type": f"{DOMAIN}/{command}", **fields}
            )
            raw_reply = await asyncio.wait_for(replies.get(), timeout=5)
            reply = raw_reply if isinstance(raw_reply, dict) else json.loads(raw_reply)
            assert reply["id"] == msg_id
            if error is not None:
                assert not reply["success"]
                assert reply["error"]["code"] == "invalid_format"
                assert error in reply["error"]["message"]
                return None
            assert reply["success"], reply.get("error")
            return reply["result"]

        # Exercise real persistence without calling any switch services.
        with patch.object(scheduler, "async_reconcile"):
            fields = {
                "entities": ["switch.plant_lights_north"],
                "on": "08:00",
                "off": "21:00",
                "enabled": True,
            }
            created = await send("create", **fields)
            assert 0 <= created["avatar"] < 20
            assert created == {
                "id": created["id"],
                "avatar": created["avatar"],
                **fields,
            }
            assert await send("list") == [created]

            named = await send(
                "update", schedule_id=created["id"], name="  Grow lights  ", **fields
            )
            assert named == {**created, "name": "Grow lights"}
            changed = {**fields, "off": "20:00", "enabled": False}
            updated = await send("update", schedule_id=created["id"], **changed)
            assert updated == {**named, **changed}
            reloaded = PlantLightScheduler(hass)
            await reloaded.async_load()
            assert reloaded.schedules == [updated]

            # A paused schedule still reserves its switches.
            conflict = "Already in use by another schedule: switch.plant_lights_north"
            await send("create", error=conflict, **fields)
            south = {**fields, "entities": ["switch.plant_lights_south"]}
            second = await send("create", name="South lights", **south)
            assert second["avatar"] != created["avatar"]
            assert second == {
                "id": second["id"],
                "name": "South lights",
                "avatar": second["avatar"],
                **south,
            }
            second = await send(
                "update", schedule_id=second["id"], avatar=created["avatar"], **south
            )
            assert second["avatar"] == created["avatar"]
            await send(
                "update",
                schedule_id=second["id"],
                avatar=20,
                error="Choose an avatar",
                **south,
            )
            both = {**fields, "entities": fields["entities"] + south["entities"]}
            await send("update", schedule_id=second["id"], error=conflict, **both)
            assert await send("list") == [updated, second]

            assert await send("delete", schedule_id=created["id"]) == {"success": True}
            reassigned = await send(
                "update", schedule_id=second["id"], name=" ", **both
            )
            assert reassigned == {
                "id": second["id"],
                "avatar": second["avatar"],
                **both,
            }
            assert await send("delete", schedule_id=second["id"]) == {"success": True}
            assert await send("list") == []

    asyncio.run(exercise_commands())
