"""Compare two saved reports (useful after an optimisation round)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .references import SUITE_ORDER


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _suite_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in report.get("summary", {}).get("suite_rows", []):
        key = f"{row['suite']}-{row['variant']}"
        result[key] = row
    return result


def _metric_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in report.get("summary", {}).get("metrics", []):
        key = f"{row['variant']}/{row['name']}"
        result[key] = row
    return result


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def compare_reports(
    old: dict[str, Any], new: dict[str, Any], *, limit_metrics: int = 30
) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("Report comparison")
    lines.append("=" * 78)
    lines.append(f"old: {old.get('run_dir') or old.get('started_at')}")
    lines.append(f"new: {new.get('run_dir') or new.get('started_at')}")
    lines.append("")

    old_summary = old.get("summary", {})
    new_summary = new.get("summary", {})
    for label, key in (("official", "official"), ("composite", "composite")):
        before = old_summary.get(key, {})
        after = new_summary.get(key, {})
        delta = float(after.get("score", 0)) - float(before.get("score", 0))
        lines.append(
            f"{label:<12} {_fmt(before.get('score')):>10} -> "
            f"{_fmt(after.get('score')):>10}   delta {delta:+.2f}"
        )
    lines.append("")

    before_suites = _suite_map(old)
    after_suites = _suite_map(new)
    lines.append("Suite deltas")
    lines.append("-" * 78)
    lines.append(f"{'suite':<28} {'old':>10} {'new':>10} {'delta':>10}")
    for key in sorted(
        set(before_suites) | set(after_suites),
        key=lambda item: (
            SUITE_ORDER.index(item.split("-")[0])
            if item.split("-")[0] in SUITE_ORDER
            else 99,
            item,
        ),
    ):
        before = before_suites.get(key, {})
        after = after_suites.get(key, {})
        old_score = float(before.get("score", 0.0))
        new_score = float(after.get("score", 0.0))
        delta = new_score - old_score
        marker = " *" if abs(delta) > 1e-9 else ""
        lines.append(
            f"{key:<28} {_fmt(old_score):>10} {_fmt(new_score):>10} "
            f"{delta:>+10.2f}{marker}"
        )
    lines.append("")

    before_metrics = _metric_map(old)
    after_metrics = _metric_map(new)
    changes: list[tuple[float, str, float, float]] = []
    for key in set(before_metrics) & set(after_metrics):
        before = float(before_metrics[key].get("value", 0.0))
        after = float(after_metrics[key].get("value", 0.0))
        if before == 0:
            continue
        changes.append(((after - before) / before * 100.0, key, before, after))
    changes.sort(key=lambda item: abs(item[0]), reverse=True)
    if changes:
        lines.append(f"Largest metric changes (top {limit_metrics})")
        lines.append("-" * 78)
        for pct, key, before, after in changes[:limit_metrics]:
            lines.append(
                f"{key:<56} {before:>12.4f} -> {after:>12.4f}  {pct:+8.2f}%"
            )
    return "\n".join(lines) + "\n"


def compare_files(old_path: Path, new_path: Path) -> str:
    return compare_reports(_load(old_path), _load(new_path))
