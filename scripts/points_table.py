#!/usr/bin/env python3
"""Flatten *every* test point of a ``report.json`` into one summary table.

``report.md`` / ``report.txt`` only print the top lost points (20 / 12) and one
row per suite, so the per-test-point numbers stay buried inside ``report.json``.
This script pulls them all out and writes a single table (Markdown + CSV) that
can be pasted into a report or diffed between runs.

Usage::

    python3 scripts/points_table.py runs/<run>/report.json
    python3 scripts/points_table.py runs/<run>/report.json --only lost
    python3 scripts/points_table.py runs/*/report.json --out-dir /tmp/tables

Nothing here re-scores anything; it only reshapes an existing report.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

STATUS_LABEL = {
    "full": "pass",
    "partial": "partial",
    "fail": "fail",
    "skip": "skip",
}


def _num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _cell(text: Any) -> str:
    """Escape a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def collect(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per test point, in the report's own suite order."""

    rows: list[dict[str, Any]] = []
    for suite in report.get("suites", []):
        for group in suite.get("groups", []):
            source = group.get("source", "")
            points = group.get("test_points") or []
            if not points:
                rows.append(
                    {
                        "group": group.get("group", ""),
                        "suite": suite.get("suite", ""),
                        "variant": suite.get("variant", ""),
                        "name": "(suite did not run)",
                        "status": "not-run",
                        "raw_status": "not-run",
                        "score": 0,
                        "max_score": 0,
                        "lost": 0,
                        "note": source,
                    }
                )
                continue
            for point in points:
                score = float(point.get("score", 0) or 0)
                max_score = float(point.get("max_score", 0) or 0)
                rows.append(
                    {
                        "group": group.get("group", ""),
                        "suite": suite.get("suite", ""),
                        "variant": suite.get("variant", ""),
                        "name": point.get("name", ""),
                        "status": STATUS_LABEL.get(
                            point.get("status", ""), point.get("status", "")
                        ),
                        "raw_status": point.get("status", ""),
                        "score": score,
                        "max_score": max_score,
                        "lost": round(max_score - score, 6),
                        "note": point.get("note", ""),
                    }
                )
    return rows


def _group_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["group"]
        if key not in acc:
            acc[key] = {
                "group": key,
                "score": 0.0,
                "max_score": 0.0,
                "full": 0,
                "partial": 0,
                "fail": 0,
                "skip": 0,
                "not_run": 0,
            }
            order.append(key)
        item = acc[key]
        item["score"] += float(row["score"] or 0)
        item["max_score"] += float(row["max_score"] or 0)
        status = row.get("raw_status") or row["status"]
        if status in ("full", "partial", "fail", "skip"):
            item[status] += 1
        else:
            item["not_run"] += 1
    return [acc[key] for key in order]


def render_markdown(report: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    arch = report.get("arch", "?")
    label = report.get("label", "")
    started = report.get("started_at", "")
    meta = report.get("summary", {}).get("counts", {})
    total_score = sum(float(r["score"] or 0) for r in rows)
    total_max = sum(float(r["max_score"] or 0) for r in rows)

    out: list[str] = []
    out.append(f"# 全部测试点得分 - {arch} ({label})")
    out.append("")
    out.append("| field | value |")
    out.append("|---|---|")
    out.append(f"| arch | {arch} |")
    out.append(f"| label | {label} |")
    out.append(f"| started_at | {started} |")
    out.append(f"| test points | {len(rows)} |")
    out.append(
        f"| full / partial / fail / skip | "
        f"{meta.get('points_full', '-')} / {meta.get('points_partial', '-')} / "
        f"{meta.get('points_failed', '-')} / {meta.get('points_skipped', '-')} |"
    )
    out.append(
        f"| summed score | **{_num(total_score)} / {_num(total_max)}** "
        f"({(total_score / total_max * 100 if total_max else 0):.1f}%) |"
    )
    out.append("")

    out.append("## 测试点明细")
    out.append("")
    out.append("| # | group | test point | status | score | max | lost | note |")
    out.append("|---:|---|---|---|---:|---:|---:|---|")
    for index, row in enumerate(rows, 1):
        out.append(
            f"| {index} | {_cell(row['group'])} | {_cell(row['name'])} | "
            f"{_cell(row['status'])} | {_num(row['score'])} | "
            f"{_num(row['max_score'])} | {_num(row['lost'])} | "
            f"{_cell(row['note'])} |"
        )
    out.append("")

    out.append("## 按测试组小计")
    out.append("")
    out.append("| group | score / max | % | full | partial | fail | skip | not-run |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for item in _group_totals(rows):
        pct = (
            f"{item['score'] / item['max_score'] * 100:.1f}%"
            if item["max_score"]
            else "-"
        )
        out.append(
            f"| {_cell(item['group'])} | {_num(item['score'])} / "
            f"{_num(item['max_score'])} | {pct} | {item['full']} | "
            f"{item['partial']} | {item['fail']} | {item['skip']} | "
            f"{item['not_run']} |"
        )
    out.append("")
    return "\n".join(out)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "group",
                "suite",
                "variant",
                "name",
                "status",
                "raw_status",
                "score",
                "max_score",
                "lost",
                "note",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="+", help="report.json file(s)")
    parser.add_argument(
        "--only",
        choices=("all", "lost", "failed"),
        default="all",
        help="all points (default), only points with lost>0, or only failures",
    )
    parser.add_argument(
        "--suffix",
        default="points",
        help="output basename: <run dir>/<suffix>.md and .csv (default: points)",
    )
    parser.add_argument("--out-dir", help="write everything here instead")
    args = parser.parse_args(argv)

    written = 0
    for raw in args.report:
        path = Path(raw).resolve()
        report = json.loads(path.read_text(encoding="utf-8"))
        rows = collect(report)
        if args.only == "lost":
            rows = [r for r in rows if float(r["lost"] or 0) > 1e-9]
        elif args.only == "failed":
            rows = [
                r
                for r in rows
                if (r.get("raw_status") or r["status"])
                in ("fail", "partial", "not-run")
            ]

        target_dir = Path(args.out_dir) if args.out_dir else path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        md_path = target_dir / f"{args.suffix}.md"
        csv_path = target_dir / f"{args.suffix}.csv"
        md_path.write_text(render_markdown(report, rows), encoding="utf-8")
        write_csv(csv_path, rows)
        print(
            f"{path.parent.name}: {len(rows)} point(s) -> "
            f"{md_path} , {csv_path}"
        )
        written += 1
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
