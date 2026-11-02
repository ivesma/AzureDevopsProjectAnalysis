"""Small helpers shared by collectors, analysers and exporters."""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable


def parse_date(value: Any) -> datetime | None:
    """Parse the ISO-8601 variants returned by Azure DevOps (7-digit fractions, 'Z')."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    match = re.match(r"^(.*?\.\d{1,6})\d*(.*)$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.year <= 1:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def days_between(start: Any, end: Any) -> float | None:
    a, b = parse_date(start), parse_date(end)
    if not a or not b:
        return None
    return (b - a).total_seconds() / 86400


def age_days(value: Any, now: datetime) -> float | None:
    return days_between(value, now)


def identity_name(identity: Any) -> str | None:
    if not identity:
        return None
    if isinstance(identity, str):
        return identity
    return identity.get("displayName") or identity.get("uniqueName") or identity.get("name")


def percentile(values: Iterable[float], pct: float) -> float | None:
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * pct / 100
    lower = int(k)
    upper = min(lower + 1, len(data) - 1)
    return data[lower] + (data[upper] - data[lower]) * (k - lower)


def summarise(values: Iterable[float]) -> dict[str, float | None]:
    data = [v for v in values if v is not None]
    if not data:
        return {"count": 0, "mean": None, "median": None, "p85": None}
    return {
        "count": len(data),
        "mean": round(statistics.fmean(data), 2),
        "median": round(statistics.median(data), 2),
        "p85": round(percentile(data, 85) or 0, 2),
    }


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower() or "item"


def safe_filename(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .") or "item"


def short_ref(ref: str | None) -> str | None:
    if not ref:
        return ref
    return ref.removeprefix("refs/heads/")
