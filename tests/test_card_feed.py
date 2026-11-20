"""Tests for the on-demand Birdfy dashboard feed."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "birdfy"
package = types.ModuleType("birdfy_card_feed_test")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[package.__name__] = package

for module_name in ("const", "media", "moments", "card_feed"):
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.{module_name}",
        PACKAGE_PATH / f"{module_name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

card_feed = sys.modules["birdfy_card_feed_test.card_feed"]


class FakeCardApi:
    """In-memory card API."""

    def __init__(self) -> None:
        self.events: object = []
        self.tags: object = {}
        self.media: object = {}
        self.requested_limit: int | None = None

    @staticmethod
    def _result(value: object) -> object:
        if isinstance(value, BaseException):
            raise value
        return value

    async def async_get_events(
        self,
        *_args: object,
        limit: int,
        **_kwargs: object,
    ) -> list:
        self.requested_limit = limit
        return self._result(self.events)

    async def async_get_tag_list(self, *_args: object) -> dict:
        return self._result(self.tags)

    async def async_resolve_media(
        self,
        *_args: object,
    ) -> dict:
        return self._result(self.media)


class CardFeedTest(unittest.IsolatedAsyncioTestCase):
    """Test bounded, on-demand card responses."""

    def setUp(self) -> None:
        """Create a loader."""
        self.api = FakeCardApi()
        self.loader = card_feed.BirdfyCardFeedLoader(
            self.api,
            serial_number="SERIAL",
            device_name="Garden Feeder",
            region="eu",
            timezone_name="UTC",
            date_range="today",
        )

    async def test_feed_is_bounded_and_contains_temporary_media(self) -> None:
        """Return signed media only in the on-demand response."""
        self.api.events = [
            {
                "alarmId": index,
                "alertTime": index,
                "aiLabels": ["Robin"],
            }
            for index in range(30)
        ]
        self.api.media = {
            index: {
                "images": [f"https://example.test/{index}.jpg"],
                "videos": [],
            }
            for index in range(20)
        }

        response = await self.loader.async_load({}, limit=100)

        self.assertEqual(self.api.requested_limit, 20)
        self.assertEqual(len(response["moments"]), 20)
        self.assertIn("image_url", response["moments"][0])

    async def test_current_statistics_are_reused(self) -> None:
        """Avoid extra statistics calls when coordinator data is available."""
        self.api.events = [{"alarmId": 1, "alertTime": 1}]

        response = await self.loader.async_load(
            {
                "total_moments": 12,
                "bird_visits": 9,
                "species_count": 3,
                "species_counts": {"Robin": 9},
            },
            limit=10,
        )

        self.assertEqual(response["total_moments"], 12)
        self.assertEqual(response["bird_visits"], 9)

    async def test_media_failure_is_partial(self) -> None:
        """Keep textual feed data when media resolution fails."""
        self.api.events = [{"alarmId": 1, "alertTime": 1}]
        self.api.media = RuntimeError("media failed")

        response = await self.loader.async_load({}, limit=10)

        self.assertEqual(response["partial_failures"], ["media"])
        self.assertIsNone(response["moments"][0]["image_url"])

    async def test_feed_failure_is_fatal(self) -> None:
        """Surface an unavailable feed to the card."""
        self.api.events = RuntimeError("feed failed")

        with self.assertRaises(card_feed.CardFeedError):
            await self.loader.async_load({}, limit=10)

    async def test_cancellation_is_propagated(self) -> None:
        """Allow Home Assistant to cancel an on-demand request."""
        self.api.tags = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.loader.async_load({}, limit=10)


if __name__ == "__main__":
    unittest.main()
