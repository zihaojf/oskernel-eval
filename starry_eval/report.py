"""Human-readable and machine-readable reports.

The same ``build_report`` structure feeds the JSON, Markdown and console
renderers.  Nothing here re-runs a test; it only formats an ``ArchResult``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .history import (
    REPORT_JSON,
    REPORT_MD,
    composite_totals,
    group_counted,
    official_totals,
    summary_entry,
)
from .model import (
    ArchResult,
    GroupResult,
    Metric,
    STATUS_FAIL,
    STATUS_FULL,
    STATUS_PARTIAL,
    STATUS_SKIP,
    SuiteResult,
    TestPoint,
)
from .references import (
    COMPOSITE_SUITES,
    OFFICIAL_SUITES,
    SUITE_ORDER,
    SUITE_SPECS,
)

VARIANT_ORDER = {"glibc": 0, "musl": 1, "default": 2}


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def _num(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}"


def _pct(score: float, max_score: float) -> str:
    if max_score <= 0:
        return "-"
    return f"{score / max_score * 100:.1f}%"


def _duration(value: float | None) -> str:
    if value is None or value <= 0:
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes}m{seconds:.0f}s"


def _suite_key(suite: str, variant: str) -> str:
    return f"{suite}-{variant}" if variant != "default" else suite


def _sort_key(suite: SuiteResult) -> tuple[int, int, str]:
    try:
        order = SUITE_ORDER.index(suite.suite)
    except ValueError:
        order = len(SUITE_ORDER)
    return (order, VARIANT_ORDER.get(suite.variant, 9), suite.variant)


def _point_status_label(point: TestPoint) -> str:
    return {
        STATUS_FULL: "pass",
        STATUS_PARTIAL: "partial",
        STATUS_FAIL: "fail",
        STATUS_SKIP: "skip",
    }.get(point.status, point.status)


# ---------------------------------------------------------------------------
# Suite rows
# ---------------------------------------------------------------------------


def suite_counted(suite: SuiteResult) -> tuple[float, float]:
    score = 0.0
    max_score = 0.0
    for group in suite.groups:
        group_score, group_max = group_counted(group)
        score += group_score
        max_score += group_max
    return score, max_score


def _suite_status(suite: SuiteResult) -> str:
    if not suite.groups:
        return "not-run"
    if any(g.source == "not-run" for g in suite.groups):
        return "not-run"
    if any(g.source == "script-no-group" for g in suite.groups):
        return "script-no-group"
    if not suite.complete:
        return "incomplete"
    return "ok"


def build_suite_rows(result: ArchResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        score, max_score = suite_counted(suite)
        alternatives = [
            (g.alternate_label, g.alternate_score, g.alternate_max)
            for g in suite.groups
            if g.alternate_score is not None
        ]
        row = {
            "suite": suite.suite,
            "variant": suite.variant,
            "name": _suite_key(suite.suite, suite.variant),
            "ran": suite.ran,
            "complete": suite.complete,
            "status": _suite_status(suite),
            "score": round(score, 6),
            "max_score": round(max_score, 6),
            "raw_score": round(suite.score, 6),
            "raw_max": round(suite.max_score, 6),
            "points_total": suite.points_total,
            "points_full": suite.points_full,
            "points_partial": suite.points_partial,
            "points_failed": suite.points_failed,
            "points_skipped": suite.points_skipped,
            "duration_sec": (
                round(suite.duration_sec, 3)
                if suite.duration_sec is not None
                else None
            ),
            "groups": [g.group for g in suite.groups],
            "warnings": suite.warnings,
        }
        if alternatives:
            label, alt_score, alt_max = alternatives[0]
            if alt_score is not None:
                row["alternate"] = {
                    "label": label,
                    "score": round(alt_score, 6),
                    "max_score": round(alt_max or 0.0, 6),
                }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Missing / warnings / lost points
# ---------------------------------------------------------------------------


def build_missing(result: ArchResult) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        status = _suite_status(suite)
        if status == "ok":
            continue
        score, max_score = suite_counted(suite)
        if status == "not-run":
            reason = "not run by the OS (no START/END marker)"
        elif status == "script-no-group":
            reason = "test script ran but printed no START/END marker"
        else:
            reason = "started but did not finish (killed or aborted)"
        missing.append(
            {
                "name": _suite_key(suite.suite, suite.variant),
                "suite": suite.suite,
                "variant": suite.variant,
                "status": status,
                "score": round(score, 6),
                "max_score": round(max_score, 6),
                "reason": reason,
            }
        )
    return missing


def build_warnings(result: ArchResult) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            warnings.append(text)

    for warning in result.warnings:
        add(warning)
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source == "not-run":
                continue
            for warning in group.warnings:
                add(f"{group.group}: {warning}")
    return warnings


def build_lost_by_suite(result: ArchResult) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source in ("not-run", "script-no-group"):
                continue
            key = _suite_key(suite.suite, suite.variant)
            bucket = buckets.setdefault(
                key,
                {
                    "name": key,
                    "suite": suite.suite,
                    "variant": suite.variant,
                    "lost": 0.0,
                    "points": 0,
                },
            )
            for point in group.points:
                if point.status == STATUS_SKIP or point.lost <= 1e-9:
                    continue
                bucket["lost"] += point.lost
                bucket["points"] += 1
    rows = [row for row in buckets.values() if row["lost"] > 1e-9]
    rows.sort(key=lambda row: row["lost"], reverse=True)
    for row in rows:
        row["lost"] = round(row["lost"], 6)
    return rows


def build_lost_points(
    result: ArchResult, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            if group.source in ("not-run", "script-no-group"):
                continue
            for point in group.points:
                if point.status == STATUS_SKIP or point.lost <= 1e-9:
                    continue
                rows.append(
                    {
                        "group": group.group,
                        "suite": suite.suite,
                        "variant": suite.variant,
                        "name": point.name,
                        "status": _point_status_label(point),
                        "score": round(point.score, 6),
                        "max_score": round(point.max_score, 6),
                        "lost": round(point.lost, 6),
                        "note": point.note,
                    }
                )
    rows.sort(key=lambda row: row["lost"], reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------


def build_metrics(result: ArchResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for suite in sorted(result.suites, key=_sort_key):
        for group in suite.groups:
            for metric in group.metrics:
                rows.append(
                    {
                        "group": group.group,
                        "suite": suite.suite,
                        "variant": suite.variant,
                        "name": metric.name,
                        "value": metric.value,
                        "unit": metric.unit,
                        "direction": metric.direction,
                        "reference": metric.reference,
                        "reference_kind": metric.reference_kind,
                        "range_low": metric.range_low,
                        "range_high": metric.range_high,
                        "quality": metric.quality,
                        "delta_pct": metric.delta_pct,
                        "note": metric.note,
                    }
                )
    return rows


def _metric_quality_summary(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = [m["quality"] for m in metrics if m.get("quality") is not None]
    if not values:
        return {"count": 0, "mean_quality": None}
    return {
        "count": len(values),
        "mean_quality": sum(values) / len(values),
        "min_quality": min(values),
        "max_quality": max(values),
    }


# ---------------------------------------------------------------------------
# Summary structure
# ---------------------------------------------------------------------------


def build_summary(
    result: ArchResult, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    official_score, official_max = official_totals(result)
    alternate_score, alternate_max = official_totals(result, alternate=True)
    if result.score_mode == "raw":
        verbatim = (official_score, official_max)
        intended = (alternate_score, alternate_max)
    else:
        intended = (official_score, official_max)
        verbatim = (alternate_score, alternate_max)
    composite_score, composite_max = composite_totals(result)
    suite_rows = build_suite_rows(result)

    previous_suites: dict[str, dict[str, Any]] = {}
    if previous:
        for row in previous.get("suites", []):
            previous_suites[f"{row.get('suite')}-{row.get('variant')}"] = row

    for row in suite_rows:
        prev = previous_suites.get(f"{row['suite']}-{row['variant']}")
        if prev is not None:
            row["previous_score"] = prev.get("score")
            row["delta"] = round(row["score"] - float(prev.get("score", 0.0)), 6)
        else:
            row["previous_score"] = None
            row["delta"] = None

    metrics = build_metrics(result)
    by_variant: dict[str, dict[str, float]] = {}
    for suite in result.suites:
        score, max_score = suite_counted(suite)
        bucket = by_variant.setdefault(
            suite.variant, {"score": 0.0, "max_score": 0.0}
        )
        bucket["score"] += score
        bucket["max_score"] += max_score

    counts = {
        "groups": len(result.groups),
        "groups_complete": len([g for g in result.groups if g.complete]),
        "groups_incomplete": len([g for g in result.groups if not g.complete]),
        "test_points": sum(s.points_total for s in result.suites),
        "points_full": sum(s.points_full for s in result.suites),
        "points_partial": sum(s.points_partial for s in result.suites),
        "points_failed": sum(s.points_failed for s in result.suites),
        "points_skipped": sum(s.points_skipped for s in result.suites),
    }

    return {
        "official": {
            "score": round(official_score, 6),
            "max_score": round(official_max, 6),
            "verbatim": {
                "score": round(verbatim[0], 6),
                "max_score": round(verbatim[1], 6),
            },
            "intended": {
                "score": round(intended[0], 6),
                "max_score": round(intended[1], 6),
            },
            "suites": list(OFFICIAL_SUITES),
        },
        "composite": {
            "score": round(composite_score, 6),
            "max_score": round(composite_max, 6),
            "suites": list(COMPOSITE_SUITES),
            "note": "local composite; ltp is excluded because its denominator is dynamic",
        },
        "by_variant": {
            key: {
                "score": round(value["score"], 6),
                "max_score": round(value["max_score"], 6),
            }
            for key, value in sorted(by_variant.items())
        },
        "counts": counts,
        "suite_rows": suite_rows,
        "missing": build_missing(result),
        "warnings": build_warnings(result),
        "lost_points": build_lost_points(result),
        "lost_by_suite": build_lost_by_suite(result),
        "metrics": metrics,
        "metric_summary": _metric_quality_summary(metrics),
        "scripts": result.scripts,
    }


# ---------------------------------------------------------------------------
# Public structure
# ---------------------------------------------------------------------------


def build_report(
    result: ArchResult,
    run_dir: Path,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = result.to_dict()
    data["run_dir"] = str(run_dir)
    data["summary"] = build_summary(result, previous)
    data["previous"] = (
        {
            "run_dir": previous.get("run_dir"),
            "started_at": previous.get("started_at"),
            "official_score": previous.get("official_score"),
            "official_max": previous.get("official_max"),
        }
        if previous
        else None
    )
    data["index_entry"] = summary_entry(result, run_dir)
    return data


# ---------------------------------------------------------------------------
# Console renderer
# ---------------------------------------------------------------------------


def render_text(report: dict[str, Any]) -> str:
    meta = report
    summary = report["summary"]
    lines: list[str] = []

    arch = meta.get("arch", "?")
    label = meta.get("label", "")
    lines.append("=" * 78)
    lines.append(f"StarryOS local evaluation  -  {arch} ({label})")
    lines.append("=" * 78)
    lines.append(f"kernel   : {meta.get('kernel') or '-'}")
    lines.append(f"image    : {meta.get('image') or '-'}")
    lines.append(
        f"run      : {meta.get('started_at') or '-'}   "
        f"wall {_duration(meta.get('wall_time_sec'))}   "
        f"exit {meta.get('exit_code')}   "
        f"timed out: {'yes' if meta.get('timed_out') else 'no'}"
    )
    lines.append(f"mode     : {meta.get('score_mode')}")
    lines.append("")

    official = summary["official"]
    composite = summary["composite"]
    counts = summary["counts"]
    lines.append("Totals")
    lines.append("-" * 78)
    verbatim = official.get("verbatim", official)
    intended = official.get("intended", official)
    if (
        abs(verbatim["score"] - intended["score"]) < 1e-9
        and abs(verbatim["max_score"] - intended["max_score"]) < 1e-9
    ):
        lines.append(
            f"official judge score : {_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}"
            f"  ({_pct(verbatim['score'], verbatim['max_score'])})"
        )
    else:
        lines.append(
            f"official judge (verbatim): {_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}"
            f"  ({_pct(verbatim['score'], verbatim['max_score'])})"
        )
        lines.append(
            f"official judge (intended): {_num(intended['score'])} / "
            f"{_num(intended['max_score'])}"
            f"  ({_pct(intended['score'], intended['max_score'])})"
        )
    lines.append(
        f"local composite      : {_num(composite['score'])} / {_num(composite['max_score'])}"
        f"  ({_pct(composite['score'], composite['max_score'])})"
    )
    lines.append(
        f"test points          : {counts['points_full']} full, "
        f"{counts['points_partial']} partial, {counts['points_failed']} fail, "
        f"{counts['points_skipped']} skip"
    )
    lines.append(
        f"groups               : {counts['groups_complete']}/{counts['groups']} complete"
    )
    lines.append("")

    lines.append("Suite scores")
    lines.append("-" * 78)
    header = (
        f"{'suite':<20} {'var':<7} {'score':>10} {'points f/p/f':>14} "
        f"{'time':>8}  {'status':<12} delta"
    )
    lines.append(header)
    for row in summary["suite_rows"]:
        delta = row.get("delta")
        delta_text = f"{delta:+.1f}" if delta is not None else ""
        lines.append(
            f"{row['suite']:<20} {row['variant']:<7} "
            f"{_num(row['score']):>4}/{_num(row['max_score']):<5} "
            f"{row['points_full']:>4}/{row['points_partial']:>3}/{row['points_failed']:<3} "
            f"{_duration(row['duration_sec']):>8}  {row['status']:<12} {delta_text}"
        )
    for row in summary["suite_rows"]:
        alternate = row.get("alternate")
        if alternate:
            lines.append(
                f"  note: {row['name']}: {alternate['label']} "
                f"{_num(alternate['score'])}/{_num(alternate['max_score'])}"
            )
        if row["status"] in ("incomplete", "script-no-group") and row["raw_score"] > 0:
            lines.append(
                f"  note: {row['name']}: partial raw score "
                f"{_num(row['raw_score'])}/{_num(row['raw_max'])} (not counted)"
            )
    lines.append("")

    missing = summary["missing"]
    if missing:
        lines.append("Not run / incomplete")
        lines.append("-" * 78)
        for row in missing:
            lines.append(
                f"! {row['name']:<24} {_num(row['score']):>6}/{_num(row['max_score']):<6} "
                f"{row['reason']}"
            )
        lines.append("")

    lost_by_suite = summary.get("lost_by_suite", [])
    if lost_by_suite:
        lines.append("Lost points by suite (largest opportunity first)")
        lines.append("-" * 78)
        for row in lost_by_suite[:12]:
            lines.append(
                f"  {row['name']:<24} lost {row['lost']:>7.1f} "
                f"across {row['points']} test point(s)"
            )
        lines.append("")

    lost = summary["lost_points"]
    if lost:
        lines.append("Top lost points")
        lines.append("-" * 78)
        for index, row in enumerate(lost[:12], 1):
            lines.append(
                f"{index:>2}. {row['group']:<22} {row['name']:<36} "
                f"{_num(row['score']):>4}/{_num(row['max_score']):<4} "
                f"lost {_num(row['lost']):>4}  {row['note']}"
            )
        lines.append("")

    warnings = summary["warnings"]
    if warnings:
        lines.append("Warnings")
        lines.append("-" * 78)
        for warning in warnings[:30]:
            lines.append(f"! {warning}")
        if len(warnings) > 30:
            lines.append(f"! ... and {len(warnings) - 30} more (see report.json)")
        lines.append("")

    lines.append("Performance")
    lines.append("-" * 78)
    metrics_by_group: dict[str, list[dict[str, Any]]] = {}
    for metric in summary["metrics"]:
        metrics_by_group.setdefault(metric["group"], []).append(metric)
    for group_name in sorted(metrics_by_group, key=_group_order):
        metrics = metrics_by_group[group_name]
        if not metrics:
            continue
        suite = metrics[0]["suite"]
        variant = metrics[0]["variant"]
        lines.append(f"[{_suite_key(suite, variant)}] {len(metrics)} metrics")
        # Worst first if a quality is available, otherwise in output order.
        def rank_key(metric: dict[str, Any]) -> tuple[int, float, float]:
            quality = metric.get("quality")
            value = float(metric.get("value", 0.0))
            # For "lower is better" a large value is worse, hence the sign.
            value_rank = -value if metric.get("direction") == "lower" else value
            if quality is not None:
                return (0, float(quality), value_rank)
            return (1, 0.0, value_rank)

        ranked = sorted(metrics, key=rank_key)
        for metric in ranked[:12]:
            value = f"{_num(metric['value'], 3)} {metric['unit']}".strip()
            extra = ""
            if metric.get("quality") is not None:
                extra += f" quality={metric['quality']:.2f}"
            if metric.get("delta_pct") is not None:
                extra += f" delta={metric['delta_pct']:+.1f}%"
            if metric.get("range_low") is not None:
                extra += (
                    f" expected {_num(metric['range_low'], 4)}"
                    f"-{_num(metric['range_high'], 4)} {metric['unit']}"
                )
            lines.append(f"  {metric['name']:<48} {value:<20}{extra}")
        if len(ranked) > 12:
            lines.append(f"  ... {len(ranked) - 12} more")
        lines.append("")

    lines.append(
        "Legend: official = provided judge_*.py suites (verbatim and "
        "newline-normalized); composite = local index over all suites except "
        "ltp (not the online total; use compare-online to align); "
        "points f/p/f = full/partial/fail."
    )
    return "\n".join(lines) + "\n"


def _group_order(group_name: str) -> tuple[int, str]:
    suite = group_name.split("-")[0]
    try:
        return (SUITE_ORDER.index(suite), group_name)
    except ValueError:
        return (len(SUITE_ORDER), group_name)


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    arch = report.get("arch", "?")
    label = report.get("label", "")

    lines.append(f"# StarryOS local evaluation - {arch} ({label})")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    for key, value in (
        ("kernel", report.get("kernel")),
        ("image", report.get("image")),
        ("started_at", report.get("started_at")),
        ("wall_time_sec", report.get("wall_time_sec")),
        ("exit_code", report.get("exit_code")),
        ("timed_out", report.get("timed_out")),
        ("score_mode", report.get("score_mode")),
    ):
        lines.append(f"| {key} | {value} |")
    lines.append("")

    official = summary["official"]
    composite = summary["composite"]
    counts = summary["counts"]
    lines.append("## 总览")
    lines.append("")
    verbatim = official.get("verbatim", official)
    intended = official.get("intended", official)
    if (
        abs(verbatim["score"] - intended["score"]) < 1e-9
        and abs(verbatim["max_score"] - intended["max_score"]) < 1e-9
    ):
        lines.append(
            f"- official judge score: **{_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}** "
            f"({_pct(verbatim['score'], verbatim['max_score'])})"
        )
    else:
        lines.append(
            f"- official judge (verbatim): **{_num(verbatim['score'])} / "
            f"{_num(verbatim['max_score'])}** "
            f"({_pct(verbatim['score'], verbatim['max_score'])})"
        )
        lines.append(
            f"- official judge (intended): **{_num(intended['score'])} / "
            f"{_num(intended['max_score'])}** "
            f"({_pct(intended['score'], intended['max_score'])})"
        )
    lines.append(
        f"- local composite: **{_num(composite['score'])} / "
        f"{_num(composite['max_score'])}** ({_pct(composite['score'], composite['max_score'])})"
    )
    lines.append(
        f"- test points: {counts['points_full']} full, "
        f"{counts['points_partial']} partial, {counts['points_failed']} fail, "
        f"{counts['points_skipped']} skip"
    )
    lines.append(
        f"- groups: {counts['groups_complete']}/{counts['groups']} complete"
    )
    lines.append("")

    lines.append("## 套件得分")
    lines.append("")
    lines.append(
        "| suite | variant | score / max | full/partial/fail | time | status | delta | note |"
    )
    lines.append("|---|---|---:|---:|---:|---|---:|---|")
    for row in summary["suite_rows"]:
        note = ""
        if row.get("alternate"):
            alt = row["alternate"]
            note = (
                f"{alt['label']}: {_num(alt['score'])}/{_num(alt['max_score'])}"
            )
        delta = row.get("delta")
        lines.append(
            f"| {row['suite']} | {row['variant']} | "
            f"{_num(row['score'])} / {_num(row['max_score'])} | "
            f"{row['points_full']}/{row['points_partial']}/{row['points_failed']} | "
            f"{_duration(row['duration_sec'])} | {row['status']} | "
            f"{f'{delta:+.1f}' if delta is not None else ''} | {note} |"
        )
    lines.append("")

    if summary["missing"]:
        lines.append("## 未运行 / 未完成")
        lines.append("")
        lines.append("| test point | score | max | reason |")
        lines.append("|---|---:|---:|---|")
        for row in summary["missing"]:
            lines.append(
                f"| {row['name']} | {_num(row['score'])} | "
                f"{_num(row['max_score'])} | {row['reason']} |"
            )
        lines.append("")

    if summary.get("lost_by_suite"):
        lines.append("## 按套件统计失分")
        lines.append("")
        lines.append("| test point | lost | test points |")
        lines.append("|---|---:|---:|")
        for row in summary["lost_by_suite"]:
            lines.append(
                f"| {row['name']} | {_num(row['lost'])} | {row['points']} |"
            )
        lines.append("")

    if summary["lost_points"]:
        lines.append("## 失分优先级")
        lines.append("")
        lines.append("| # | group | test point | score | max | lost | note |")
        lines.append("|---:|---|---|---:|---:|---:|---|")
        for index, row in enumerate(summary["lost_points"], 1):
            lines.append(
                f"| {index} | {row['group']} | {row['name']} | "
                f"{_num(row['score'])} | {_num(row['max_score'])} | "
                f"{_num(row['lost'])} | {row['note']} |"
            )
        lines.append("")

    if summary["warnings"]:
        lines.append("## 警告")
        lines.append("")
        for warning in summary["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## 性能指标")
    lines.append("")
    metrics_by_group: dict[str, list[dict[str, Any]]] = {}
    for metric in summary["metrics"]:
        metrics_by_group.setdefault(metric["group"], []).append(metric)
    for group_name in sorted(metrics_by_group, key=_group_order):
        metrics = metrics_by_group[group_name]
        if not metrics:
            continue
        lines.append(f"### {group_name}")
        lines.append("")
        lines.append("| metric | value | unit | ref | quality | delta |")
        lines.append("|---|---:|---|---:|---:|---:|")
        for metric in metrics:
            ref = "-"
            if metric.get("range_low") is not None:
                ref = (
                    f"{_num(metric['range_low'], 4)}-"
                    f"{_num(metric['range_high'], 4)}"
                )
            elif metric.get("reference") is not None:
                ref = _num(metric["reference"], 4)
            quality = (
                f"{metric['quality']:.3f}"
                if metric.get("quality") is not None
                else ""
            )
            delta = (
                f"{metric['delta_pct']:+.1f}%"
                if metric.get("delta_pct") is not None
                else ""
            )
            lines.append(
                f"| {metric['name']} | {_num(metric['value'], 4)} | "
                f"{metric['unit']} | {ref} | {quality} | {delta} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_reports(report: dict[str, Any], run_dir: Path) -> tuple[Path, Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / REPORT_JSON
    md_path = run_dir / REPORT_MD
    txt_path = run_dir / "report.txt"

    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    text = render_text(report)
    txt_path.write_text(text, encoding="utf-8")
    return json_path, md_path, txt_path


def print_report(report: dict[str, Any]) -> None:
    print(render_text(report), end="")
