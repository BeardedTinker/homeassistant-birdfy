"""Authenticated Netvue/Birdfy account API client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time
from typing import Any
import uuid

import aiohttp

from .const import AUTH_BASE_URL, DEFAULT_UCID

RET_OK = 0
RET_USER_SIGNATURE_INVALID = 73
RET_TIME_INVALID = 84
RET_TOKEN_EXPIRED = 113
RET_REFRESH_TOKEN_NOT_MATCH = 117
RET_REFRESH_TOKEN_EXPIRED = 118
RET_USER_HAS_NEVER_LOGIN = 128
AUTH_ERROR_CODES = {
    RET_USER_SIGNATURE_INVALID,
    RET_TOKEN_EXPIRED,
    RET_REFRESH_TOKEN_NOT_MATCH,
    RET_REFRESH_TOKEN_EXPIRED,
    RET_USER_HAS_NEVER_LOGIN,
}
BIRDFY_PRODUCT_CODE_RE = re.compile(r"^[PQ][A-Z0-9]{2}$")


class BirdfyAccountApiError(Exception):
    """Base exception for authenticated Birdfy API failures."""


class BirdfyAuthenticationError(BirdfyAccountApiError):
    """Raised when Birdfy authentication fails."""


@dataclass(frozen=True, slots=True)
class BirdfyAuthState:
    """Credentials that must survive a Home Assistant restart."""

    token: str
    refresh_token: str
    user_id: int | str
    region: str
    udid: str


CredentialsUpdatedCallback = Callable[[BirdfyAuthState], Awaitable[None]]


def password_digest(password: str) -> str:
    """Return the MD5 digest required by the Netvue login API."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", password):
        return password.lower()
    return hashlib.md5(  # noqa: S324 - required by the remote API
        password.encode(),
        usedforsecurity=False,
    ).hexdigest()


def compute_nvs_signature(
    token: str,
    ucid: str,
    udid: str,
    user_id: str | int,
    timestamp_ms: str | int,
) -> str:
    """Compute the chained HMAC signature expected by Netvue API v2."""

    def digest(key: str, message: str) -> str:
        return hmac.new(
            key.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

    key = digest(f"nvs1{token}", str(ucid))
    key = digest(key, str(udid))
    key = digest(key, str(user_id))
    key = digest(key, str(timestamp_ms))
    return digest(key, "nvs1_request")


def _return_code(data: object) -> int | None:
    """Return a normalized Netvue response code."""
    if not isinstance(data, dict) or "ret" not in data:
        return None
    try:
        return int(data["ret"])
    except (TypeError, ValueError):
        return None


class BirdfyAccountApi:
    """Authenticated account API with bounded retries and token persistence."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        refresh_token: str | None = None,
        user_id: int | str | None = None,
        region: str | None = None,
        udid: str | None = None,
        credentials_updated: CredentialsUpdatedCallback | None = None,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._username = (username or "").strip()
        self._password = password or ""
        self._token = token or ""
        self._refresh_token = refresh_token or ""
        self._user_id = user_id or 0
        self._region = region or ""
        self._udid = udid or f"web-{uuid.uuid4().hex}"
        self._credentials_updated = credentials_updated
        self._time_source = time_source
        self._clock_skew_ms = 0
        self._auth_lock = asyncio.Lock()

    @property
    def auth_state(self) -> BirdfyAuthState:
        """Return current renewable authentication state."""
        return BirdfyAuthState(
            token=self._token,
            refresh_token=self._refresh_token,
            user_id=self._user_id,
            region=self._region,
            udid=self._udid,
        )

    def _build_headers(self, *, signed: bool) -> dict[str, str]:
        """Build request headers without exposing the access token."""
        headers = {
            "accept-language": "en",
            "x-nvs-ucid": DEFAULT_UCID,
            "x-nvs-udid": self._udid,
        }
        if not signed:
            return headers

        headers["x-nvs-version"] = '{"signature":2}'
        if not self._token or not self._user_id:
            return headers

        timestamp = str(
            int(self._time_source() * 1000) - self._clock_skew_ms
        )
        headers.update(
            {
                "x-nvs-userid": str(self._user_id),
                "x-nvs-time": timestamp,
                "x-nvs-signature": compute_nvs_signature(
                    self._token,
                    DEFAULT_UCID,
                    self._udid,
                    self._user_id,
                    timestamp,
                ),
            }
        )
        return headers

    async def _async_read_json(self, response: Any) -> Any:
        """Read a JSON response with a stable integration error."""
        try:
            return await response.json(content_type=None)
        except (TypeError, ValueError) as err:
            raise BirdfyAccountApiError(
                "Birdfy API returned an invalid response"
            ) from err

    async def _async_store_auth(self, data: dict[str, Any]) -> None:
        """Apply and persist refreshed authentication data."""
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise BirdfyAuthenticationError(
                "Birdfy authentication response did not contain a token"
            )

        self._token = token
        refresh_token = data.get("refreshToken")
        if isinstance(refresh_token, str) and refresh_token:
            self._refresh_token = refresh_token
        if data.get("userID") not in (None, ""):
            self._user_id = data["userID"]
        if isinstance(data.get("region"), str):
            self._region = data["region"]

        if self._credentials_updated is not None:
            await self._credentials_updated(self.auth_state)

    async def async_login(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> BirdfyAuthState:
        """Authenticate with a Birdfy/Netvue account."""
        login_username = (username or self._username).strip()
        login_password = password if password is not None else self._password
        if not login_username or not login_password:
            raise BirdfyAuthenticationError(
                "Birdfy username and password are required"
            )

        try:
            async with self._session.post(
                f"{AUTH_BASE_URL}users/login/v2",
                json={
                    "username": login_username,
                    "password": password_digest(login_password),
                    "locale": "en",
                    "platform": 0,
                },
                headers=self._build_headers(signed=False),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await self._async_read_json(response)
                code = _return_code(data)
                if response.status >= 400 or code not in (None, RET_OK):
                    raise BirdfyAuthenticationError(
                        f"Birdfy login failed (status={response.status}, code={code})"
                    )
                if not isinstance(data, dict):
                    raise BirdfyAuthenticationError(
                        "Birdfy login returned an invalid response"
                    )
                self._username = login_username
                self._password = login_password
                await self._async_store_auth(data)
                return self.auth_state
        except BirdfyAccountApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BirdfyAccountApiError(
                "Unable to reach the Birdfy authentication service"
            ) from err

    async def async_refresh_token(self, failed_token: str | None = None) -> bool:
        """Refresh authentication once, sharing work between concurrent calls."""
        async with self._auth_lock:
            # Another request refreshed the token while this caller waited.
            if failed_token and self._token and self._token != failed_token:
                return True

            if self._token and self._refresh_token:
                try:
                    async with self._session.post(
                        f"{AUTH_BASE_URL}auth/refreshtoken",
                        json={
                            "token": self._token,
                            "refreshToken": self._refresh_token,
                        },
                        headers=self._build_headers(signed=True),
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as response:
                        data = await self._async_read_json(response)
                        if (
                            response.status < 400
                            and _return_code(data) in (None, RET_OK)
                            and isinstance(data, dict)
                            and data.get("token")
                        ):
                            await self._async_store_auth(data)
                            return True
                except (
                    aiohttp.ClientError,
                    BirdfyAccountApiError,
                    TimeoutError,
                ):
                    pass

            if self._username and self._password:
                await self.async_login()
                return True
            return False

    async def _async_request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        signed: bool = True,
    ) -> Any:
        """Make one API request with bounded clock and authentication retries."""
        if signed and not self._token:
            await self.async_login()

        clean_params = (
            {
                str(key): str(value)
                for key, value in params.items()
                if value is not None
            }
            if params
            else None
        )
        adjusted_clock = False
        refreshed_auth = False

        for _attempt in range(3):
            request_token = self._token
            try:
                async with self._session.request(
                    method,
                    url,
                    params=clean_params,
                    json=json_data,
                    headers=self._build_headers(signed=signed),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 204:
                        return {}
                    data = await self._async_read_json(response)
                    code = _return_code(data)
                    if response.status < 400 and code in (None, RET_OK):
                        return data

                    if code == RET_TIME_INVALID and not adjusted_clock:
                        adjusted_clock = self._adjust_clock(data)
                        if adjusted_clock:
                            continue

                    if (
                        not refreshed_auth
                        and (
                            code in AUTH_ERROR_CODES
                            or response.status in (401, 403)
                        )
                    ):
                        refreshed_auth = await self.async_refresh_token(
                            request_token
                        )
                        if refreshed_auth:
                            continue

                    raise BirdfyAccountApiError(
                        "Birdfy API request failed "
                        f"(status={response.status}, code={code})"
                    )
            except BirdfyAccountApiError:
                raise
            except (aiohttp.ClientError, TimeoutError) as err:
                raise BirdfyAccountApiError(
                    "Unable to reach the Birdfy API"
                ) from err

        raise BirdfyAccountApiError("Birdfy API retry limit reached")

    def _adjust_clock(self, data: object) -> bool:
        """Update local clock skew from a Netvue error response."""
        if not isinstance(data, dict):
            return False
        raw_message = data.get("msg")
        try:
            message = (
                json.loads(raw_message)
                if isinstance(raw_message, str)
                else raw_message
            )
            server_time = int(message.get("time", 0))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False
        if server_time <= 0:
            return False

        self._clock_skew_ms = int(self._time_source() * 1000) - server_time
        return True

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return recognized Birdfy feeder cameras on the account."""
        data = await self._async_request(
            "GET",
            f"{AUTH_BASE_URL}devices/v3",
        )
        raw_devices = data.get("devices", []) if isinstance(data, dict) else []
        devices: list[dict[str, Any]] = []

        for raw_device in raw_devices:
            if not isinstance(raw_device, dict) or not raw_device.get("serialNumber"):
                continue
            device = dict(raw_device)
            raw_ability = device.get("ability")
            if isinstance(raw_ability, str):
                try:
                    device["ability"] = json.loads(raw_ability)
                except ValueError:
                    pass

            serial_number = str(device["serialNumber"])
            product_code = str(device.get("productCode") or "")
            device_name = str(device.get("deviceName") or "").lower()
            if (
                BIRDFY_PRODUCT_CODE_RE.fullmatch(product_code)
                or (len(serial_number) >= 4 and serial_number[2] == "7")
                or "birdfy" in device_name
                or "bird feeder" in device_name
            ):
                devices.append(device)

        return devices
