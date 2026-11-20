"""Response services used by the bundled Birdfy dashboard card."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import time
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .card_feed import BirdfyCardFeedLoader, CardFeedError
from .const import (
    ATTR_DEVICE_ID,
    ATTR_LIMIT,
    CARD_CACHE_TTL,
    CARD_DEFAULT_LIMIT,
    CARD_MAX_LIMIT,
    DOMAIN,
    SERVICE_GET_MOMENTS,
)

GET_MOMENTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(
            ATTR_LIMIT,
            default=CARD_DEFAULT_LIMIT,
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=CARD_MAX_LIMIT)),
    }
)
_RUNTIME_KEY = "_card_service_runtime"


@dataclass(slots=True)
class CardServiceRuntime:
    """Short-lived response cache and per-feeder request locks."""

    cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = field(
        default_factory=dict
    )
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Birdfy response services once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    runtime = domain_data.setdefault(_RUNTIME_KEY, CardServiceRuntime())
    if hass.services.has_service(DOMAIN, SERVICE_GET_MOMENTS):
        return

    async def async_get_moments(call: ServiceCall) -> dict[str, Any]:
        """Return a bounded feed for the bundled card."""
        serial_number, coordinator, account_api = _resolve_feeder(
            hass,
            call.data[ATTR_DEVICE_ID],
        )
        limit = call.data[ATTR_LIMIT]
        cache_key = (serial_number, limit)
        now = time.monotonic()
        if cached := runtime.cache.get(cache_key):
            if cached[0] > now:
                return deepcopy(cached[1])

        lock = runtime.locks.setdefault(serial_number, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            if cached := runtime.cache.get(cache_key):
                if cached[0] > now:
                    return deepcopy(cached[1])

            fetcher = coordinator.fetcher
            loader = BirdfyCardFeedLoader(
                account_api,
                serial_number=fetcher.serial_number,
                device_name=fetcher.device_name,
                region=fetcher.region,
                timezone_name=fetcher.timezone_name,
                date_range=fetcher.date_range,
            )
            try:
                response = await loader.async_load(
                    coordinator.data,
                    limit=limit,
                )
            except CardFeedError as err:
                raise HomeAssistantError(
                    "Unable to load Birdfy Moments"
                ) from err

            runtime.cache[cache_key] = (
                time.monotonic() + CARD_CACHE_TTL,
                response,
            )
            return deepcopy(response)

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MOMENTS,
        async_get_moments,
        schema=GET_MOMENTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _resolve_feeder(
    hass: HomeAssistant,
    device_id: str,
) -> tuple[str, Any, Any]:
    """Resolve one registered Birdfy feeder without exposing identifiers."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError("Birdfy feeder not found")

    serial_number = next(
        (
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        ),
        None,
    )
    if serial_number is None:
        raise HomeAssistantError("Birdfy feeder not found")

    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        coordinator = entry_data.get("moments_coordinators", {}).get(
            serial_number
        )
        account_api = entry_data.get("account_api")
        if coordinator is not None and account_api is not None:
            return serial_number, coordinator, account_api

    raise HomeAssistantError("Birdfy feeder is not available")
