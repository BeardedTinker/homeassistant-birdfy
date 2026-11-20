"""Tests for the authenticated Birdfy account API."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "birdfy"


class FakeClientError(Exception):
    """Stand-in for aiohttp.ClientError."""


class FakeClientTimeout:
    """Stand-in for aiohttp.ClientTimeout."""

    def __init__(self, *, total: int) -> None:
        self.total = total


aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientError = FakeClientError
aiohttp.ClientTimeout = FakeClientTimeout
aiohttp.ClientSession = object
previous_aiohttp = sys.modules.get("aiohttp")
sys.modules["aiohttp"] = aiohttp

package = types.ModuleType("birdfy_account_test")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules[package.__name__] = package

for module_name in (
    "const",
    "config_data",
    "media",
    "moments",
    "account_api",
):
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.{module_name}",
        PACKAGE_PATH / f"{module_name}.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

account_api = sys.modules["birdfy_account_test.account_api"]
if previous_aiohttp is None:
    del sys.modules["aiohttp"]
else:
    sys.modules["aiohttp"] = previous_aiohttp


class FakeResponse:
    """Async response context manager."""

    def __init__(self, status: int, data: object) -> None:
        self.status = status
        self._data = data

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> object:
        del content_type
        return self._data


class FakeSession:
    """Queue-based aiohttp session fake."""

    def __init__(
        self,
        *,
        posts: list[FakeResponse] | None = None,
        requests: list[FakeResponse] | None = None,
    ) -> None:
        self.posts = posts or []
        self.requests = requests or []
        self.post_calls: list[dict[str, object]] = []
        self.request_calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self.posts.pop(0)

    def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        self.request_calls.append({"method": method, "url": url, **kwargs})
        return self.requests.pop(0)


class AuthenticationTest(unittest.IsolatedAsyncioTestCase):
    """Test login, signing and token renewal."""

    def test_password_digest_accepts_existing_digest(self) -> None:
        """Avoid hashing an already-hashed password twice."""
        digest = "A" * 32
        self.assertEqual(
            sys.modules["birdfy_account_test.config_data"].password_digest(
                digest
            ),
            digest.lower(),
        )

    def test_signature_is_stable(self) -> None:
        """Protect the reverse-engineered signature algorithm."""
        self.assertEqual(
            account_api.compute_nvs_signature(
                "token",
                "ucid",
                "udid",
                42,
                123456,
            ),
            "6d36b37626a1bfbd1e3c782570b5b98a23316987d6c796357d5c6b119624a971",
        )

    async def test_login_persists_renewable_state(self) -> None:
        """Persist refreshed tokens through the supplied callback."""
        stored = []

        async def store(state: object) -> None:
            stored.append(state)

        session = FakeSession(
            posts=[
                FakeResponse(
                    200,
                    {
                        "ret": 0,
                        "token": "access",
                        "refreshToken": "refresh",
                        "userID": 42,
                        "region": "eu",
                    },
                )
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            username="person@example.com",
            password="secret",
            udid="test-udid",
            credentials_updated=store,
        )

        state = await client.async_login()

        self.assertEqual(state.token, "access")
        self.assertEqual(state.refresh_token, "refresh")
        self.assertEqual(len(stored), 1)
        payload = session.post_calls[0]["json"]
        self.assertNotEqual(payload["password"], "secret")

    async def test_auth_failure_refreshes_and_retries_once(self) -> None:
        """Retry a failed request with a newly refreshed token."""
        session = FakeSession(
            posts=[
                FakeResponse(
                    200,
                    {
                        "ret": 0,
                        "token": "new-token",
                        "refreshToken": "new-refresh",
                    },
                )
            ],
            requests=[
                FakeResponse(401, {"ret": 113}),
                FakeResponse(200, {"ret": 0, "devices": []}),
            ],
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="old-token",
            refresh_token="old-refresh",
            user_id=42,
            udid="test-udid",
        )

        devices = await client.async_get_devices()

        self.assertEqual(devices, [])
        self.assertEqual(len(session.request_calls), 2)
        self.assertEqual(client.auth_state.token, "new-token")

    async def test_concurrent_waiter_reuses_refreshed_token(self) -> None:
        """Do not perform a second refresh after another request succeeded."""
        session = FakeSession()
        client = account_api.BirdfyAccountApi(
            session,
            token="new-token",
            refresh_token="refresh",
            user_id=42,
        )

        refreshed = await client.async_refresh_token("old-token")

        self.assertTrue(refreshed)
        self.assertEqual(session.post_calls, [])

    async def test_clock_skew_is_corrected_once(self) -> None:
        """Retry with server time after a clock-invalid response."""
        session = FakeSession(
            requests=[
                FakeResponse(400, {"ret": 84, "msg": '{"time": 999000}'}),
                FakeResponse(200, {"ret": 0, "devices": []}),
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
            udid="test-udid",
            time_source=lambda: 1000.0,
        )

        await client.async_get_devices()

        first_headers = session.request_calls[0]["headers"]
        second_headers = session.request_calls[1]["headers"]
        self.assertEqual(first_headers["x-nvs-time"], "1000000")
        self.assertEqual(second_headers["x-nvs-time"], "999000")

    async def test_error_does_not_expose_request_url(self) -> None:
        """Keep device identifiers out of surfaced API errors."""
        session = FakeSession(
            requests=[FakeResponse(500, {"ret": 999})]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
        )

        with self.assertRaises(account_api.BirdfyAccountApiError) as context:
            await client._async_request(  # pylint: disable=protected-access
                "GET",
                "https://example.test/devices/SECRET-SERIAL/events",
            )

        self.assertNotIn("SECRET-SERIAL", str(context.exception))


class DeviceDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """Test safe feeder discovery."""

    async def test_non_birdfy_cameras_are_not_returned(self) -> None:
        """Never fall back to treating every Netvue camera as a feeder."""
        session = FakeSession(
            requests=[
                FakeResponse(
                    200,
                    {
                        "ret": 0,
                        "devices": [
                            {
                                "serialNumber": "AA1234",
                                "productCode": "CAM",
                                "deviceName": "Garage",
                            },
                            {
                                "serialNumber": "AB7123",
                                "productCode": "P1A",
                                "deviceName": "Garden Birdfy",
                                "ability": '{"ai": true}',
                            },
                        ],
                    },
                )
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
        )

        devices = await client.async_get_devices()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["deviceName"], "Garden Birdfy")
        self.assertEqual(devices[0]["ability"], {"ai": True})


class MomentsEndpointTest(unittest.IsolatedAsyncioTestCase):
    """Test Moments endpoint behavior and response filtering."""

    async def test_tag_list_is_cached_by_validated_region(self) -> None:
        """Fetch each regional tag list once."""
        session = FakeSession(
            requests=[
                FakeResponse(
                    200,
                    [
                        {
                            "key": "3_northern_cardinal",
                            "name": "Northern Cardinal",
                        }
                    ],
                )
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
            region="EU",
        )

        first = await client.async_get_tag_list()
        second = await client.async_get_tag_list()

        self.assertEqual(first["northern cardinal"], "Northern Cardinal")
        self.assertIs(first, second)
        self.assertEqual(len(session.request_calls), 1)
        self.assertTrue(
            session.request_calls[0]["url"].startswith(
                "https://eu-api2.netvue.co/"
            )
        )

    async def test_invalid_region_uses_default_host(self) -> None:
        """Do not interpolate untrusted region values into a hostname."""
        session = FakeSession(requests=[FakeResponse(200, [])])
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
            region="eu.example.test/path",
        )

        await client.async_get_tag_list()

        self.assertTrue(
            session.request_calls[0]["url"].startswith(
                "https://api2.netvue.co/"
            )
        )

    async def test_bydate_failure_falls_back_and_encodes_serial(self) -> None:
        """Fall back to the range endpoint without exposing path separators."""
        session = FakeSession(
            requests=[
                FakeResponse(404, {"ret": 404}),
                FakeResponse(
                    200,
                    {"ret": 0, "events": [{"alarmId": "newest"}]},
                ),
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
        )

        events = await client.async_get_events(
            "SERIAL/../OTHER",
            "today",
            timezone_name="UTC",
        )

        self.assertEqual(events, [{"alarmId": "newest"}])
        self.assertIn(
            "SERIAL%2F..%2FOTHER",
            session.request_calls[0]["url"],
        )
        self.assertEqual(len(session.request_calls), 2)

    async def test_media_resolver_filters_non_http_urls(self) -> None:
        """Only expose absolute HTTP(S) media links."""
        session = FakeSession(
            requests=[
                FakeResponse(
                    200,
                    {
                        "ret": 0,
                        "eventList": [
                            {
                                "alarmId": 7,
                                "videoFileList": [
                                    "https://example.test/video.mp4",
                                    "file:///private/video.mp4",
                                ],
                                "picList": (
                                    '["https://example.test/image.jpg",'
                                    ' "javascript:alert(1)"]'
                                ),
                            }
                        ],
                    },
                )
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
        )

        media = await client.async_resolve_media(
            "SERIAL",
            [
                {
                    "alarmId": 7,
                    "description": '{"recordDir": "dir"}',
                    "pictureList": "one.jpg,two.jpg",
                }
            ],
        )

        self.assertEqual(
            media,
            {
                7: {
                    "videos": ["https://example.test/video.mp4"],
                    "images": ["https://example.test/image.jpg"],
                }
            },
        )
        payload = session.request_calls[0]["json"]
        self.assertEqual(
            payload["eventList"][0]["pictureList"],
            ["one.jpg", "two.jpg"],
        )

    async def test_count_endpoints_normalize_responses(self) -> None:
        """Convert total counts and discard malformed species entries."""
        session = FakeSession(
            requests=[
                FakeResponse(200, {"ret": 0, "total": "12"}),
                FakeResponse(
                    200,
                    {
                        "ret": 0,
                        "tagCountList": [
                            {"tag": "Robin", "count": 3},
                            "invalid",
                        ],
                    },
                ),
            ]
        )
        client = account_api.BirdfyAccountApi(
            session,
            token="token",
            user_id=42,
        )

        total = await client.async_get_event_count(
            "SERIAL",
            100,
            200,
        )
        counts = await client.async_get_species_counts(
            "SERIAL",
            100,
            200,
        )

        self.assertEqual(total, 12)
        self.assertEqual(counts, [{"tag": "Robin", "count": 3}])
        self.assertEqual(
            session.request_calls[0]["params"]["queryStartTime"],
            "100",
        )


if __name__ == "__main__":
    unittest.main()
