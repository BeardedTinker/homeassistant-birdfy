"""The Birdfy integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .account_api import (
    BirdfyAccountApi,
    BirdfyAccountApiError,
    BirdfyAuthState,
)
from .api import BirdfyHighlightsApi, BirdfyRecapApi
from .config_data import runtime_configuration_signature
from .const import (
    CONF_DATE_RANGE,
    CONF_PASSWORD,
    CONF_RECAP_UUID,
    CONF_REFRESH_TOKEN,
    CONF_REGION,
    CONF_TOKEN,
    CONF_UDID,
    CONF_USER_ID,
    CONF_USERNAME,
    CONF_UUID,
    DEFAULT_DATE_RANGE,
    DOMAIN,
)
from .coordinator import (
    BirdfyHighlightsCoordinator,
    BirdfyMomentsCoordinator,
    BirdfyRecapCoordinator,
)
from .moments_fetcher import BirdfyMomentsFetcher
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.IMAGE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up shared Birdfy response services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Birdfy from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    entry_store: dict[str, Any] = {
        "coordinators": {},
        "recap_coordinator": None,
        "moments_coordinators": {},
        "account_api": None,
        "devices": [],
        "runtime_signature": runtime_configuration_signature(entry.data),
    }
    hass.data[DOMAIN][entry.entry_id] = entry_store

    session = async_get_clientsession(hass)
    username = str(entry.data.get(CONF_USERNAME) or "").strip()
    password = str(entry.data.get(CONF_PASSWORD) or "")
    has_account = bool(username and password)

    highlight_uuid = str(entry.data.get(CONF_UUID) or "").strip()
    if highlight_uuid:
        api = BirdfyHighlightsApi(highlight_uuid, session)
        for _subentry_id, subentry in entry.subentries.items():
            await _async_setup_subentry(
                hass,
                entry,
                subentry,
                api,
                allow_initial_failure=has_account,
            )

        if not entry.subentries:
            coordinator = BirdfyHighlightsCoordinator(
                hass,
                api,
                entry,
                subentry_id=None,
                date_range=DEFAULT_DATE_RANGE,
            )
            try:
                await coordinator.async_config_entry_first_refresh()
            except ConfigEntryNotReady:
                if not has_account:
                    raise
                _LOGGER.warning(
                    "Birdfy Highlights is unavailable; continuing with Moments"
                )
            entry_store["coordinators"]["default"] = coordinator

    recap_uuid = entry.data.get(CONF_RECAP_UUID)
    if recap_uuid:
        recap_api = BirdfyRecapApi(recap_uuid, session)
        recap_coordinator = BirdfyRecapCoordinator(hass, recap_api, entry)
        try:
            await recap_coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            if not has_account:
                raise
            _LOGGER.warning(
                "Birdfy Recap is unavailable; continuing with Moments"
            )
        entry_store["recap_coordinator"] = recap_coordinator

    if has_account:

        async def async_store_credentials(state: BirdfyAuthState) -> None:
            """Persist renewable tokens without forcing a reload."""
            new_data = {
                **entry.data,
                CONF_TOKEN: state.token,
                CONF_REFRESH_TOKEN: state.refresh_token,
                CONF_USER_ID: state.user_id,
                CONF_REGION: state.region,
                CONF_UDID: state.udid,
            }
            if new_data != entry.data:
                hass.config_entries.async_update_entry(entry, data=new_data)

        account_api = BirdfyAccountApi(
            session,
            username=username,
            password=password,
            token=entry.data.get(CONF_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            user_id=entry.data.get(CONF_USER_ID),
            region=entry.data.get(CONF_REGION),
            udid=entry.data.get(CONF_UDID),
            credentials_updated=async_store_credentials,
        )
        try:
            devices = await account_api.async_get_devices()
        except BirdfyAccountApiError as err:
            raise ConfigEntryNotReady(
                "Unable to fetch Birdfy account devices"
            ) from err
        if not devices:
            raise ConfigEntryNotReady(
                "No recognized Birdfy feeders found on the account"
            )

        entry_store["account_api"] = account_api
        entry_store["devices"] = devices
        for device in devices:
            fetcher = BirdfyMomentsFetcher(account_api, device)
            coordinator = BirdfyMomentsCoordinator(hass, fetcher, entry)
            await coordinator.async_config_entry_first_refresh()
            entry_store["moments_coordinators"][
                fetcher.serial_number
            ] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def _async_setup_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry: ConfigSubentry,
    api: BirdfyHighlightsApi,
    *,
    allow_initial_failure: bool = False,
) -> None:
    """Set up a subentry coordinator."""
    date_range = subentry.data.get(CONF_DATE_RANGE, DEFAULT_DATE_RANGE)

    coordinator = BirdfyHighlightsCoordinator(
        hass, api, entry, subentry_id=subentry.subentry_id, date_range=date_range
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if not allow_initial_failure:
            raise
        _LOGGER.warning(
            "Birdfy Highlight date range is unavailable; "
            "continuing with Moments"
        )

    hass.data[DOMAIN][entry.entry_id]["coordinators"][subentry.subentry_id] = coordinator


async def async_setup_subentry(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> bool:
    """Set up a subentry."""
    highlight_uuid = entry.data.get(CONF_UUID)
    if not highlight_uuid:
        return False

    session = async_get_clientsession(hass)
    api = BirdfyHighlightsApi(highlight_uuid, session)

    await _async_setup_subentry(
        hass,
        entry,
        subentry,
        api,
        allow_initial_failure=bool(
            entry.data.get(CONF_USERNAME)
            and entry.data.get(CONF_PASSWORD)
        ),
    )

    # Forward to platforms for this subentry
    await hass.config_entries.async_forward_entry_setups(
        entry, PLATFORMS, subentry_id=subentry.subentry_id
    )

    return True


async def async_unload_subentry(
    hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry
) -> bool:
    """Unload a subentry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS, subentry_id=subentry.subentry_id
    )

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id]["coordinators"].pop(
            subentry.subentry_id, None
        )

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    entry_store = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    signature = runtime_configuration_signature(entry.data)
    if entry_store and entry_store.get("runtime_signature") == signature:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
