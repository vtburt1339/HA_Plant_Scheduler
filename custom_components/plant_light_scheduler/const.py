"""Constants for Plant Light Scheduler."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "plant_light_scheduler"
NAME: Final = "Plant Light Scheduler"
VERSION: Final = "0.4.1"

DATA_SCHEDULER: Final = "scheduler"
DATA_POWER: Final = "power"
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

CARD_URL: Final = "/plant_light_scheduler/plant-light-scheduler-card.js"
AVATAR_URL: Final = "/plant_light_scheduler/plant-avatars.svg"
AVATAR_COUNT: Final = 20

WS_LIST: Final = f"{DOMAIN}/list"
WS_CREATE: Final = f"{DOMAIN}/create"
WS_UPDATE: Final = f"{DOMAIN}/update"
WS_DELETE: Final = f"{DOMAIN}/delete"

