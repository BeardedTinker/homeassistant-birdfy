"""On-demand Birdfy Moments feed for the bundled dashboard card."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from .const import CARD_MAX_LIMIT
from .moments import build_moments_snapshot


class CardFeedApi(Protocol):
    """API methods needed by the card feed loader."""

    async def async_get_events(
        self,
        serial_number: str,
        date_range: str,
        *,
        timezone_name: str | None = None,
        region: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def async_get_tag_list(
        self,
        region: str | None = None,
    ) -> dict[str, str]: ...

    async def async_resolve_media(
        self,
        serial_number: str,
        events: list[dict[str, Any]],
    ) -> dict[Any, dict[str, list[str]]]: ...


class CardFeedError(Exception):
    """Raised when the on-demand feed cannot be loaded."""


class BirdfyCardFeedLoader:
    """Load a bounded feed without storing it in entity state."""

    def __init__(
        self,
        api: CardFeedApi,
        *,
        serial_number: str,
        device_name: str,
        region: str | None,
        timezone_name: str | None,
        date_range: str,
    ) -> None:
        """Initialize the loader."""
        self.api = api
        self.serial_number = serial_number
        self.device_name = device_name
        self.region = region
        self.timezone_name = timezone_name
        self.date_range = date_range

    async def async_load(
        self,
        coordinator_data: dict[str, Any] | None,
        *,
        limit: int,
    ) -> dict[str, Any]:
        """Load recent Moments and temporary media URLs."""
        safe_limit = max(1, min(int(limit), CARD_MAX_LIMIT))
        events_result, tags_result = await asyncio.gather(
            self.api.async_get_events(
                self.serial_number,
                self.date_range,
                timezone_name=self.timezone_name,
                region=self.region,
                limit=safe_limit,
            ),
            self.api.async_get_tag_list(self.region),
            return_exceptions=True,
        )
        for result in (events_result, tags_result):
            if isinstance(result, asyncio.CancelledError):
                raise result
        if isinstance(events_result, BaseException):
            raise CardFeedError("Unable to load Birdfy Moments") from events_result

        events = [
            event
            for event in events_result[:safe_limit]
            if isinstance(event, dict)
        ]
        partial_failures = []
        tag_map = {}
        if isinstance(tags_result, dict):
            tag_map = tags_result
        else:
            partial_failures.append("tag_list")

        media_by_alarm: dict[Any, dict[str, list[str]]] = {}
        if events:
            try:
                media_by_alarm = await self.api.async_resolve_media(
                    self.serial_number,
                    events,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                partial_failures.append("media")

        snapshot = build_moments_snapshot(
            events,
            tag_map=tag_map,
            media_by_alarm=media_by_alarm,
            date_range=self.date_range,
        )
        current = coordinator_data or {}
        return {
            "device_name": self.device_name,
            "total_moments": current.get(
                "total_moments",
                snapshot["total_moments"],
            ),
            "bird_visits": current.get(
                "bird_visits",
                snapshot["bird_visits"],
            ),
            "species_count": current.get(
                "species_count",
                snapshot["species_count"],
            ),
            "species_counts": current.get(
                "species_counts",
                snapshot["species_counts"],
            ),
            "moments": snapshot["moments"][:safe_limit],
            "partial_failures": sorted(
                set(current.get("partial_failures", ()))
                | set(partial_failures)
            ),
        }
