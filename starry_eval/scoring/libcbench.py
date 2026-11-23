"""libc-bench: one coverage point per benchmark, time/memory as metrics."""

from __future__ import annotations

import re

from ..model import GroupResult, Metric, TestPoint, STATUS_FAIL, STATUS_FULL
from ..references import LIBCBENCH_EXPECTED
from .common import add_metric, to_float

# A benchmark header, e.g. ``b_malloc_sparse (0)`` or
# ``b_string_strstr ("abcdef")``.
BENCH_RE = re.compile(r"^([A-Za-z_][\w.]*)\s*(?:\((.*)\))?\s*$")
TIME_RE = re.compile(
    r"^\s*time:\s*([0-9.]+),\s*virt:\s*(\d+),\s*res:\s*(\d+),\s*dirty:\s*(\d+)\s*$"
)

EXPECTED_COUNT = 27


def score_libcbench(group: GroupResult, baseline: dict[str, float]) -> None:
    points: list[TestPoint] = []
    metrics: list[Metric] = []
    pending: str | None = None
    pending_args = ""
    seen_base: list[str] = []

    for line in group.body.splitlines():
        match = TIME_RE.match(line)
        if match and pending is not None:
            time_s = to_float(match.group(1))
            virt = int(match.group(2))
            res = int(match.group(3))
            dirty = int(match.group(4))
            display = pending
            if pending_args:
                display = f"{pending}({pending_args})"
            if display in {p.name for p in points}:
                # The same benchmark can be printed more than once; keep the
                # names unique so the report is unambiguous.
                suffix = 2
                while f"{display}#{suffix}" in {p.name for p in points}:
                    suffix += 1
                display = f"{display}#{suffix}"

            point = TestPoint(
                name=display,
                score=1.0,
                max_score=1.0,
                status=STATUS_FULL,
                kind="performance",
                note=f"time={time_s:.6f}s virt={virt} res={res} dirty={dirty}",
                extra={
                    "time_sec": time_s,
                    "virt_kb": virt,
                    "res_kb": res,
                    "dirty_kb": dirty,
                },
            )
            points.append(point)
            add_metric(
                metrics,
                name=f"libcbench/{display}",
                value=time_s,
                unit="s",
                direction="lower",
                baseline=baseline.get(f"libcbench/{display}"),
            )
            seen_base.append(pending)
            pending = None
            pending_args = ""
            continue

        match = BENCH_RE.match(line)
        if match:
            pending = match.group(1)
            pending_args = (match.group(2) or "").strip()
            continue

    # A benchmark that started but never printed its time line is a failed
    # point, not a missing row.
    if pending is not None:
        points.append(
            TestPoint(
                name=pending,
                score=0.0,
                max_score=1.0,
                status=STATUS_FAIL,
                kind="performance",
                note="benchmark header found but no time line",
            )
        )

    if len(points) < EXPECTED_COUNT:
        points.append(
            TestPoint(
                name="libcbench/missing-benchmarks",
                score=0.0,
                max_score=float(EXPECTED_COUNT - len(points)),
                status=STATUS_FAIL,
                kind="performance",
                note=f"expected {EXPECTED_COUNT} benchmarks, parsed {len(points)}",
            )
        )
    elif len(points) > EXPECTED_COUNT:
        # New libc-bench revision: keep the result but do not inflate the max.
        group.warnings.append(
            f"libcbench printed {len(points)} benchmarks, expected {EXPECTED_COUNT}"
        )

    group.points.extend(points)
    group.metrics.extend(metrics)

    missing = [name for name in LIBCBENCH_EXPECTED if name not in seen_base]
    if missing:
        group.warnings.append(
            "libcbench did not print: " + ", ".join(sorted(set(missing)))
        )
