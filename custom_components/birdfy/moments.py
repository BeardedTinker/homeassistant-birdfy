"""Pure helpers for Birdfy Moments data."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
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
from .media import is_http_url

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
MAX_TRACKED_MOMENTS = 200


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


def moment_timestamp(event: Mapping[str, Any]) -> int:
    """Return a normalized Moment timestamp."""
    value = event.get("alertTime") or event.get("alertTimeTs") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def moment_id(event: Mapping[str, Any]) -> str | None:
    """Return a stable Moment identifier."""
    alarm_id = event.get("alarmId")
    if alarm_id in (None, ""):
        return None
    return str(alarm_id)


def _media_for_alarm(
    media_by_alarm: Mapping[Any, Mapping[str, Sequence[str]]],
    alarm_id: object,
) -> Mapping[str, Sequence[str]]:
    """Find resolved media despite integer/string alarm ID differences."""
    if alarm_id in media_by_alarm:
        return media_by_alarm[alarm_id]
    alarm_key = str(alarm_id)
    if alarm_key in media_by_alarm:
        return media_by_alarm[alarm_key]
    return {}


def build_moments_snapshot(
    raw_events: Sequence[Mapping[str, Any]],
    *,
    tag_map: dict[str, str] | None = None,
    raw_tag_counts: Sequence[Mapping[str, Any]] = (),
    total_count: int | None = None,
    media_by_alarm: Mapping[Any, Mapping[str, Sequence[str]]] | None = None,
    date_range: str = DATE_RANGE_TODAY,
    start_time: int | None = None,
    end_time: int | None = None,
) -> dict[str, Any]:
    """Normalize API responses into coordinator-ready Moments data."""
    tags = tag_map or {}
    media = media_by_alarm or {}
    ordered_events = sorted(
        (event for event in raw_events if isinstance(event, Mapping)),
        key=moment_timestamp,
        reverse=True,
    )

    moments: list[dict[str, Any]] = []
    for event in ordered_events:
        alarm_id = event.get("alarmId")
        labels = extract_event_labels(dict(event), tags)
        resolved_media = _media_for_alarm(media, alarm_id)
        videos = resolved_media.get("videos", ())
        images = resolved_media.get("images", ())
        video_url = next(
            (value for value in videos if is_http_url(value)),
            None,
        )
        image_url = next(
            (value for value in images if is_http_url(value)),
            None,
        )
        if image_url is None and is_http_url(event.get("pic")):
            image_url = str(event["pic"]).strip()

        moments.append(
            {
                "alarm_id": alarm_id,
                "time": moment_timestamp(event),
                "species": labels[0] if labels else "Motion",
                "ai_labels": labels,
                "video_url": video_url,
                "image_url": image_url,
                "type": event.get("type"),
            }
        )

    bird_visits: int | None = None
    animal_visits = 0
    collected_count = 0
    species_counts: dict[str, int] = {}

    for item in raw_tag_counts:
        raw_tag = str(item.get("commonName") or item.get("tag") or "").strip()
        if not raw_tag:
            continue
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue

        category = raw_tag.lower()
        if category == "all":
            if total_count is None:
                total_count = count
        elif category == "allbird":
            bird_visits = count
        elif category == "allanimal":
            animal_visits = count
        elif category == "collected":
            collected_count = count
        else:
            label = normalize_species_label(raw_tag, tags)
            if label and label.lower() not in GENERIC_AI_LABELS:
                species_counts[label] = species_counts.get(label, 0) + count

    # Recent detections can be newer than the statistics endpoint.
    for moment in moments:
        for label in moment["ai_labels"]:
            if (
                label.lower() not in GENERIC_AI_LABELS
                and label not in species_counts
            ):
                species_counts[label] = 1

    sorted_species_counts = dict(
        sorted(
            species_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )
    if total_count is None:
        total_count = len(moments)
    else:
        total_count = max(total_count, len(moments))
    observed_bird_visits = (
        sum(sorted_species_counts.values())
        if sorted_species_counts
        else sum(
            1
            for moment in moments
            if any(
                label.lower() == "bird"
                or label.lower() not in GENERIC_AI_LABELS
                for label in moment["ai_labels"]
            )
        )
    )
    if bird_visits is None:
        bird_visits = observed_bird_visits
    else:
        bird_visits = max(bird_visits, observed_bird_visits)

    last_moment = moments[0] if moments else None
    return {
        "total_moments": total_count,
        "bird_visits": bird_visits,
        "animal_visits": animal_visits,
        "collected_count": collected_count,
        "species_count": len(sorted_species_counts),
        "species_list": list(sorted_species_counts),
        "species_counts": sorted_species_counts,
        "moments": moments,
        "last_moment": last_moment,
        "last_detection": last_moment["time"] if last_moment else None,
        "date_range": date_range,
        "start_time": start_time,
        "end_time": end_time,
    }


class NewMomentTracker:
    """Track recently observed alarm IDs and return genuinely new Moments."""

    def __init__(self, max_tracked: int = MAX_TRACKED_MOMENTS) -> None:
        """Initialize bounded in-memory tracking."""
        self._seen_order: deque[str] = deque(maxlen=max(1, max_tracked))
        self._seen_ids: set[str] = set()
        self._initialized = False
        self._high_watermark = 0

    def update(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        """Record a feed update and return new events oldest first."""
        ordered = sorted(
            (event for event in events if moment_id(event) is not None),
            key=moment_timestamp,
            reverse=True,
        )
        if not self._initialized:
            for event in ordered:
                self._remember(moment_id(event))
            self._high_watermark = max(
                (moment_timestamp(event) for event in ordered),
                default=0,
            )
            self._initialized = True
            return []

        new_events = [
            event
            for event in ordered
            if moment_id(event) not in self._seen_ids
            and moment_timestamp(event) >= self._high_watermark
        ]
        for event in ordered:
            self._remember(moment_id(event))
        newest_timestamp = max(
            (moment_timestamp(event) for event in ordered),
            default=self._high_watermark,
        )
        self._high_watermark = max(self._high_watermark, newest_timestamp)
        return sorted(new_events, key=moment_timestamp)

    def _remember(self, event_id: str | None) -> None:
        """Remember one ID while keeping the set and deque bounded."""
        if event_id is None or event_id in self._seen_ids:
            return
        if len(self._seen_order) == self._seen_order.maxlen:
            expired = self._seen_order.popleft()
            self._seen_ids.discard(expired)
        self._seen_order.append(event_id)
        self._seen_ids.add(event_id)
