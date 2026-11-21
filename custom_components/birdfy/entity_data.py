"""Pure entity data helpers for Birdfy Moments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping


def _last_moment(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the latest normalized Moment."""
    value = data.get("last_moment")
    return value if isinstance(value, Mapping) else {}


def moments_sensor_value(data: Mapping[str, Any], key: str) -> Any:
    """Return one Moments sensor state."""
    if key == "last_species":
        return _last_moment(data).get("species")
    return data.get(key)


def moments_sensor_attributes(
    data: Mapping[str, Any],
    key: str,
) -> dict[str, Any]:
    """Return bounded, Recorder-friendly Moments attributes."""
    attributes: dict[str, Any] = {}
    partial_failures = data.get("partial_failures")
    if partial_failures:
        attributes["partial_failures"] = list(partial_failures)

    if key == "species_count":
        attributes["species_list"] = list(data.get("species_list") or [])
        attributes["species_counts"] = dict(data.get("species_counts") or {})
    elif key == "last_species":
        latest = _last_moment(data)
        attributes["alarm_id"] = latest.get("alarm_id")
        attributes["ai_labels"] = list(latest.get("ai_labels") or [])
        if timestamp := _timestamp(latest.get("time")):
            attributes["detection_time"] = datetime.fromtimestamp(
                timestamp / 1000,
                tz=UTC,
            ).isoformat()

    return attributes


def last_moment_image_attributes(
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """Return stable image metadata without temporary signed URLs."""
    latest = _last_moment(data)
    if not latest:
        return {}

    attributes = {
        "alarm_id": latest.get("alarm_id"),
        "species": latest.get("species"),
        "ai_labels": list(latest.get("ai_labels") or []),
    }
    if timestamp := _timestamp(latest.get("time")):
        attributes["detection_time"] = datetime.fromtimestamp(
            timestamp / 1000,
            tz=UTC,
        ).isoformat()
    return attributes


def last_moment_detection_time(
    data: Mapping[str, Any],
) -> datetime | None:
    """Return the latest real detection time without inventing a timestamp."""
    timestamp = _timestamp(_last_moment(data).get("time"))
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp / 1000, tz=UTC)


def _timestamp(value: object) -> int:
    """Normalize a millisecond timestamp."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
