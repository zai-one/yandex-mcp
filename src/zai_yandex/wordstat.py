"""Validated Wordstat request bodies for Yandex Cloud Search API v2."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

DEVICES = frozenset({"DEVICE_ALL", "DEVICE_DESKTOP", "DEVICE_PHONE", "DEVICE_TABLET"})
PERIODS = frozenset({"PERIOD_DAILY", "PERIOD_WEEKLY", "PERIOD_MONTHLY"})


def request_body(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if operation == "getRegionsTree":
        if arguments:
            raise ValueError("region tree has no client parameters")
        return {}
    if operation not in {"dynamics", "regions"}:
        raise ValueError("unknown Wordstat operation")
    phrase = arguments["phrase"]
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 400:
        raise ValueError("phrase must contain 1-400 characters")
    devices = arguments.get("devices")
    if devices is None:
        devices = ["DEVICE_ALL"]
    if (
        not isinstance(devices, list)
        or not 1 <= len(devices) <= 3
        or any(device not in DEVICES for device in devices)
        or len(set(devices)) != len(devices)
        or ("DEVICE_ALL" in devices and len(devices) != 1)
    ):
        raise ValueError("invalid Wordstat devices")
    payload = {"phrase": phrase, "devices": devices}
    if operation == "regions":
        region = arguments.get("region", "REGION_ALL")
        if region not in {"REGION_ALL", "REGION_CITIES", "REGION_REGIONS"}:
            raise ValueError("invalid region grouping")
        return {**payload, "region": region}
    period = arguments["period"]
    if period not in PERIODS:
        raise ValueError("invalid Wordstat aggregation period")
    dates = []
    for name in ("from_date", "to_date"):
        value = arguments[name]
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("dates must use YYYY-MM-DD")
        dates.append(date.fromisoformat(value))
    if dates[1] < dates[0]:
        raise ValueError("end date precedes start date")
    regions = arguments.get("regions") or []
    if (
        not isinstance(regions, list)
        or len(regions) > 100
        or any(not isinstance(value, str) or re.fullmatch(r"[0-9]{1,18}", value) is None for value in regions)
    ):
        raise ValueError("invalid Wordstat region IDs")
    return {
        **payload,
        "period": period,
        "fromDate": dates[0].isoformat() + "T00:00:00Z",
        "toDate": dates[1].isoformat() + "T00:00:00Z",
        "regions": regions,
    }
