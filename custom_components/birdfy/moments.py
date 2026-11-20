"""Pure helpers for Birdfy Moments data."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import (
    DATE_RANGE_ALL_TIME,
    DATE_RANGE_LAST_7_DAYS,
    DATE_RANGE_LAST_14_DAYS,
    DATE_RANGE_LAST_30_DAYS,
    DATE_RANGE_THIS_MONTH,
    DATE_RANGE_THIS_WEEK,
    DATE_RANGE_TODAY,
    DATE_RANGE_YESTERDAY,
)

IGNORED_AI_LABELS = {"no bird", "no_bird", "nobird", "not a bird"}
GENERIC_AI_LABELS = {
    "animal",
    "bird",
    "human",
    "package",
    "person",
    "pet",
    "squirrel",
    "vehicle",
}
AI_LABEL_KEYS = {"commonname", "desc", "label", "name", "species", "tag"}
LABEL_MASK_NAMES = ("person", "pet", "vehicle", "package", "animal", "bird", "bird")
TAG_PREFIX_RE = re.compile(r"^\d+_")


def resolve_timezone(timezone_name: str | None) -> ZoneInfo | None:
    """Resolve an IANA timezone name, returning None when it is invalid."""
    if not timezone_name:
        return None
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def date_range_timestamps(
    date_range: str,
    timezone_name: str | None = None,
    *,
    now: datetime | None = None,
) -> tuple[int | None, int | None]:
    """Return inclusive millisecond bounds for a Birdfy date range."""
    timezone = resolve_timezone(timezone_name)
    if now is None:
        current = datetime.now(timezone) if timezone else datetime.now()
    elif timezone and now.tzinfo is not None:
        current = now.astimezone(timezone)
    elif timezone:
        current = now.replace(tzinfo=timezone)
    else:
        current = now

    today = current.replace(hour=0, minute=0, second=0, microsecond=0)

    if date_range == DATE_RANGE_ALL_TIME:
        return None, None
    if date_range == DATE_RANGE_YESTERDAY:
        start = today - timedelta(days=1)
        end = today - timedelta(milliseconds=1)
    elif date_range == DATE_RANGE_LAST_7_DAYS:
        start = today - timedelta(days=6)
        end = today + timedelta(days=1) - timedelta(milliseconds=1)
    elif date_range == DATE_RANGE_LAST_14_DAYS:
        start = today - timedelta(days=13)
        end = today + timedelta(days=1) - timedelta(milliseconds=1)
    elif date_range == DATE_RANGE_LAST_30_DAYS:
        start = today - timedelta(days=29)
        end = today + timedelta(days=1) - timedelta(milliseconds=1)
    elif date_range == DATE_RANGE_THIS_WEEK:
        start = today - timedelta(days=current.weekday())
        end = today + timedelta(days=1) - timedelta(milliseconds=1)
    elif date_range == DATE_RANGE_THIS_MONTH:
        start = today.replace(day=1)
        end = today + timedelta(days=1) - timedelta(milliseconds=1)
    else:
        # Unknown ranges intentionally retain the integration's historic
        # behavior and use today.
        start = today
        end = today + timedelta(days=1) - timedelta(milliseconds=1)

    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def normalize_species_label(
    raw_label: object,
    tag_map: dict[str, str] | None = None,
) -> str:
    """Convert a Netvue tag key into a human-readable label."""
    cleaned = str(raw_label or "").strip()
    if not cleaned:
        return ""

    attribute = None
    if match := re.fullmatch(r"(.+?)\s*\(\s*([^()]+?)\s*\)", cleaned):
        base_name = match.group(1).strip()
        attribute = match.group(2).strip()
    else:
        base_name = cleaned

    lookup = tag_map or {}
    if resolved := lookup.get(base_name.lower()):
        display_name = resolved
    elif TAG_PREFIX_RE.match(base_name):
        stripped = TAG_PREFIX_RE.sub("", base_name).replace("_", " ").strip()
        display_name = lookup.get(stripped.lower(), stripped.title())
    else:
        display_name = base_name.replace("_", " ")
        if display_name.islower():
            display_name = display_name.title()

    return f"{display_name} ({attribute})" if attribute else display_name


def extract_event_labels(
    event: dict[str, Any],
    tag_map: dict[str, str] | None = None,
) -> list[str]:
    """Extract normalized, de-duplicated AI labels from a Moment event."""
    description: dict[str, Any] = {}
    raw_description = event.get("description")
    if isinstance(raw_description, str) and raw_description.strip():
        try:
            parsed = json.loads(raw_description)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            description = parsed
    elif isinstance(raw_description, dict):
        description = raw_description

    extra = event.get("extra")
    motion = extra.get("motion") if isinstance(extra, dict) else None
    sources = (
        event.get("aiLabels"),
        motion.get("aiLabels") if isinstance(motion, dict) else None,
        description.get("aiLabels"),
    )
    found: dict[str, str] = {}

    def collect(node: Any, accept_strings: bool = True) -> None:
        if isinstance(node, str):
            if not accept_strings:
                return
            for part in node.split(","):
                raw = part.strip()
                normalized_key = re.sub(r"[\s_-]+", " ", raw.lower())
                if not raw or normalized_key in IGNORED_AI_LABELS:
                    continue
                display = normalize_species_label(raw, tag_map)
                if display:
                    found.setdefault(display.lower(), display)
        elif isinstance(node, list):
            for item in node:
                collect(item, accept_strings)
        elif isinstance(node, dict):
            for key, value in node.items():
                key_matches = str(key).lower() in AI_LABEL_KEYS
                if key_matches or isinstance(value, (dict, list)):
                    collect(value, key_matches)

    for source in sources:
        if source is not None:
            collect(source)

    labels = list(found.values())
    specific_labels = [
        label for label in labels if label.lower() not in GENERIC_AI_LABELS
    ]
    if specific_labels:
        return specific_labels
    if labels:
        return labels

    label_mask = event.get("labelMask")
    if not isinstance(label_mask, int) or label_mask <= 0:
        return []

    mask_labels: list[str] = []
    for index, name in enumerate(LABEL_MASK_NAMES):
        if label_mask & (1 << index):
            title = name.title()
            if title not in mask_labels:
                mask_labels.append(title)
    return mask_labels
