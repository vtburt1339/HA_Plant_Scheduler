"""WebSocket commands for scheduler power readings and energy settings."""

from __future__ import annotations

from typing import Any

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api as ws
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DATA_POWER, DOMAIN
from .power import PlantLightPower

PREFIX = f"{DOMAIN}/energy"


def async_register_energy_commands(hass: HomeAssistant) -> None:
    """Register the dashboard's report and admin actions."""
    for handler in (ws_get, ws_set_rate, ws_set_source):
        ws.async_register_command(hass, handler)


def _power(hass: HomeAssistant) -> PlantLightPower:
    if (power := hass.data.get(DOMAIN, {}).get(DATA_POWER)) is None:
        raise HomeAssistantError("Set up Plant Light Scheduler to view power readings")
    return power


@ws.websocket_command({"type": f"{PREFIX}/get"})
@callback
def ws_get(
    hass: HomeAssistant, connection: ws.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the energy report filtered by entity read access."""
    user = connection.user

    def can_read(entity_id: str) -> bool:
        return user.is_admin or user.permissions.check_entity(entity_id, POLICY_READ)

    connection.send_result(msg["id"], _power(hass).report(can_read, user.is_admin))


@ws.require_admin
@ws.websocket_command({"type": f"{PREFIX}/set_rate", "rate": str})
@ws.async_response
async def ws_set_rate(
    hass: HomeAssistant, connection: ws.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Set or clear the energy rate."""
    try:
        if "rate" not in msg:
            raise ValueError("Rate is required")
        await _power(hass).async_set_rate(msg["rate"])
    except ValueError as err:
        connection.send_error(msg["id"], ws.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"])


@ws.require_admin
@ws.websocket_command({"type": f"{PREFIX}/set_sources", "switch": str, "power": str})
@ws.async_response
async def ws_set_source(
    hass: HomeAssistant, connection: ws.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Set the power sensor override for one scheduled switch."""
    try:
        if not {"switch", "power"} <= msg.keys():
            raise ValueError("Choose a switch and a power sensor")
        await _power(hass).async_set_source(msg["switch"], msg["power"])
    except ValueError as err:
        connection.send_error(msg["id"], ws.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"])
