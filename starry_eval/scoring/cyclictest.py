"""cyclictest + hackbench parser.

The test script reports success from the cyclictest exit code, which can be 0
even when no latency sample was produced (for example when hackbench cannot
fork).  The parser keeps the official success point but marks such runs as
``partial`` and raises a warning, so a "4/4" result is not mistaken for good
real-time behaviour.
"""

from __future__ import annotations

import re

from ..model import GroupResult, TestPoint, STATUS_FAIL, STATUS_FULL, STATUS_PARTIAL
from ..references import (
    CYCLIC_MAX_BAD_US,
    CYCLIC_MAX_WARN_US,
    CYCLIC_SCORE_MAX_US,
    CYCLICTEST_CASES,
)
from .common import add_metric, find_status_sections

START_RE = re.compile(r"^======\s*cyclictest\s+(.+?)\s+begin\s*======$")
END_RE = re.compile(
    r"^======\s*cyclictest\s+(.+?)\s+end:\s*(success|fail)\s*======$"
)
KILL_RE = re.compile(r"^======\s*kill hackbench:\s*(.+?)\s*======$")

THREAD_RE = re.compile(
    r"T:\s*(-?\d+)\s+\(\s*(\d+)\)\s+P:(\d+)\s+I:(\d+)\s+C:\s*(\d+)\s+"
    r"Min:\s*(-?\d+)\s+Act:\s*(-?\d+)\s+Avg:\s*(-?\d+)\s+Max:\s*(-?\d+)"
)


def score_cyclictest(group: GroupResult, baseline: dict[str, float]) -> None:
    sections = find_status_sections(group.body, START_RE, END_RE)
    points: list[TestPoint] = []
    seen: list[str] = []
    kill_ok = False

    for name, text, complete, status in sections:
        seen.append(name)
        ok = complete and status == "success"
        threads: list[tuple[int, int, int, int]] = []
        for line in text.splitlines():
            match = THREAD_RE.search(line)
            if match:
                threads.append(
                    (
                        int(match.group(6)),  # min
                        int(match.group(8)),  # avg
                        int(match.group(9)),  # max
                        int(match.group(5)),  # count
                    )
                )
        has_data = bool(threads)

        max_latency = max((item[2] for item in threads), default=None)
        # The online cyclictest judge is latency-based, not just an exit-code
        # check.  A run whose maximum latency is far outside the real-time
        # range gets 0 run-points even if cyclictest exits 0.
        latency_ok = (
            max_latency is not None and max_latency <= CYCLIC_SCORE_MAX_US
        )
        if not ok:
            state = STATUS_FAIL
            score = 0.0
        elif not has_data:
            state = STATUS_PARTIAL
            score = 0.0
        elif latency_ok:
            state = STATUS_FULL
            score = 1.0
        else:
            state = STATUS_FAIL
            score = 0.0

        note = f"end: {status or 'missing'}"
        if ok and not has_data:
            note += ", no latency samples"
        elif max_latency is not None:
            note += (
                f", max latency {max_latency} us "
                f"(score threshold {CYCLIC_SCORE_MAX_US:g} us)"
            )
        points.append(
            TestPoint(
                name=name,
                score=score,
                max_score=1.0,
                status=state,
                kind="mixed",
                note=note,
                extra={"threads": len(threads), "max_latency_us": max_latency},
            )
        )

        if not complete:
            group.warnings.append(f"cyclictest {name}: section has no end marker")
        if not ok:
            group.warnings.append(f"cyclictest {name}: reported {status or 'incomplete'}")
        if ok and not has_data:
            group.warnings.append(
                f"cyclictest {name}: success but no T: latency line "
                "(stress load did not really run)"
            )

        if threads and max_latency is not None:
            avg_latency = sum(item[1] for item in threads) / len(threads)
            add_metric(
                group.metrics,
                name=f"cyclictest/{name} max_latency",
                value=float(max_latency),
                unit="us",
                direction="lower",
                range_low=0.0,
                range_high=CYCLIC_MAX_BAD_US,
                baseline=baseline.get(f"cyclictest/{name} max_latency"),
                note="max over test threads",
            )
            add_metric(
                group.metrics,
                name=f"cyclictest/{name} avg_latency",
                value=avg_latency,
                unit="us",
                direction="lower",
                range_low=0.0,
                range_high=CYCLIC_MAX_BAD_US,
                baseline=baseline.get(f"cyclictest/{name} avg_latency"),
            )
            if max_latency >= CYCLIC_MAX_BAD_US:
                group.warnings.append(
                    f"cyclictest {name}: max latency {max_latency} us is very high "
                    f"(run score 0, threshold {CYCLIC_SCORE_MAX_US:g} us)"
                )
            elif max_latency >= CYCLIC_MAX_WARN_US:
                group.warnings.append(
                    f"cyclictest {name}: max latency {max_latency} us is high "
                    f"(run score 0, threshold {CYCLIC_SCORE_MAX_US:g} us)"
                )

    for line in group.body.splitlines():
        match = KILL_RE.match(line.strip())
        if match:
            kill_ok = match.group(1).strip().lower().startswith("success")

    if not kill_ok:
        for point in points:
            if point.name.startswith("STRESS"):
                point.score = 0.0
                point.status = STATUS_FAIL
                point.note += "; discarded because hackbench was not killed cleanly"
        group.warnings.append(
            "cyclictest: STRESS results discarded (kill hackbench was not successful)"
        )

    if "sleep: not found" in group.body:
        group.warnings.append(
            "cyclictest: the test script's 'sleep 1' failed; stress timing is off"
        )
    if "fork() (error" in group.body:
        group.warnings.append(
            "cyclictest: hackbench fork() failed; the STRESS runs are not loaded"
        )
    if "High resolution timers not available" in group.body:
        group.warnings.append("cyclictest: high-resolution timers not available")
    if "No measurements available" in group.body:
        group.warnings.append("cyclictest: hackbench reported no measurements")

    missing = [name for name in CYCLICTEST_CASES if name not in seen]
    if missing:
        points.append(
            TestPoint(
                name="cyclictest/missing-cases",
                score=0.0,
                max_score=float(len(missing)),
                status=STATUS_FAIL,
                kind="mixed",
                note="missing: " + ", ".join(missing),
            )
        )

    group.points.extend(points)
