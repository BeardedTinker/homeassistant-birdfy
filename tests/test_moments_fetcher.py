"""Tests for efficient Birdfy Moments polling."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "birdfy"
package = types.ModuleType("birdfy_fetcher_test")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[package.__name__] = package

for module_name in ("const", "media", "moments", "moments_fetcher"):
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.{module_name}",
        PACKAGE_PATH / f"{module_name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

fetcher_module = sys.modules["birdfy_fetcher_test.moments_fetcher"]


class FakeMomentsApi:
    """Configurable in-memory Moments API."""

    def __init__(self) -> None:
        self.event_batches: list[object] = []
        self.tag_result: object = {}
        self.total_result: object = 0
        self.counts_result: object = []
        self.media_result: object | None = None
        self.media_calls: list[list[dict]] = []

    @staticmethod
    def _result(value: object) -> object:
        if isinstance(value, BaseException):
            raise value
        return value

    async def async_get_events(self, *_args: object, **_kwargs: object) -> list:
        return self._result(self.event_batches.pop(0))

    async def async_get_tag_list(self, *_args: object, **_kwargs: object) -> dict:
        return self._result(self.tag_result)

    async def async_get_event_count(self, *_args: object, **_kwargs: object) -> int:
        return self._result(self.total_result)

    async def async_get_species_counts(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> list:
        return self._result(self.counts_result)

    async def async_resolve_media(
        self,
        _serial_number: str,
        events: list[dict],
    ) -> dict:
        self.media_calls.append(events)
        if self.media_result is not None:
            return self._result(self.media_result)
        return {
            event["alarmId"]: {
                "images": [
                    f"https://example.test/{event['alarmId']}.jpg"
                ],
                "videos": [],
            }
            for event in events
        }


class MomentsFetcherTest(unittest.IsolatedAsyncioTestCase):
    """Test required/optional polling and media efficiency."""

    def setUp(self) -> None:
        """Create a feeder and API fake."""
        self.api = FakeMomentsApi()
        self.fetcher = fetcher_module.BirdfyMomentsFetcher(
            self.api,
            {
                "serialNumber": "SERIAL",
                "deviceName": "Garden Feeder",
                "timezone": "UTC",
                "region": "eu",
            },
        )

    async def test_initial_poll_seeds_events_and_resolves_only_latest(self) -> None:
        """Avoid startup notifications and unnecessary media lookups."""
        self.api.event_batches = [
            [
                {"alarmId": "old", "alertTime": 100},
                {"alarmId": "latest", "alertTime": 200},
            ]
        ]

        snapshot = await self.fetcher.async_fetch()

        self.assertEqual(snapshot["new_moments"], [])
        self.assertEqual(
            [event["alarmId"] for event in self.api.media_calls[0]],
            ["latest"],
        )
        self.assertEqual(
            snapshot["last_moment"]["image_url"],
            "https://example.test/latest.jpg",
        )

    async def test_new_events_are_normalized_in_arrival_order(self) -> None:
        """Resolve media and return multiple new arrivals oldest first."""
        self.api.event_batches = [
            [{"alarmId": "seed", "alertTime": 100}],
            [
                {
                    "alarmId": "newest",
                    "alertTime": 300,
                    "aiLabels": ["Robin"],
                },
                {
                    "alarmId": "new",
                    "alertTime": 200,
                    "aiLabels": ["Blue Tit"],
                },
                {"alarmId": "seed", "alertTime": 100},
            ],
        ]
        await self.fetcher.async_fetch()

        snapshot = await self.fetcher.async_fetch()

        self.assertEqual(
            [moment["alarm_id"] for moment in snapshot["new_moments"]],
            ["new", "newest"],
        )
        self.assertEqual(len(self.api.media_calls[1]), 2)

    async def test_optional_failures_return_partial_snapshot(self) -> None:
        """Keep the feed available when statistics or labels fail."""
        self.api.event_batches = [[{"alarmId": "one", "alertTime": 100}]]
        self.api.tag_result = RuntimeError("tags failed")
        self.api.total_result = RuntimeError("total failed")
        self.api.counts_result = RuntimeError("counts failed")

        snapshot = await self.fetcher.async_fetch()

        self.assertEqual(
            snapshot["partial_failures"],
            ["species_counts", "tag_list", "total_count"],
        )
        self.assertEqual(snapshot["total_moments"], 1)

    async def test_feed_failure_is_fatal(self) -> None:
        """Do not publish stale-looking success when the feed fails."""
        self.api.event_batches = [RuntimeError("feed failed")]

        with self.assertRaises(fetcher_module.MomentsFeedError):
            await self.fetcher.async_fetch()

    async def test_media_failure_is_partial(self) -> None:
        """Publish sensor data even when temporary media links fail."""
        self.api.event_batches = [[{"alarmId": "one", "alertTime": 100}]]
        self.api.media_result = RuntimeError("media failed")

        snapshot = await self.fetcher.async_fetch()

        self.assertEqual(snapshot["partial_failures"], ["media"])
        self.assertIsNone(snapshot["last_moment"]["image_url"])

    async def test_cancellation_is_not_swallowed(self) -> None:
        """Allow Home Assistant to cancel an in-flight update."""
        self.api.event_batches = [[{"alarmId": "one", "alertTime": 100}]]
        self.api.tag_result = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.fetcher.async_fetch()


if __name__ == "__main__":
    unittest.main()
