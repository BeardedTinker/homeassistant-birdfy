"""Pure configuration helpers for Birdfy."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .const import (
    CONF_PASSWORD,
    CONF_RECAP_UUID,
    CONF_USERNAME,
    CONF_UUID,
)


def password_digest(password: str) -> str:
    """Return the MD5 digest required by the Netvue login API."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", password):
        return password.lower()
    return hashlib.md5(  # noqa: S324 - required by the remote API
        password.encode(),
        usedforsecurity=False,
    ).hexdigest()


def runtime_configuration_signature(
    data: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Return fields whose change requires a config-entry reload."""
    return (
        str(data.get(CONF_USERNAME) or ""),
        str(data.get(CONF_PASSWORD) or ""),
        str(data.get(CONF_UUID) or ""),
        str(data.get(CONF_RECAP_UUID) or ""),
    )
