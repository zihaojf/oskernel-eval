"""Score one parsed test group.

Dispatch order:

1. the provided ``judge_*.py`` script, when the suite is official and the
   script is present;
2. the local parser for suites that have no official judge;
3. a warning-only result for unknown suites.
"""

from __future__ import annotations

from pathlib import Path

from ..model import GroupResult, TestPoint, STATUS_FAIL
from ..references import OFFICIAL_SUITES, SUITE_SPECS
from .cyclictest import score_cyclictest
from .iozone import score_iozone_fallback
from .libcbench import score_libcbench
from .lmbench import score_lmbench
from .ltp import score_ltp
from .network import score_iperf, score_netperf
from .official import JudgeError, official_available, score_official
from .unixbench import score_unixbench

LOCAL_PARSERS = {
    "iozone": score_iozone_fallback,
    "unixbench": score_unixbench,
    "iperf": score_iperf,
    "libcbench": score_libcbench,
    "lmbench": score_lmbench,
    "netperf": score_netperf,
    "cyclictest": score_cyclictest,
    "ltp": score_ltp,
}


def score_group(
    group: GroupResult,
    judge_dir: Path,
    baseline: dict[str, float],
    *,
    score_mode: str = "both",
) -> GroupResult:
    """Fill ``group.points`` / ``group.metrics`` in place."""

    suite = group.suite
    if suite in OFFICIAL_SUITES and official_available(judge_dir, suite):
        try:
            official = score_official(
                suite, group.body, judge_dir, mode=score_mode
            )
            group.points = official.points
            group.metrics = official.metrics
            group.source = official.source
            if official.alternate_points is not None:
                group.alternate_score = official.alternate_score
                group.alternate_max = official.alternate_max
                group.alternate_label = (
                    "official judge, newline-normalized"
                    if official.source == "official-raw"
                    else "official judge, verbatim"
                )
            for warning in official.warnings:
                if warning not in group.warnings:
                    group.warnings.append(warning)
            return group
        except JudgeError as exc:
            group.warnings.append(f"official judge failed: {exc}")

    parser = LOCAL_PARSERS.get(suite)
    if parser is not None:
        parser(group, baseline)
        group.source = "local"
        return group

    spec = SUITE_SPECS.get(suite)
    hint = f" ({spec.title})" if spec else ""
    group.warnings.append(f"no scorer for suite {suite!r}{hint}")
    if spec is not None and spec.default_max:
        group.points.append(
            TestPoint(
                name=f"{suite}/no-scorer",
                score=0.0,
                max_score=float(spec.default_max),
                status=STATUS_FAIL,
                kind=spec.kind,
                note="suite was run but no judge could score it",
            )
        )
    return group
