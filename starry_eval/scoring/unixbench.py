"""UnixBench: one coverage point per benchmark; values are performance metrics."""

from __future__ import annotations

import re

from ..model import GroupResult, Metric, TestPoint, STATUS_FAIL, STATUS_FULL
from ..references import UNIXBENCH_EXPECTED, UNIXBENCH_UNITS
from .common import add_metric, missing_names, to_float

LINE_RE = re.compile(
    r"^Unixbench\s+(.+?)\s+test\(([^)]*)\):\s*([-+]?[0-9.]+)\s*$"
)


def score_unixbench(group: GroupResult, baseline: dict[str, float]) -> None:
    points: list[TestPoint] = []
    metrics: list[Metric] = []
    seen: list[str] = []

    for line in group.body.splitlines():
        match = LINE_RE.match(line.strip())
        if not match:
            continue
        name = match.group(1).strip()
        unit = match.group(2).strip()
        value = to_float(match.group(3))
        seen.append(name)

        point = TestPoint(
            name=name,
            score=1.0,
            max_score=1.0,
            status=STATUS_FULL,
            kind="performance",
            note=f"{value:g} {unit}",
            extra={"value": value, "unit": unit},
        )
        points.append(point)
        add_metric(
            metrics,
            name=f"unixbench/{name}",
            value=value,
            unit=unit,
            direction="higher",
            baseline=baseline.get(f"unixbench/{name}"),
        )

    missing = missing_names(UNIXBENCH_EXPECTED, seen)
    if missing:
        points.append(
            TestPoint(
                name="unixbench/missing-benchmarks",
                score=0.0,
                max_score=float(len(missing)),
                status=STATUS_FAIL,
                kind="performance",
                note="missing: " + ", ".join(missing),
            )
        )

    group.points.extend(points)
    group.metrics.extend(metrics)

    # Sanity checks that are useful when the shell pipeline in
    # unixbench_testcode.sh fails (e.g. awk/grep missing).
    if not points:
        group.warnings.append("UnixBench printed no 'Unixbench ... test(...)' lines")
    for name, point in zip(seen, points):
        expected_unit = UNIXBENCH_UNITS.get(name)
        if expected_unit and point.extra.get("unit") != expected_unit:
            group.warnings.append(
                f"unixbench {name}: unit {point.extra.get('unit')!r} != {expected_unit!r}"
            )
