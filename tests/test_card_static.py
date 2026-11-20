"""Static checks for the bundled Birdfy Moments card."""

from __future__ import annotations

from pathlib import Path
import unittest

CARD_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "birdfy"
    / "www"
    / "birdfy-moments-card.js"
)


class CardStaticTest(unittest.TestCase):
    """Protect the card's on-demand and privacy design."""

    @classmethod
    def setUpClass(cls) -> None:
        """Read the bundled card once."""
        cls.source = CARD_PATH.read_text()

    def test_card_uses_bounded_response_service(self) -> None:
        """Fetch the feed through the response service."""
        self.assertIn('service: "get_moments"', self.source)
        self.assertIn("return_response: true", self.source)
        self.assertIn("Math.min(20", self.source)

    def test_card_has_device_selector_and_localization(self) -> None:
        """Expose a feeder selector and English/Croatian strings."""
        self.assertIn('selector: { device: { integration: "birdfy" } }', self.source)
        self.assertIn("const I18N", self.source)
        self.assertIn("Birdfy trenuci", self.source)

    def test_card_keeps_feed_in_memory_only(self) -> None:
        """Avoid state attributes and browser persistence."""
        self.assertNotIn(".states", self.source)
        self.assertNotIn("localStorage", self.source)
        self.assertNotIn("sessionStorage", self.source)
        self.assertIn("CACHE_TTL_MS = 60_000", self.source)

    def test_card_avoids_dynamic_code_execution(self) -> None:
        """Do not introduce eval-style script execution."""
        self.assertNotIn("eval(", self.source)
        self.assertNotIn("new Function(", self.source)


if __name__ == "__main__":
    unittest.main()
