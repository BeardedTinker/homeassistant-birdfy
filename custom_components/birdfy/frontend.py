"""Serve and register the bundled Birdfy Moments card."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_VERSION = "1.2.2"
CARD_URL_PATH = "/birdfy/birdfy-moments-card.js"
_REGISTERED = f"{DOMAIN}_frontend_registered"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and register one versioned Lovelace resource."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True

    card_path = Path(__file__).parent / "www" / "birdfy-moments-card.js"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(card_path), True)]
    )

    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
        from homeassistant.components.lovelace.resources import (
            ResourceStorageCollection,
        )
    except ImportError:  # pragma: no cover - Lovelace ships with Home Assistant
        _LOGGER.warning(
            "Lovelace is unavailable; Birdfy card was not registered"
        )
        return

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.warning(
            "Lovelace is not initialized; Birdfy card was not registered"
        )
        return

    resources = lovelace_data.resources
    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.warning(
            "Lovelace uses YAML resources; add %s?v=%s as a module manually",
            CARD_URL_PATH,
            CARD_VERSION,
        )
        return

    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True

    url = f"{CARD_URL_PATH}?v={CARD_VERSION}"
    for item in resources.async_items():
        if item["url"].startswith(CARD_URL_PATH):
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"url": url})
            return

    await resources.async_create_item({"res_type": "module", "url": url})
