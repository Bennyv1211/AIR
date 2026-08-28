from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.models import UsageRecord

RECENT_WINDOW_DAYS = 7
BASELINE_WINDOW_DAYS = 28


def build_usage_overrides(usage_records: list[UsageRecord]) -> dict[str, float]:
    by_sku: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in usage_records:
        by_sku[record.sku].append(record)

    overrides: dict[str, float] = {}
    for sku, records in by_sku.items():
        override = _usage_override_for_sku(records)
        if override is not None:
            overrides[sku] = override
    return overrides


def _usage_override_for_sku(records: list[UsageRecord]) -> float | None:
    sorted_records = sorted(records, key=lambda item: item.usage_date)
    latest_date = sorted_records[-1].usage_date
    recent_cutoff = latest_date - timedelta(days=RECENT_WINDOW_DAYS - 1)
    baseline_cutoff = latest_date - timedelta(days=BASELINE_WINDOW_DAYS - 1)

    recent_records = [record for record in sorted_records if record.usage_date >= recent_cutoff]
    baseline_records = [record for record in sorted_records if record.usage_date >= baseline_cutoff]
    if not baseline_records:
        return None

    recent_average = sum(record.units_used for record in recent_records) / max(len(recent_records), 1)
    baseline_average = sum(record.units_used for record in baseline_records) / max(len(baseline_records), 1)

    if baseline_average <= 0:
        return recent_average if recent_average > 0 else None

    trend_ratio = recent_average / baseline_average
    trend_adjustment = min(max(trend_ratio, 0.65), 1.5)
    return max(recent_average * trend_adjustment, 0.01)
