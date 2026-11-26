"""Plant Light Scheduler integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from homeassistant import config_entries
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    CARD_URL,
    DATA_ENERGY,
    DATA_SCHEDULER,
    DOMAIN,
    ENERGY_CARD_URL,
    VERSION,
)
from .energy import PlantLightEnergy
from .energy_websocket import async_register_energy_commands
from .scheduler import PlantLightScheduler
from .websocket_api import async_register_websocket_commands

type PlantLightSchedulerConfigEntry = config_entries.ConfigEntry[dict[str, Any]]


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Set up frontend assets and WebSocket commands."""
    hass.data.setdefault(DOMAIN, {})
    frontend = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url, str(frontend / url.rsplit("/", 1)[1]), cache_headers=False
            )
            for url in (CARD_URL, ENERGY_CARD_URL)
        ]
    )
    # Auto-load the card for a frictionless install. It can also be added as a
    # dashboard resource manually; that loading path is documented in README.
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    add_extra_js_url(hass, f"{ENERGY_CARD_URL}?v={VERSION}")
    async_register_websocket_commands(hass)
    async_register_energy_commands(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, _entry: PlantLightSchedulerConfigEntry
) -> bool:
    """Set up the persistent scheduler."""
    scheduler = PlantLightScheduler(hass)
    await scheduler.async_load()
    energy = PlantLightEnergy(hass, scheduler)
    await energy.async_start()
    hass.data[DOMAIN][DATA_ENERGY] = energy
    hass.data[DOMAIN][DATA_SCHEDULER] = scheduler
    await scheduler.async_start()
    return True


async def async_unload_entry(
    hass: HomeAssistant, _entry: PlantLightSchedulerConfigEntry
) -> bool:
    """Unload the scheduler config entry."""
    scheduler: PlantLightScheduler | None = hass.data[DOMAIN].pop(DATA_SCHEDULER, None)
    if scheduler:
        scheduler.async_stop()
    if energy := hass.data[DOMAIN].pop(DATA_ENERGY, None):
        await energy.async_stop()
    return True
