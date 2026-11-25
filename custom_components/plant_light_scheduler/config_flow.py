"""Config flow for Plant Light Scheduler."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN, NAME


class PlantLightSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single Plant Light Scheduler config entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle setup from the integrations page."""
        return self.async_create_entry(title=NAME, data={})

