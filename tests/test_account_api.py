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

for module_name in ("const", "account_api"):
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
        self.assertEqual(account_api.password_digest(digest), digest.lower())

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


if __name__ == "__main__":
    unittest.main()
