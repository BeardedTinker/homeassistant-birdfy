"""Tests for bounded Birdfy entity state and attributes."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "birdfy"
    / "entity_data.py"
)
SPEC = importlib.util.spec_from_file_location("birdfy_entity_data", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
entity_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entity_data)


class EntityDataTest(unittest.TestCase):
    """Test Moments entity projections."""

    def setUp(self) -> None:
        """Create a representative coordinator snapshot."""
        self.data = {
            "total_moments": 4,
            "bird_visits": 3,
            "species_count": 2,
            "species_list": ["Robin", "Blue Tit"],
            "species_counts": {"Robin": 2, "Blue Tit": 1},
            "partial_failures": ["media"],
            "moments": [{"large": "feed must not be recorded"}],
            "last_moment": {
                "alarm_id": "event-1",
                "time": 1781560800000,
                "species": "Robin",
                "ai_labels": ["Robin"],
                "image_url": "https://signed.example/image?secret=yes",
                "video_url": "https://signed.example/video?secret=yes",
            },
        }

    def test_sensor_values_are_small_scalars(self) -> None:
        """Expose useful state without serializing the feed."""
        self.assertEqual(
            entity_data.moments_sensor_value(self.data, "total_moments"),
            4,
        )
        self.assertEqual(
            entity_data.moments_sensor_value(self.data, "last_species"),
            "Robin",
        )

    def test_sensor_attributes_exclude_feed_and_signed_urls(self) -> None:
        """Keep temporary media and the full feed out of Recorder."""
        attributes = entity_data.moments_sensor_attributes(
            self.data,
            "last_species",
        )

        serialized = repr(attributes)
        self.assertNotIn("signed.example", serialized)
        self.assertNotIn("feed must not be recorded", serialized)
        self.assertEqual(attributes["alarm_id"], "event-1")

    def test_image_attributes_exclude_signed_urls(self) -> None:
        """Expose stable metadata while image bytes use HA's proxy."""
        attributes = entity_data.last_moment_image_attributes(self.data)

        self.assertEqual(attributes["species"], "Robin")
        self.assertNotIn("url", " ".join(attributes))

    def test_image_timestamp_requires_a_real_moment(self) -> None:
        """Do not make an empty image entity look freshly updated."""
        self.assertIsNone(entity_data.last_moment_detection_time({}))
        self.assertIsNone(
            entity_data.last_moment_detection_time(
                {"last_moment": {"time": "invalid"}},
            )
        )
        self.assertEqual(
            entity_data.last_moment_detection_time(self.data),
            datetime.fromtimestamp(1781560800000 / 1000, tz=UTC),
        )


if __name__ == "__main__":
    unittest.main()
