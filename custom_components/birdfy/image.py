"""Image platform for Birdfy."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BirdfyHighlightsCoordinator
from .media import highlight_timestamp, image_url_for_highlights, latest_highlight

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    subentry_id: str | None = None,
) -> None:
    """Set up Birdfy image entities."""
    coordinators = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    entities = []

    if subentry_id:
        # Set up image for a specific subentry
        coordinator = coordinators.get(subentry_id)
        if coordinator:
            subentry = entry.subentries.get(subentry_id)
            subentry_name = subentry.title if subentry else "Unknown"
            entities.append(
                BirdfyLastBirdImage(hass, coordinator, entry, subentry_id, subentry_name)
            )
    else:
        # Initial setup - create images for all coordinators
        for coord_id, coordinator in coordinators.items():
            if coord_id == "default":
                entities.append(
                    BirdfyLastBirdImage(hass, coordinator, entry, None, None)
                )
            else:
                subentry = entry.subentries.get(coord_id)
                subentry_name = subentry.title if subentry else "Unknown"
                entities.append(
                    BirdfyLastBirdImage(hass, coordinator, entry, coord_id, subentry_name)
                )

    _LOGGER.debug("Setting up %d image entities", len(entities))
    async_add_entities(entities)


class BirdfyLastBirdImage(CoordinatorEntity, ImageEntity):
    """Image entity showing the last bird seen."""

    _attr_has_entity_name = True
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: BirdfyHighlightsCoordinator,
        entry: ConfigEntry,
        subentry_id: str | None = None,
        subentry_name: str | None = None,
    ) -> None:
        """Initialize the image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)

        self._subentry_id = subentry_id
        self._subentry_name = subentry_name
        self._last_url: str | None = None
        self._cached_image: bytes | None = None
        self._attr_image_last_updated = datetime.now(UTC)

        # Set name based on subentry
        if subentry_name:
            self._attr_name = f"Last Bird - {subentry_name}"
        else:
            self._attr_name = "Last Bird"

        # Create unique ID based on subentry
        if subentry_id:
            self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_last_bird_image"
        else:
            self._attr_unique_id = f"{entry.entry_id}_last_bird_image"

        # Create device info
        if subentry_id and subentry_name:
            device_id = f"{entry.entry_id}_{subentry_id}"
            device_name = f"Birdfy - {subentry_name}"
        else:
            device_id = entry.entry_id
            device_name = "Birdfy"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Birdfy",
            "model": "Highlights API",
        }

        _LOGGER.debug("Initialized BirdfyLastBirdImage entity: %s", self._attr_unique_id)

    def _get_image_url(self) -> str | None:
        """Get the current image URL from coordinator data."""
        if self.coordinator.data is None:
            return None

        highlights = self.coordinator.data.get("highlights", [])
        thumbnails = self.coordinator.data.get("thumbnails", {})
        return image_url_for_highlights(highlights, thumbnails)

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
        url = self._get_image_url()

        if url is None:
            return None

        # Return cached image if URL hasn't changed
        if url == self._last_url and self._cached_image is not None:
            return self._cached_image

        # Fetch new image
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    if response.content_type.startswith("image/"):
                        self._attr_content_type = response.content_type
                    self._cached_image = await response.read()
                    self._last_url = url
                    return self._cached_image
                _LOGGER.warning("Failed to fetch Birdfy image: HTTP %s", response.status)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Failed to fetch Birdfy image: %s", err)

        return self._cached_image

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check if image URL changed to update the timestamp
        new_url = self._get_image_url()
        if new_url != self._last_url:
            self._cached_image = None  # Clear cache
            self._attr_image_last_updated = datetime.now(UTC)

        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        if self.coordinator.data is None:
            return {}

        highlights = self.coordinator.data.get("highlights", [])
        if not highlights:
            return {}

        last = latest_highlight(highlights)
        if last is None:
            return {}

        attrs = {
            "species": last.get("species"),
            "title": last.get("title"),
            "category": last.get("category"),
            "image_url": self._get_image_url(),
        }

        if timestamp := highlight_timestamp(last):
            attrs["detection_time"] = datetime.fromtimestamp(
                timestamp / 1000, tz=UTC
            ).isoformat()

        if last.get("video_url"):
            attrs["video_url"] = last.get("video_url")

        return attrs
