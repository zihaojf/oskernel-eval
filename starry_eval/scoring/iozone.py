"""Fallback iozone parser.

``judge_iozone.py`` is used whenever it is available.  This fallback only
extracts the throughput table for the report and awards one coverage point per
metric; it cannot reproduce the official baseline-relative score.
"""

from __future__ import annotations

import re

from ..model import GroupResult, Metric, TestPoint, STATUS_FAIL, STATUS_FULL
from .common import to_float

SECTION_RE = re.compile(r"^iozone (?:throughput|throughtput) (.+?) measurements\s*$")
CHILD_RE = re.compile(r"^\s*Children see throughput for\s+(.+?)\s*=\s*([0-9.]+)\s*kB/sec")
MAX_RE = re.compile(r"^\s*Max throughput per process\s*=\s*([0-9.]+)\s*kB/sec")


def parse_iozone_metrics(body: str) -> list[tuple[str, float]]:
    section = ""
    child = ""
    metrics: list[tuple[str, float]] = []
    for line in body.splitlines():
        match = SECTION_RE.match(line.strip())
        if match:
            section = match.group(1).strip()
            continue
        match = CHILD_RE.match(line)
        if match:
            child = match.group(1).strip()
            continue
        match = MAX_RE.match(line)
        if match and section and child:
            metrics.append((f"iozone {section} {child} (kb/sec)", to_float(match.group(1))))
    return metrics


def score_iozone_fallback(group: GroupResult, baseline: dict[str, float]) -> None:
    metrics = parse_iozone_metrics(group.body)
    points: list[TestPoint] = []
    for name, value in metrics:
        points.append(
            TestPoint(
                name=name,
                score=1.0,
                max_score=1.0,
                status=STATUS_FULL,
                kind="performance",
                note=f"{value:g} kB/sec（只算覆盖分）",
                extra={"value": value},
            )
        )
        group.metrics.append(
            Metric(
                name=name,
                value=value,
                unit="kB/sec",
                direction="higher",
                reference=baseline.get(name),
                reference_kind="baseline" if name in baseline else "",
            )
        )
    if not points:
        points.append(
            TestPoint(
                name="iozone/missing-metrics",
                score=0.0,
                max_score=1.0,
                status=STATUS_FAIL,
                kind="performance",
                note="没有解析到 iozone 吞吐行",
            )
        )
        group.warnings.append("iozone 兜底解析：没有解析到吞吐行")
    group.points.extend(points)
