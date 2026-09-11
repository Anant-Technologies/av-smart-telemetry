"""Host-local timezone helpers.

fitvid works in the host machine's local timezone. FIT files are UTC and are
converted to local on load; media creation times are interpreted as local wall
clock (cameras often stamp local time with a misleading Z).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def host_local_tz():
    """Timezone of the machine running fitvid."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def to_local(dt: datetime, *, assume_utc_if_naive: bool = True) -> datetime:
    """Convert an aware (or naive) datetime into host-local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc if assume_utc_if_naive else host_local_tz())
    return dt.astimezone(host_local_tz())


def parse_wall_clock_as_local(raw: str | None) -> datetime | None:
    """Parse a media creation_time wall clock as host-local (keep local tz).

    Strips Z / offsets and attaches the host timezone so FIT (converted to
    local) and cameras share one timeline for offset math.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None

    cleaned = raw
    if cleaned.endswith("Z") or cleaned.endswith("z"):
        cleaned = cleaned[:-1]
    cleaned = re.sub(r"[+-]\d{2}:?\d{2}$", "", cleaned).strip()

    naive: datetime | None = None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            naive = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        try:
            dt = datetime.fromisoformat(cleaned)
            naive = dt.replace(tzinfo=None)
        except ValueError:
            return None
    return naive.replace(tzinfo=host_local_tz())


def parse_datetime(value: Any, *, assume_utc_if_naive: bool = False) -> datetime | None:
    """Parse ISO / datetime into host-local time.

    - Aware values are converted to local.
    - Naive values: treat as local wall clock unless assume_utc_if_naive.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_local(value, assume_utc_if_naive=assume_utc_if_naive)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(
            host_local_tz()
        )
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    # Prefer explicit ISO with offset → to local; else wall clock as local
    try:
        cleaned = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=host_local_tz())
        return dt.astimezone(host_local_tz())
    except ValueError:
        return parse_wall_clock_as_local(s)
