"""Run directories, the run index and the performance baseline file."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import ArchResult
from .references import COMPOSITE_SUITES, OFFICIAL_SUITES, SUITE_SPECS


INDEX_FILE = "index.json"
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
SERIAL_LOG = "serial.log"
META_JSON = "meta.json"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class RunStore:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- directories ------------------------------------------------------

    def new_run_dir(self, arch: str, tag: str | None = None) -> Path:
        base = f"{timestamp()}-{arch}"
        if tag:
            base += f"-{tag}"
        path = self.runs_dir / base
        suffix = 2
        while path.exists():
            path = self.runs_dir / f"{base}-{suffix}"
            suffix += 1
        path.mkdir(parents=True)
        return path

    def copy_log(self, src: Path, run_dir: Path) -> Path:
        dst = run_dir / SERIAL_LOG
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        return dst

    # -- index ------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.runs_dir / INDEX_FILE

    def load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            data = data.get("runs", [])
        return data if isinstance(data, list) else []

    def append_index(self, entry: dict[str, Any]) -> None:
        entries = self.load_index()
        entries.append(entry)
        self.index_path.write_text(
            json.dumps({"version": 1, "runs": entries}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    def latest(self, arch: str | None = None) -> dict[str, Any] | None:
        entries = self.load_index()
        if arch:
            entries = [e for e in entries if e.get("arch") == arch]
        return entries[-1] if entries else None

    def previous(
        self, arch: str, current_run_dir: Path, *, complete_only: bool = True
    ) -> dict[str, Any] | None:
        entries = [
            e
            for e in self.load_index()
            if e.get("arch") == arch
            and Path(e.get("run_dir", "")).resolve() != current_run_dir.resolve()
        ]
        if complete_only:
            entries = [
                e
                for e in entries
                if not e.get("timed_out")
                and e.get("groups", 0) > 0
                and e.get("groups_complete", 0) == e.get("groups", 0)
            ]
        return entries[-1] if entries else None


# ---------------------------------------------------------------------------
# Summary helpers (also used by the index and the report)
# ---------------------------------------------------------------------------


def group_counted(group) -> tuple[float, float]:
    """Score/max used for totals.

    A group without an END marker is not captured by the online harness.  Its
    partial score is shown in the report but counts as zero; the denominator
    uses the suite's expected maximum so the loss is visible.
    """

    if group.complete:
        return group.counted_score, group.max_score
    spec = SUITE_SPECS.get(group.suite)
    default = float(spec.default_max) if spec and spec.default_max else 0.0
    return 0.0, max(default, group.max_score)


def official_totals(
    result: ArchResult, *, alternate: bool = False
) -> tuple[float, float]:
    score = 0.0
    max_score = 0.0
    for suite in result.suites:
        if suite.suite not in OFFICIAL_SUITES:
            continue
        for group in suite.groups:
            if alternate and group.alternate_score is not None:
                if group.complete:
                    score += group.alternate_score
                    max_score += group.alternate_max or group.max_score
                else:
                    spec = SUITE_SPECS.get(group.suite)
                    default = (
                        float(spec.default_max) if spec and spec.default_max else 0.0
                    )
                    max_score += max(default, group.alternate_max or 0.0)
            else:
                group_score, group_max = group_counted(group)
                score += group_score
                max_score += group_max
    return score, max_score


def composite_totals(result: ArchResult) -> tuple[float, float]:
    score = 0.0
    max_score = 0.0
    for suite in result.suites:
        if suite.suite not in COMPOSITE_SUITES:
            continue
        for group in suite.groups:
            group_score, group_max = group_counted(group)
            score += group_score
            max_score += group_max
    return score, max_score


def summary_entry(result: ArchResult, run_dir: Path) -> dict[str, Any]:
    official_score, official_max = official_totals(result)
    composite_score, composite_max = composite_totals(result)
    suites = []
    for suite in result.suites:
        suites.append(
            {
                "suite": suite.suite,
                "variant": suite.variant,
                "ran": suite.ran,
                "complete": suite.complete,
                "score": round(suite.score, 6),
                "max_score": round(suite.max_score, 6),
                "counted_score": round(
                    sum(g.counted_score for g in suite.groups), 6
                ),
                "points_full": suite.points_full,
                "points_partial": suite.points_partial,
                "points_failed": suite.points_failed,
            }
        )
    return {
        "run_dir": str(run_dir),
        "arch": result.arch,
        "label": result.label,
        "started_at": result.started_at,
        "wall_time_sec": round(result.wall_time_sec, 3),
        "timed_out": result.timed_out,
        "score_mode": result.score_mode,
        "groups": len(result.groups),
        "groups_complete": len([g for g in result.groups if g.complete]),
        "groups_incomplete": len([g for g in result.groups if not g.complete]),
        "official_score": round(official_score, 6),
        "official_max": round(official_max, 6),
        "composite_score": round(composite_score, 6),
        "composite_max": round(composite_max, 6),
        "suites": suites,
    }


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def load_baseline(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        data = data["metrics"]
    if not isinstance(data, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in data.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def metrics_from_results(results: Iterable[ArchResult]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for result in results:
        for suite in result.suites:
            for group in suite.groups:
                for metric in group.metrics:
                    metrics[f"{result.arch}/{group.variant}/{metric.name}"] = (
                        metric.value
                    )
    return metrics


def metrics_from_report(report: dict[str, Any]) -> dict[str, float]:
    """Extract baseline metrics from a saved ``report.json``."""

    metrics: dict[str, float] = {}
    arch = report.get("arch", "")
    for suite in report.get("suites", []):
        for group in suite.get("groups", []):
            variant = group.get("variant", "")
            for metric in group.get("metrics", []):
                name = metric.get("name")
                value = metric.get("value")
                if name is None or value is None:
                    continue
                metrics[f"{arch}/{variant}/{name}"] = float(value)
    return metrics


def save_baseline(path: Path, metrics: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": {key: metrics[key] for key in sorted(metrics)},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
