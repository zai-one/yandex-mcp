"""Metrika report windows with explicit sampling and bounded pagination."""

from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import UTC, date, datetime
from typing import Any

from zai_yandex.adapters.metrika import YandexMetrikaAdapter
from zai_yandex.transport import ProviderError

MAX_BYTES = 262_144


def report_params(counter_id, date1, date2, metrics, dimensions, filters, sort, page_size):
    counter = YandexMetrikaAdapter.validate_counter_id(counter_id)
    start, end = date.fromisoformat(date1), date.fromisoformat(date2)
    if not 0 <= (end - start).days <= 92:
        raise ValueError("report windows must be between 1 and 93 days")
    if not 1 <= len(metrics) <= 12 or len(dimensions) > 5:
        raise ValueError("report requires 1-12 metrics and at most 5 dimensions")
    fields = metrics + dimensions
    if len(set(fields)) != len(fields) or any(
        not re.fullmatch(r"ym:(?:s|pv):[A-Za-z0-9_<>]+", item) for item in fields
    ):
        raise ValueError("invalid or duplicate report fields")
    if type(page_size) is not int or not 1 <= page_size <= 500:
        raise ValueError("page_size must be between 1 and 500")
    values = dict(ids=counter, date1=date1, date2=date2, metrics=",".join(metrics), limit=page_size)
    if dimensions:
        values["dimensions"] = ",".join(dimensions)
    if filters is not None:
        values["filters"] = filters
    if sort is not None:
        if any(item.removeprefix("-") not in fields for item in sort.split(",")):
            raise ValueError("sort must reference requested fields")
        values["sort"] = sort
    return YandexMetrikaAdapter.validate_statistics(values)


def _finite(value):
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _numbers(value, size):
    if (
        not isinstance(value, list)
        or len(value) != size
        or any(item is not None and (type(item) not in (int, float) or not _finite(item)) for item in value)
    ):
        raise ProviderError("invalid report metrics")
    return value


async def collect(read, params, metrics, dimensions, max_pages):
    rows, fingerprints, pages = [], set(), []
    group_keys = set()
    metadata, totals = None, None
    reason, complete, next_offset = "page_limit", False, 1
    limit = params["limit"]
    for number in range(max_pages):
        offset = number * limit + 1
        # The caller uses runtime.read: both literal and field-aware redaction precede export.
        data = await read({**params, "offset": offset})
        batch = data.get("data") if isinstance(data, dict) else None
        if not isinstance(batch, list) or len(batch) > limit:
            raise ProviderError("invalid report rows")
        current_meta = {
            key: data.get(key)
            for key in (
                "total_rows",
                "total_rows_rounded",
                "sampled",
                "sample_share",
                "sample_size",
                "sample_space",
            )
        }
        current_totals = _numbers(data.get("totals"), len(metrics))
        if metadata is not None and (metadata != current_meta or totals != current_totals):
            reason, next_offset = "report_changed", offset
            break
        metadata, totals = current_meta, current_totals
        normalized = []
        for row in batch:
            if not isinstance(row, dict):
                raise ProviderError("invalid report row")
            dims = row.get("dimensions", [])
            if (
                not isinstance(dims, list)
                or len(dims) != len(dimensions)
                or any(not isinstance(v, dict) for v in dims)
            ):
                raise ProviderError("invalid report dimensions")
            normalized.append(
                {
                    "dimensions": dict(zip(dimensions, dims, strict=True)),
                    "metrics": dict(zip(metrics, _numbers(row.get("metrics"), len(metrics)), strict=True)),
                }
            )
        fingerprint = json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if batch and fingerprint in fingerprints:
            reason, next_offset = "repeated_page", offset
            break
        batch_keys = [
            json.dumps(
                [
                    dimension.get("id") if dimension.get("id") is not None else dimension.get("name")
                    for dimension in row["dimensions"].values()
                ],
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            for row in normalized
        ]
        if len(set(batch_keys)) != len(batch_keys) or group_keys.intersection(batch_keys):
            reason, next_offset = "overlapping_dimension_groups", offset
            break
        group_keys.update(batch_keys)
        fingerprints.add(fingerprint)
        if len(json.dumps(rows + normalized, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
            reason, next_offset = "byte_limit", offset
            break
        rows.extend(normalized)
        pages.append(offset)
        next_offset = offset + len(batch)
        total = metadata["total_rows"]
        exact = type(total) is int and total >= 0 and metadata["total_rows_rounded"] is False
        if not exact:
            reason = "missing_exact_row_count"
            break
        if len(batch) != min(limit, max(0, total - offset + 1)):
            reason = "inconsistent_row_count"
            break
        if len(rows) == total:
            complete, reason, next_offset = True, "end_of_report", None
            break
    return {
        "source": {
            "provider": "yandex_metrika",
            "path": "/stat/v1/data",
            "parameters": params,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "included_offsets": pages,
        },
        "rows": rows,
        "totals": dict(zip(metrics, totals or [], strict=True)),
        "metadata": metadata,
        "row_count": len(rows),
        "rows_complete": complete,
        "stop_reason": reason,
        "next_offset": next_offset,
        "consistency": "live_requests_not_an_atomic_snapshot",
    }


def delta(before, after):
    if before is None or after is None:
        return {"before": before, "after": after, "delta": None, "change_percent": None}
    difference = after - before
    percentage = None if before == 0 else difference / abs(before) * 100
    overflow = not _finite(difference) or (percentage is not None and not _finite(percentage))
    result = {
        "before": before,
        "after": after,
        "delta": difference if _finite(difference) else None,
        "change_percent": percentage if percentage is None or _finite(percentage) else None,
    }
    if overflow:
        result["calculation_status"] = "numeric_overflow"
    return result


def as_csv(periods):
    rows = []
    for period, report in periods.items():
        for item in report["rows"]:
            row = {"period": period}
            for field, dimension in item["dimensions"].items():
                row[field + ":id"] = dimension.get("id")
                row[field + ":name"] = dimension.get("name")
            row.update(item["metrics"])
            rows.append(row)
    columns = sorted({field for row in rows for field in row})

    def cell(value):
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            value = "'" + value
        return value

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    if columns:
        writer.writerow(columns)
        writer.writerows([cell(row.get(key)) for key in columns] for row in rows)
    value = stream.getvalue()
    if len(value.encode()) > MAX_BYTES:
        raise ProviderError("export too large; request fewer rows")
    return value, columns


def register_reports(server: Any, runtime: Any):
    @server.tool(auth=runtime.require_scopes("yandex_metrika:read"))
    async def metrika_report(
        counter_id: str,
        date1: str,
        date2: str,
        metrics: list[str],
        dimensions: list[str] | None = None,
        filters: str | None = None,
        sort: str | None = None,
        compare_date1: str | None = None,
        compare_date2: str | None = None,
        page_size: int = 100,
        max_pages: int = 1,
        output: str = "json",
    ) -> dict[str, Any]:
        """Report or compare two Metrika periods, with sampling, exact totals and bounded JSON/CSV rows.

        Each period uses at most three pages. Complete rows can still be sampled; inspect metadata.
        Differences compare provider totals, never sums of partial rows. Missing values stay null.
        """
        if type(max_pages) is not int or not 1 <= max_pages <= 3 or output not in {"json", "csv"}:
            raise ValueError("invalid page limit or output format")
        if (compare_date1 is None) != (compare_date2 is None):
            raise ValueError("both comparison dates are required")
        dims = dimensions or []
        requests = {
            "current": report_params(counter_id, date1, date2, metrics, dims, filters, sort, page_size)
        }
        if compare_date1 is not None:
            requests["previous"] = report_params(
                counter_id, compare_date1, compare_date2, metrics, dims, filters, sort, page_size
            )
        adapter = runtime.registry.yandex_metrika()

        async def read(params):
            return await runtime.read(
                "yandex_metrika", "statistics", params, lambda: adapter.statistics(params)
            )

        periods = {
            key: await collect(read, params, metrics, dims, max_pages) for key, params in requests.items()
        }
        result = {"periods": periods, "format": output, "comparison": None}
        if "previous" in periods:
            result["comparison"] = {
                key: delta(periods["previous"]["totals"][key], periods["current"]["totals"][key])
                for key in metrics
            }
        if output == "csv":
            result["csv"], result["columns"] = as_csv(periods)
            result["formula_strings_escaped"] = True
            for report in periods.values():
                del report["rows"]
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
            raise ProviderError("report too large; request fewer rows")
        return result
