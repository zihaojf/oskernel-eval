"""lmbench parser.

lmbench's output is a mix of labelled lines (``Simple syscall: 12.34
microseconds``) and bare number pairs (``0.500000 1234.56``).  The test script
runs a fixed list of commands, so the unlabelled metrics are recovered from
their position inside the four echo-labelled sections.  The expected ranges
come from ``doc/lmbench.md``.
"""

from __future__ import annotations

import re

from ..model import (
    GroupResult,
    Metric,
    TestPoint,
    STATUS_FAIL,
    STATUS_FULL,
    STATUS_PARTIAL,
    range_quality,
)
from ..references import LMBENCH_SPECS, LmbenchSpec
from .common import convert_unit, to_float

SECTION_MARKERS = (
    ("latency", "latency measurements"),
    ("fs_latency", "file system latency"),
    ("bandwidth", "Bandwidth measurements"),
    ("ctx", "context switch overhead"),
)

TWO_NUMBER_RE = re.compile(r"^\s*([0-9.]+)\s+([0-9.]+)\s*$")
CTX_LINE_RE = re.compile(r"^\s*96[:\s]\s*([0-9.]+)\s*$")


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "preamble"
    sections[current] = []
    for line in body.splitlines():
        stripped = line.strip()
        marker = next((name for name, text in SECTION_MARKERS if stripped == text), None)
        if marker:
            current = marker
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def _first_match(patterns: tuple[str, ...], text: str, nth: int = 1) -> re.Match[str] | None:
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, re.MULTILINE))
        if len(matches) >= nth:
            return matches[nth - 1]
    return None


def _extract_labelled(
    spec: LmbenchSpec, body: str
) -> tuple[float | None, str]:
    match = _first_match(spec.patterns, body, spec.nth)
    if not match:
        return None, ""
    value = to_float(match.group(1))
    found_unit = ""
    if match.lastindex and match.lastindex >= 2 and match.group(2):
        found_unit = match.group(2)
    return value, found_unit


def _extract_positional(
    index: int,
    sections: dict[str, str],
    body: str,
) -> tuple[float | None, str]:
    latency = sections.get("latency", "")
    fs_section = sections.get("fs_latency", "")
    bandwidth = sections.get("bandwidth", "")
    ctx = sections.get("ctx", "")

    if index == 17:  # lat_mmap -> "<MB> <microseconds>"
        lines = [m for m in (TWO_NUMBER_RE.match(line) for line in latency.splitlines()) if m]
        if lines:
            return to_float(lines[-1].group(2)), "us"
        return None, ""

    if index == 19:  # lat_fs -> "10k 581 105675 191452"
        for line in reversed(fs_section.splitlines()):
            fields = line.split()
            if len(fields) >= 4:
                try:
                    create = float(fields[-2])
                    delete = float(fields[-1])
                except ValueError:
                    continue
                return create + delete, "creations+deletes/sec"
        return None, ""

    if index in (20, 21, 22, 23):
        lines = [m for m in (TWO_NUMBER_RE.match(line) for line in bandwidth.splitlines()) if m]
        offset = 0 if index in (20, 21) else 2
        position = offset + (0 if index in (20, 22) else 1)
        if position < len(lines):
            return to_float(lines[position].group(2)), "MB/sec"
        return None, ""

    if index == 24:  # lat_ctx -> "2 1.23" ... "96 5.67"
        match = CTX_LINE_RE.search(ctx)
        if match:
            return to_float(match.group(1)), "us"
        lines = [m for m in (TWO_NUMBER_RE.match(line) for line in ctx.splitlines()) if m]
        if lines:
            return to_float(lines[-1].group(2)), "us"
        return None, ""

    return None, ""



def score_lmbench(group: GroupResult, baseline: dict[str, float]) -> None:
    sections = _split_sections(group.body)
    points: list[TestPoint] = []
    missing: list[str] = []

    for spec in LMBENCH_SPECS:
        value: float | None = None
        found_unit = ""

        # Prefer labelled output when the metric has patterns; fall back to the
        # positional parser for the bare-number commands.
        if spec.patterns:
            value, found_unit = _extract_labelled(spec, group.body)
        if value is None:
            value, found_unit = _extract_positional(spec.index, sections, group.body)

        if value is None:
            missing.append(f"{spec.index}. {spec.title}")
            continue

        unit = found_unit or spec.unit
        if unit != spec.unit:
            value = convert_unit(value, unit, spec.unit)
            unit = spec.unit

        quality = range_quality(value, spec.low, spec.high, spec.direction)
        status = (
            STATUS_FULL
            if quality >= 0.8
            else STATUS_PARTIAL
            if quality > 0
            else STATUS_FAIL
        )
        points.append(
            TestPoint(
                name=f"{spec.index:02d} {spec.title}",
                score=quality,
                max_score=1.0,
                status=status,
                kind="performance",
                note=f"{value:g} {unit} (expected {spec.low:g}-{spec.high:g} {spec.unit})",
                extra={
                    "key": spec.key,
                    "value": value,
                    "unit": unit,
                    "range_low": spec.low,
                    "range_high": spec.high,
                },
            )
        )
        group.metrics.append(
            Metric(
                name=f"lmbench/{spec.key}",
                value=value,
                unit=unit,
                direction=spec.direction,
                range_low=spec.low,
                range_high=spec.high,
                reference_kind="doc-range",
                quality=quality,
                delta_pct=None,
            )
        )
    if missing:
        points.append(
            TestPoint(
                name="lmbench/missing-metrics",
                score=0.0,
                max_score=float(len(missing)),
                status=STATUS_FAIL,
                kind="performance",
                note="missing: " + "; ".join(missing),
            )
        )
        group.warnings.append(
            f"lmbench: {len(missing)} of {len(LMBENCH_SPECS)} metrics missing"
        )

    group.points.extend(points)
