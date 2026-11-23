"""Turn a serial log into scored architecture results.

This module is deliberately independent of QEMU: the ``score`` subcommand uses
it on a saved log, and the ``run`` subcommand uses it on a live capture.
"""

from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_LONG_SCRIPT_TIMEOUT, DEFAULT_SCRIPT_TIMEOUT
from .model import (
    ArchResult,
    GroupResult,
    SuiteResult,
    TestPoint,
    STATUS_FAIL,
    STATUS_SKIP,
)
from .references import SUITE_ORDER, SUITE_SPECS
from .scoring import score_group
from .serial_parser import ScriptRun, parse_serial, raw_group_to_result


# ---------------------------------------------------------------------------
# Synthetic groups for things the OS did not run
# ---------------------------------------------------------------------------


def _expected_max(suite: str) -> float:
    spec = SUITE_SPECS.get(suite)
    if spec is None or spec.default_max is None:
        return 0.0
    return float(spec.default_max)


def _not_run_group(suite: str, variant: str, reason: str) -> GroupResult:
    max_score = _expected_max(suite)
    status = STATUS_SKIP if max_score <= 0 else STATUS_FAIL
    point = TestPoint(
        name=f"{suite}/not-run",
        score=0.0,
        max_score=max_score,
        status=status,
        kind=SUITE_SPECS.get(suite).kind if suite in SUITE_SPECS else "functional",
        note=reason,
    )
    return GroupResult(
        group=f"{suite}-{variant}",
        suite=suite,
        variant=variant,
        complete=False,
        body="",
        points=[point],
        source="not-run",
        warnings=[reason],
    )


def _script_without_group(
    suite: str, variant: str, status: int | None
) -> GroupResult:
    max_score = _expected_max(suite)
    point = TestPoint(
        name=f"{suite}/no-group-marker",
        score=0.0,
        max_score=max_score,
        status=STATUS_FAIL if max_score else STATUS_SKIP,
        kind=SUITE_SPECS.get(suite).kind if suite in SUITE_SPECS else "functional",
        note=(
            "test script ran"
            + (f" (status {status})" if status is not None else "")
            + " but printed no OS COMP TEST GROUP marker"
        ),
    )
    return GroupResult(
        group=f"{suite}-{variant}",
        suite=suite,
        variant=variant,
        complete=False,
        body="",
        points=[point],
        source="script-no-group",
        warnings=[point.note],
    )


# ---------------------------------------------------------------------------
# Duration / script bookkeeping
# ---------------------------------------------------------------------------


def _scoped_baseline(
    baseline: dict[str, float], arch: str, variant: str
) -> dict[str, float]:
    """Select the baseline entries for one arch/libc variant.

    Keys in ``baseline.json`` are written as ``<arch>/<variant>/<metric>``.
    An old or hand-written file without that prefix is accepted as a fallback
    so the scorer remains easy to bootstrap.
    """

    prefix = f"{arch}/{variant}/"
    scoped = {
        key[len(prefix) :]: value
        for key, value in baseline.items()
        if key.startswith(prefix)
    }
    if scoped:
        return scoped
    return baseline


def _script_map(
    script_runs: list[ScriptRun],
    script_times: dict[str, tuple[float, float]] | None,
) -> dict[tuple[str, str], list[ScriptRun]]:
    result: dict[tuple[str, str], list[ScriptRun]] = {}
    for run in script_runs:
        if not run.suite:
            continue
        if script_times and run.path in script_times:
            start, end = script_times[run.path]
            run.duration_sec = max(0.0, end - start)
        result.setdefault((run.suite, run.variant), []).append(run)
    return result


def _attach_durations(
    groups: list[GroupResult],
    scripts: dict[tuple[str, str], list[ScriptRun]],
) -> None:
    used: set[tuple[str, str]] = set()
    for group in groups:
        if group.duration_sec is not None:
            continue
        key = (group.suite, group.variant)
        if key in used:
            continue
        runs = scripts.get(key)
        if not runs:
            continue
        durations = [run.duration_sec for run in runs if run.duration_sec is not None]
        if durations:
            group.duration_sec = sum(durations)
            used.add(key)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _expected_variants(observed: list[str]) -> list[str]:
    """Variants that the report should show.

    The competition image ships both ``glibc`` and ``musl`` test trees.  If at
    least one of them was observed, report the other one as "not run" too, so a
    truncated or one-sided run cannot silently look complete.
    """

    observed = [v for v in observed if v]
    if not observed:
        return ["default"]
    if any(v in ("glibc", "musl") for v in observed):
        result = ["glibc", "musl"]
        for variant in observed:
            if variant not in result:
                result.append(variant)
        return result
    return sorted(set(observed))


def aggregate_suites(
    groups: list[GroupResult],
    *,
    variants: list[str],
    script_runs: dict[tuple[str, str], list[ScriptRun]] | None = None,
) -> list[SuiteResult]:
    """Group scored groups into suites and add explicit "not run" entries."""

    present: dict[tuple[str, str], list[GroupResult]] = {}
    for group in groups:
        present.setdefault((group.suite, group.variant), []).append(group)

    suites: list[SuiteResult] = []
    for suite in SUITE_ORDER:
        for variant in variants:
            key = (suite, variant)
            items = present.get(key)
            if not items:
                reason = f"{suite} ({variant}) was not run by the OS"
                if script_runs and key in script_runs:
                    run = script_runs[key][0]
                    items = [_script_without_group(suite, variant, run.status)]
                else:
                    items = [_not_run_group(suite, variant, reason)]
            suite_result = SuiteResult(suite=suite, variant=variant, groups=items)
            suite_result.ran = any(g.source != "not-run" for g in items)
            suites.append(suite_result)

    # Unknown suites (a new test point that is not in the catalogue) are kept
    # in the report rather than dropped.
    known = {(s.suite, s.variant) for s in suites}
    for group in groups:
        key = (group.suite, group.variant)
        if key in known:
            continue
        suite_result = SuiteResult(
            suite=group.suite, variant=group.variant, groups=[group], ran=True
        )
        suites.append(suite_result)
        known.add(key)

    return suites


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def evaluate_text(
    arch: str,
    label: str,
    text: str,
    *,
    judge_dir: Path,
    baseline: dict[str, float],
    score_mode: str = "both",
    kernel: str = "",
    image: str = "",
    command: list[str] | None = None,
    started_at: str = "",
    wall_time_sec: float = 0.0,
    timed_out: bool = False,
    exit_code: int | None = None,
    log_path: str = "",
    script_times: dict[str, tuple[float, float]] | None = None,
) -> ArchResult:
    parsed = parse_serial(text)
    warnings: list[str] = list(parsed.warnings)
    if not parsed.groups:
        warnings.append(
            "no '#### OS COMP TEST GROUP START ... ####' markers were found"
        )
    if timed_out:
        warnings.append(
            f"QEMU was killed after the timeout; groups without END markers are "
            "not counted"
        )

    # Build and score one GroupResult per captured group.
    groups: list[GroupResult] = []
    for raw in parsed.groups:
        group = raw_group_to_result(raw)
        scoped = _scoped_baseline(baseline, arch, group.variant)
        score_group(group, judge_dir, scoped, score_mode=score_mode)
        if not group.complete:
            group.warnings.append(
                "group is incomplete (no END marker); partial score is shown but "
                "the online harness would not count it"
            )
        groups.append(group)

    scripts = _script_map(parsed.script_runs, script_times)
    _attach_durations(groups, scripts)

    # A script that ran but produced no group marker is a distinct failure
    # mode from "the OS never ran the suite".
    for (suite, variant), runs in scripts.items():
        if suite not in SUITE_SPECS:
            continue
        if any(g.suite == suite and g.variant == variant for g in groups):
            continue
        status = runs[-1].status if runs else None
        groups.append(_script_without_group(suite, variant, status))
        warnings.append(
            f"{suite} ({variant}) script ran but produced no test group marker"
        )

    observed_variants = sorted({g.variant for g in groups})
    variants = _expected_variants(observed_variants)
    suites = aggregate_suites(groups, variants=variants, script_runs=scripts)

    # Script-level timing warnings (the guest init uses per-script timeouts:
    # 240 s for most suites, 600 s for unixbench).
    for group in groups:
        if group.duration_sec is None:
            continue
        limit = (
            DEFAULT_LONG_SCRIPT_TIMEOUT
            if group.suite == "unixbench"
            else DEFAULT_SCRIPT_TIMEOUT
        )
        if group.duration_sec >= limit * 0.8:
            group.warnings.append(
                f"script took {group.duration_sec:.1f}s, close to the guest "
                f"{limit:g}s per-test timeout"
            )

    # Keep the full script table for the report.
    script_rows: list[dict[str, object]] = []
    for run in parsed.script_runs:
        script_rows.append(
            {
                "path": run.path,
                "suite": run.suite,
                "variant": run.variant,
                "status": run.status,
                "duration_sec": (
                    round(run.duration_sec, 3)
                    if run.duration_sec is not None
                    else None
                ),
            }
        )

    return ArchResult(
        arch=arch,
        label=label,
        kernel=kernel,
        image=image,
        command=command or [],
        started_at=started_at,
        wall_time_sec=wall_time_sec,
        timed_out=timed_out,
        exit_code=exit_code,
        groups=groups,
        suites=suites,
        raw_log=log_path,
        score_mode=score_mode,
        warnings=warnings,
        scripts=script_rows,
    )
