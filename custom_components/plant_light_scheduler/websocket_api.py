"""WebSocket commands used by the Plant Light Scheduler card."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import (
    DATA_SCHEDULER,
    DOMAIN,
    WS_CREATE,
    WS_DELETE,
    WS_LIST,
    WS_UPDATE,
)
from .logic import validate_time
from .scheduler import PlantLightScheduler

MAX_ENTITIES = 64
SWITCH_ENTITY_PATTERN = re.compile(r"^switch\.[a-z0-9_]+$")
# Home Assistant validates the message shape before calling the handler.
SCHEDULE_FIELDS = {
    "entities": [str],
    "on": str,
    "off": str,
    "enabled": bool,
    "name": str,
}


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register card API commands."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_create)
    websocket_api.async_register_command(hass, ws_update)
    websocket_api.async_register_command(hass, ws_delete)


def _scheduler(hass: HomeAssistant) -> PlantLightScheduler:
    """Return the configured scheduler."""
    scheduler = hass.data.get(DOMAIN, {}).get(DATA_SCHEDULER)
    if scheduler is None:
        raise RuntimeError("Plant Light Scheduler is not configured")
    return scheduler


def _schedule_data(msg: dict[str, Any]) -> dict[str, Any]:
    """Validate schedule data without coupling to a schema library version."""
    raw_entities = msg.get("entities")
    if not isinstance(raw_entities, list) or not raw_entities:
        raise ValueError("Select at least one switch")
    if len(raw_entities) > MAX_ENTITIES:
        raise ValueError(f"A schedule can contain at most {MAX_ENTITIES} switches")

    entities: list[str] = []
    for entity_id in raw_entities:
        if not isinstance(entity_id, str) or not SWITCH_ENTITY_PATTERN.fullmatch(
            entity_id
        ):
            raise ValueError("Schedules may only contain switch entities")
        if entity_id not in entities:
            entities.append(entity_id)

    on_time = validate_time(msg.get("on"))
    off_time = validate_time(msg.get("off"))
    if on_time == off_time:
        raise ValueError("On and off times must be different")

    enabled = msg.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Enabled must be true or false")

    data = {
        "entities": entities,
        "on": on_time,
        "off": off_time,
        "enabled": enabled,
    }
    if "name" in msg:
        name = msg["name"].strip()
        if len(name) > 100:
            raise ValueError("Schedule name must be 100 characters or fewer")
        data["name"] = name
    return data


def _schedule_id(msg: dict[str, Any]) -> str:
    """Validate a schedule ID."""
    schedule_id = msg.get("schedule_id")
    if not isinstance(schedule_id, str) or not schedule_id:
        raise ValueError("schedule_id is required")
    return schedule_id


@websocket_api.websocket_command({"type": WS_LIST})
@callback
def ws_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return all schedules."""
    try:
        schedules = _scheduler(hass).schedules
    except RuntimeError as err:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
        return
    connection.send_result(msg["id"], schedules)


@websocket_api.require_admin
@websocket_api.websocket_command({"type": WS_CREATE, **SCHEDULE_FIELDS})
@websocket_api.async_response
async def ws_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a schedule."""
    try:
        schedule = await _scheduler(hass).async_create(_schedule_data(msg))
    except (RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"], schedule)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {"type": WS_UPDATE, "schedule_id": str, **SCHEDULE_FIELDS}
)
@websocket_api.async_response
async def ws_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update a schedule."""
    try:
        schedule = await _scheduler(hass).async_update(
            _schedule_id(msg), _schedule_data(msg)
        )
    except KeyError:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "Schedule not found"
        )
        return
    except (RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"], schedule)


@websocket_api.require_admin
@websocket_api.websocket_command({"type": WS_DELETE, "schedule_id": str})
@websocket_api.async_response
async def ws_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a schedule."""
    try:
        await _scheduler(hass).async_delete(_schedule_id(msg))
    except KeyError:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "Schedule not found"
        )
        return
    except (RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"], {"success": True})

