"""Tests for Birdfy media helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "birdfy" / "media.py"
)
SPEC = importlib.util.spec_from_file_location("birdfy_media", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


class MediaHelpersTest(unittest.TestCase):
    """Test Birdfy media URL selection."""

    def test_newest_photo_highlight_wins(self) -> None:
        """Prefer the newest direct image even when input is unordered."""
        highlights = [
            {"time": 100, "video_url": "https://example.com/old.jpg"},
            {"time": 300, "video_url": "https://example.com/new.jpeg?token=x"},
            {"time": 200, "video_url": "https://example.com/clip.mp4"},
        ]

        self.assertEqual(
            media.image_url_for_highlights(highlights, {}),
            "https://example.com/new.jpeg?token=x",
        )

    def test_absolute_thumbnail_is_fallback(self) -> None:
        """Use a matching absolute thumbnail when no photo highlight exists."""
        highlights = [
            {
                "time": 100,
                "species": "Robin",
                "video_url": "https://example.com/clip.mp4",
            }
        ]

        self.assertEqual(
            media.image_url_for_highlights(
                highlights, {"Robin": "https://example.com/robin.webp"}
            ),
            "https://example.com/robin.webp",
        )

    def test_relative_thumbnail_is_rejected(self) -> None:
        """Do not return an unusable relative coverKey."""
        self.assertIsNone(
            media.image_url_for_highlights(
                [{"time": 100, "species": "Robin"}],
                {"Robin": "covers/robin.jpg"},
            )
        )

    def test_latest_highlight_handles_invalid_time(self) -> None:
        """Sort malformed timestamps behind valid timestamps."""
        latest = media.latest_highlight(
            [{"time": "invalid", "title": "Old"}, {"time": "200", "title": "New"}]
        )

        self.assertIsNotNone(latest)
        self.assertEqual(latest["title"], "New")

    def test_invalid_timestamp_returns_zero(self) -> None:
        """Treat malformed timestamps as missing."""
        self.assertEqual(media.highlight_timestamp({"time": "invalid"}), 0)


if __name__ == "__main__":
    unittest.main()
