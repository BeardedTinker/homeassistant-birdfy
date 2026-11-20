"""Tests for Birdfy Moments helpers."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "birdfy"

# Load the pure modules without importing the Home Assistant-dependent package.
package = types.ModuleType("birdfy_test")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[package.__name__] = package

for module_name in ("const", "moments"):
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.{module_name}",
        PACKAGE_PATH / f"{module_name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

const = sys.modules["birdfy_test.const"]
moments = sys.modules["birdfy_test.moments"]


class DateRangeTest(unittest.TestCase):
    """Test timezone-aware date ranges."""

    def test_today_uses_device_timezone(self) -> None:
        """Calculate local midnight rather than server midnight."""
        now = datetime(2026, 6, 15, 22, 30, tzinfo=UTC)

        start, end = moments.date_range_timestamps(
            const.DATE_RANGE_TODAY,
            "Europe/Vienna",
            now=now,
        )

        self.assertEqual(start, 1781560800000)
        self.assertEqual(end, 1781647199999)

    def test_all_time_has_no_bounds(self) -> None:
        """Represent all-time queries without artificial timestamps."""
        self.assertEqual(
            moments.date_range_timestamps(const.DATE_RANGE_ALL_TIME),
            (None, None),
        )

    def test_week_starts_on_monday(self) -> None:
        """Use Monday as the first day of this-week ranges."""
        now = datetime(2026, 6, 17, 12, tzinfo=UTC)

        start, _ = moments.date_range_timestamps(
            const.DATE_RANGE_THIS_WEEK,
            "UTC",
            now=now,
        )

        self.assertEqual(start, 1781481600000)

    def test_dst_transition_uses_local_calendar_day(self) -> None:
        """Keep local midnight bounds across a 23-hour DST day."""
        now = datetime(2026, 3, 29, 12, tzinfo=UTC)

        start, end = moments.date_range_timestamps(
            const.DATE_RANGE_TODAY,
            "Europe/Vienna",
            now=now,
        )

        self.assertEqual(end - start + 1, 23 * 60 * 60 * 1000)


class LabelExtractionTest(unittest.TestCase):
    """Test Moment AI label parsing."""

    def test_specific_species_replaces_generic_bird(self) -> None:
        """Prefer a species name over a generic Bird label."""
        event = {"aiLabels": ["Bird", "3_northern_cardinal"]}

        self.assertEqual(
            moments.extract_event_labels(event),
            ["Northern Cardinal"],
        )

    def test_nested_and_json_labels_are_deduplicated(self) -> None:
        """Read known nested sources without harvesting unrelated strings."""
        event = {
            "description": '{"aiLabels": [{"name": "Robin"}], "url": "secret"}',
            "extra": {"motion": {"aiLabels": "robin, No_Bird"}},
        }

        self.assertEqual(moments.extract_event_labels(event), ["Robin"])

    def test_tag_map_preserves_attribute(self) -> None:
        """Resolve localized tag keys while keeping sex/age attributes."""
        self.assertEqual(
            moments.normalize_species_label(
                "3_northern_cardinal (male)",
                {"3_northern_cardinal": "Northern Cardinal"},
            ),
            "Northern Cardinal (male)",
        )

    def test_label_mask_is_last_fallback(self) -> None:
        """Use labelMask only when no structured labels exist."""
        self.assertEqual(
            moments.extract_event_labels({"labelMask": 1 << 5}),
            ["Bird"],
        )


class SnapshotTest(unittest.TestCase):
    """Test normalized coordinator snapshots."""

    def test_snapshot_sorts_events_and_uses_latest_media(self) -> None:
        """Build a stable newest-first snapshot from unordered API data."""
        snapshot = moments.build_moments_snapshot(
            [
                {
                    "alarmId": 1,
                    "alertTime": 100,
                    "aiLabels": ["Robin"],
                },
                {
                    "alarmId": 2,
                    "alertTimeTs": "200",
                    "aiLabels": ["Bird", "Blue Tit"],
                },
            ],
            media_by_alarm={
                "2": {
                    "videos": ["https://example.test/latest.mp4"],
                    "images": ["https://example.test/latest.jpg"],
                }
            },
        )

        self.assertEqual(snapshot["last_moment"]["alarm_id"], 2)
        self.assertEqual(snapshot["last_moment"]["species"], "Blue Tit")
        self.assertEqual(
            snapshot["last_moment"]["image_url"],
            "https://example.test/latest.jpg",
        )
        self.assertEqual(snapshot["species_list"], ["Blue Tit", "Robin"])

    def test_snapshot_prefers_statistics_and_filters_invalid_counts(self) -> None:
        """Use statistics while tolerating malformed category values."""
        snapshot = moments.build_moments_snapshot(
            [],
            raw_tag_counts=[
                {"tag": "all", "count": "12"},
                {"tag": "allbird", "count": 7},
                {"tag": "allanimal", "count": 2},
                {"tag": "collected", "count": 1},
                {"tag": "Robin", "count": 4},
                {"tag": "Broken", "count": "invalid"},
            ],
        )

        self.assertEqual(snapshot["total_moments"], 12)
        self.assertEqual(snapshot["bird_visits"], 7)
        self.assertEqual(snapshot["animal_visits"], 2)
        self.assertEqual(snapshot["collected_count"], 1)
        self.assertEqual(snapshot["species_counts"], {"Robin": 4})


class NewMomentTrackerTest(unittest.TestCase):
    """Test notification event de-duplication."""

    def test_initial_feed_is_seeded_without_notifications(self) -> None:
        """Never replay old events when Home Assistant starts."""
        tracker = moments.NewMomentTracker()

        new_events = tracker.update(
            [
                {"alarmId": "two", "alertTime": 200},
                {"alarmId": "one", "alertTime": 100},
            ]
        )

        self.assertEqual(new_events, [])

    def test_multiple_new_events_are_returned_oldest_first(self) -> None:
        """Return every new arrival between polls in notification order."""
        tracker = moments.NewMomentTracker()
        tracker.update([{"alarmId": "one", "alertTime": 100}])

        new_events = tracker.update(
            [
                {"alarmId": "three", "alertTime": 300},
                {"alarmId": "two", "alertTime": 200},
                {"alarmId": "one", "alertTime": 100},
            ]
        )

        self.assertEqual(
            [event["alarmId"] for event in new_events],
            ["two", "three"],
        )

    def test_reordering_does_not_duplicate_events(self) -> None:
        """Ignore already-seen IDs if API ordering changes."""
        tracker = moments.NewMomentTracker()
        tracker.update([{"alarmId": "one", "alertTime": 100}])
        tracker.update([{"alarmId": "two", "alertTime": 200}])

        self.assertEqual(
            tracker.update(
                [
                    {"alarmId": "one", "alertTime": 100},
                    {"alarmId": "two", "alertTime": 200},
                ]
            ),
            [],
        )

    def test_delayed_older_event_is_not_reported(self) -> None:
        """Do not notify on an old event newly inserted into the feed."""
        tracker = moments.NewMomentTracker()
        tracker.update([{"alarmId": "current", "alertTime": 200}])

        self.assertEqual(
            tracker.update(
                [
                    {"alarmId": "current", "alertTime": 200},
                    {"alarmId": "old", "alertTime": 100},
                ]
            ),
            [],
        )

    def test_empty_feed_after_initialization_is_safe(self) -> None:
        """Tolerate temporary empty API responses."""
        tracker = moments.NewMomentTracker()
        tracker.update([{"alarmId": "current", "alertTime": 200}])

        self.assertEqual(tracker.update([]), [])


if __name__ == "__main__":
    unittest.main()
