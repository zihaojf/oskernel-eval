"""Small helpers shared by the local (performance) judges."""

from __future__ import annotations

import re
from typing import Iterable

from ..model import Metric, clamp


NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)")


def to_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = NUMBER_RE.search(value)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def to_int(value: str | None, default: int = 0) -> int:
    return int(to_float(value, default))


# Multipliers to a canonical unit.
_TIME_TO_US = {"us": 1.0, "µs": 1.0, "microsecond": 1.0, "microseconds": 1.0,
               "ms": 1000.0, "s": 1_000_000.0}
_BW_TO_KB = {"KB": 1.0, "K": 1.0, "MB": 1024.0, "M": 1024.0,
             "GB": 1024.0 * 1024.0, "G": 1024.0 * 1024.0}
_BW_TO_MB = {"KB": 1.0 / 1024.0, "K": 1.0 / 1024.0, "MB": 1.0, "M": 1.0,
             "GB": 1024.0, "G": 1024.0}


def convert_unit(value: float, found_unit: str, wanted_unit: str) -> float:
    """Convert a bandwidth/time value to the unit used by the reference table."""

    found = found_unit.strip()
    wanted = wanted_unit.strip()
    if found == wanted:
        return value
    if wanted in ("KB/sec", "KB/s"):
        return value * _BW_TO_KB.get(found, 1.0)
    if wanted in ("MB/sec", "MB/s"):
        return value * _BW_TO_MB.get(found, 1.0)
    if wanted in ("us", "µs"):
        return value * _TIME_TO_US.get(found, 1.0)
    return value


def add_metric(
    metrics: list[Metric],
    *,
    name: str,
    value: float,
    unit: str = "",
    direction: str = "higher",
    reference: float | None = None,
    reference_kind: str = "",
    range_low: float | None = None,
    range_high: float | None = None,
    baseline: float | None = None,
    note: str = "",
) -> Metric:
    """Append a metric and compute its quality/delta fields."""

    quality: float | None = None
    if range_low is not None and range_high is not None:
        if direction == "lower":
            quality = clamp((range_high - value) / (range_high - range_low))
        else:
            quality = clamp((value - range_low) / (range_high - range_low))
        reference_kind = reference_kind or "doc-range"
    elif baseline is not None and baseline > 0:
        ratio = value / baseline if direction == "higher" else baseline / value
        quality = clamp(ratio)
        reference = baseline
        reference_kind = reference_kind or "baseline"

    delta_pct = None
    if baseline is not None and baseline > 0:
        delta_pct = (value - baseline) / baseline * 100.0

    metric = Metric(
        name=name,
        value=value,
        unit=unit,
        direction=direction,
        reference=reference,
        reference_kind=reference_kind,
        range_low=range_low,
        range_high=range_high,
        quality=quality,
        delta_pct=delta_pct,
        note=note,
    )
    metrics.append(metric)
    return metric


def missing_names(expected: Iterable[str], seen: Iterable[str]) -> list[str]:
    seen_set = set(seen)
    return [name for name in expected if name not in seen_set]


def find_sections(
    body: str,
    start_re: re.Pattern[str],
    end_re: re.Pattern[str],
) -> list[tuple[str, str, bool]]:
    """Split a script body into ``(name, text, complete)`` sections.

    Used by iperf/netperf/cyclictest: each case prints
    ``====== <tool> <NAME> begin ======`` and a matching ``end`` line.
    """

    sections: list[tuple[str, str, bool]] = []
    name = ""
    buffer: list[str] = []
    open_case = False
    for line in body.splitlines():
        start = start_re.search(line)
        if start:
            if open_case:
                sections.append((name, "\n".join(buffer), False))
            name = start.group(1).strip()
            buffer = []
            open_case = True
            continue
        end = end_re.search(line)
        if end and open_case:
            sections.append((name, "\n".join(buffer), True))
            open_case = False
            buffer = []
            continue
        if open_case:
            buffer.append(line)
    if open_case:
        sections.append((name, "\n".join(buffer), False))
    return sections


def find_status_sections(
    body: str,
    start_re: re.Pattern[str],
    end_re: re.Pattern[str],
) -> list[tuple[str, str, bool, str]]:
    """Like :func:`find_sections`, but also capture ``end: success/fail``.

    ``end_re`` must expose the case name in group 1 and the status in group 2.
    """

    sections: list[tuple[str, str, bool, str]] = []
    name = ""
    buffer: list[str] = []
    open_case = False
    for line in body.splitlines():
        start = start_re.search(line)
        if start:
            if open_case:
                sections.append((name, "\n".join(buffer), False, ""))
            name = start.group(1).strip()
            buffer = []
            open_case = True
            continue
        end = end_re.search(line)
        if end and open_case:
            sections.append(
                (name, "\n".join(buffer), True, end.group(2).strip().lower())
            )
            open_case = False
            buffer = []
            continue
        if open_case:
            buffer.append(line)
    if open_case:
        sections.append((name, "\n".join(buffer), False, ""))
    return sections
