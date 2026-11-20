"""Media helpers for Birdfy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

IMAGE_SUFFIXES = (".jpeg", ".jpg", ".png", ".webp")


def is_http_url(value: object) -> bool:
    """Return whether value is an absolute HTTP(S) URL."""
    if not isinstance(value, str):
        return False

    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_image_url(value: object) -> bool:
    """Return whether value is an absolute URL to a supported image."""
    if not is_http_url(value):
        return False

    return urlparse(str(value).strip()).path.lower().endswith(IMAGE_SUFFIXES)


def highlight_timestamp(highlight: Mapping[str, Any]) -> int:
    """Return a sortable highlight timestamp."""
    try:
        return int(highlight.get("time") or 0)
    except (TypeError, ValueError):
        return 0


def sorted_highlights(
    highlights: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return highlights sorted newest first without changing the input."""
    return sorted(highlights, key=highlight_timestamp, reverse=True)


def latest_highlight(
    highlights: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the newest highlight."""
    ordered = sorted_highlights(highlights)
    return ordered[0] if ordered else None


def image_url_for_highlights(
    highlights: Sequence[Mapping[str, Any]],
    thumbnails: Mapping[str, Any],
) -> str | None:
    """Return the newest usable highlight image URL."""
    ordered = sorted_highlights(highlights)

    # Birdfy exposes photo highlights through fileUrl. The coordinator currently
    # stores that field as video_url for both photos and videos.
    for highlight in ordered:
        file_url = highlight.get("video_url")
        if is_image_url(file_url):
            return str(file_url).strip()

    # Older payloads may only include species thumbnails. Relative coverKey
    # values are not directly fetchable and must not be returned.
    for highlight in ordered:
        species = highlight.get("species")
        thumbnail = thumbnails.get(species) if species else None
        if is_http_url(thumbnail):
            return str(thumbnail).strip()

    for thumbnail in thumbnails.values():
        if is_http_url(thumbnail):
            return str(thumbnail).strip()

    return None
