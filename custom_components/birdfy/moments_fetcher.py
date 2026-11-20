"""Efficient polling orchestration for Birdfy Moments."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .const import DATE_RANGE_TODAY
from .moments import (
    NewMomentTracker,
    build_moments_snapshot,
    date_range_timestamps,
    moment_id,
    moment_timestamp,
)

MAX_MEDIA_EVENTS_PER_UPDATE = 5


class MomentsApi(Protocol):
    """API methods required by the Moments fetcher."""

    async def async_get_tag_list(
        self,
        region: str | None = None,
    ) -> dict[str, str]: ...

    async def async_get_event_count(
        self,
        serial_number: str,
        start_time: int,
        end_time: int,
        *,
        region: str | None = None,
    ) -> int | None: ...

    async def async_get_species_counts(
        self,
        serial_number: str,
        start_time: int,
        end_time: int,
        *,
        region: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def async_get_events(
        self,
        serial_number: str,
        date_range: str,
        *,
        timezone_name: str | None = None,
        region: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    async def async_resolve_media(
        self,
        serial_number: str,
        events: list[dict[str, Any]],
    ) -> dict[Any, dict[str, list[str]]]: ...


class MomentsFeedError(Exception):
    """Raised when the required Moments feed cannot be fetched."""


class BirdfyMomentsFetcher:
    """Combine required and optional API responses into one snapshot."""

    def __init__(
        self,
        api: MomentsApi,
        device: Mapping[str, Any],
        *,
        date_range: str = DATE_RANGE_TODAY,
        event_limit: int = 20,
    ) -> None:
        """Initialize a per-device fetcher."""
        serial_number = str(device.get("serialNumber") or "").strip()
        if not serial_number:
            raise ValueError("Birdfy device has no serial number")

        self.api = api
        self.serial_number = serial_number
        self.device_name = str(
            device.get("deviceName") or serial_number
        ).strip()
        self.device_model = str(
            device.get("productName")
            or device.get("productCode")
            or "Birdfy Feeder"
        ).strip()
        self.region = str(device.get("region") or "").strip() or None
        self.timezone_name = (
            str(device["timezone"]).strip()
            if device.get("timezone")
            else None
        )
        self.date_range = date_range
        self.event_limit = max(1, min(event_limit, 100))
        self._tracker = NewMomentTracker()

    async def async_fetch(self) -> dict[str, Any]:
        """Fetch one Moments snapshot with graceful optional degradation."""
        start_time, end_time = date_range_timestamps(
            self.date_range,
            self.timezone_name,
        )
        operation_names = ["events", "tag_list"]
        operations = [
            self.api.async_get_events(
                self.serial_number,
                self.date_range,
                timezone_name=self.timezone_name,
                region=self.region,
                limit=self.event_limit,
            ),
            self.api.async_get_tag_list(self.region),
        ]
        if start_time is not None and end_time is not None:
            operation_names.extend(("total_count", "species_counts"))
            operations.extend(
                (
                    self.api.async_get_event_count(
                        self.serial_number,
                        start_time,
                        end_time,
                        region=self.region,
                    ),
                    self.api.async_get_species_counts(
                        self.serial_number,
                        start_time,
                        end_time,
                        region=self.region,
                    ),
                )
            )

        results = dict(
            zip(
                operation_names,
                await asyncio.gather(*operations, return_exceptions=True),
                strict=True,
            )
        )
        for result in results.values():
            if isinstance(result, asyncio.CancelledError):
                raise result

        events_result = results["events"]
        if isinstance(events_result, BaseException):
            if isinstance(events_result, asyncio.CancelledError):
                raise events_result
            raise MomentsFeedError("Unable to fetch Birdfy Moments feed") from (
                events_result
            )
        raw_events = [
            event
            for event in events_result
            if isinstance(event, dict)
        ]

        partial_failures = [
            name
            for name, result in results.items()
            if name != "events" and isinstance(result, BaseException)
        ]
        tag_result = results["tag_list"]
        tag_map = tag_result if isinstance(tag_result, dict) else {}
        total_result = results.get("total_count")
        total_count = total_result if isinstance(total_result, int) else None
        counts_result = results.get("species_counts")
        raw_tag_counts = (
            counts_result if isinstance(counts_result, list) else []
        )

        new_events = self._tracker.update(raw_events)
        media_candidates = self._media_candidates(raw_events, new_events)
        media_by_alarm: dict[Any, dict[str, list[str]]] = {}
        if media_candidates:
            try:
                media_by_alarm = await self.api.async_resolve_media(
                    self.serial_number,
                    media_candidates,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                partial_failures.append("media")

        snapshot = build_moments_snapshot(
            raw_events,
            tag_map=tag_map,
            raw_tag_counts=raw_tag_counts,
            total_count=total_count,
            media_by_alarm=media_by_alarm,
            date_range=self.date_range,
            start_time=start_time,
            end_time=end_time,
        )
        normalized_by_id = {
            str(moment["alarm_id"]): moment
            for moment in snapshot["moments"]
            if moment.get("alarm_id") not in (None, "")
        }
        snapshot["new_moments"] = [
            normalized_by_id[event_id]
            for event in new_events
            if (event_id := moment_id(event)) in normalized_by_id
        ]
        snapshot["partial_failures"] = sorted(set(partial_failures))
        return snapshot

    @staticmethod
    def _media_candidates(
        raw_events: Sequence[Mapping[str, Any]],
        new_events: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve only media needed by the latest entity and new events."""
        newest = max(raw_events, key=moment_timestamp, default=None)
        selected: list[Mapping[str, Any]] = list(
            new_events[-MAX_MEDIA_EVENTS_PER_UPDATE:]
        )
        selected_ids = {moment_id(event) for event in selected}
        if newest is not None and moment_id(newest) not in selected_ids:
            selected.append(newest)

        return [
            dict(event)
            for event in selected[-MAX_MEDIA_EVENTS_PER_UPDATE:]
        ]
