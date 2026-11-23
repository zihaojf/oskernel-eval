"""Data model shared by the QEMU runner, the serial parser and the reporters.

The model deliberately keeps "what the OS printed" (``GroupResult.body``)
separate from "how it was scored" (``TestPoint`` / ``Metric``).  This makes it
possible to re-score an old serial log without booting QEMU again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Test points
# ---------------------------------------------------------------------------

STATUS_FULL = "full"
STATUS_PARTIAL = "partial"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"

KIND_FUNCTIONAL = "functional"
KIND_PERFORMANCE = "performance"
KIND_MIXED = "mixed"


@dataclass
class TestPoint:
    """One scoring unit of a test group.

    For functional suites (basic, busybox, lua, libctest, ltp) a point is one
    test case and ``passed``/``total`` describe how many assertions or cases
    passed.  For performance suites a point is one benchmark metric and
    ``score``/``max_score`` are a local quality value.
    """

    name: str
    score: float = 0.0
    max_score: float = 1.0
    passed: int | None = None
    total: int | None = None
    status: str = STATUS_FAIL
    kind: str = KIND_FUNCTIONAL
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def lost(self) -> float:
        return max(0.0, self.max_score - self.score)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "score": round(self.score, 6),
            "max_score": round(self.max_score, 6),
            "status": self.status,
            "kind": self.kind,
        }
        if self.passed is not None:
            data["passed"] = self.passed
        if self.total is not None:
            data["total"] = self.total
        if self.note:
            data["note"] = self.note
        if self.extra:
            data["extra"] = self.extra
        return data


@dataclass
class Metric:
    """A measured performance value (one row in the performance table).

    ``quality`` is always in ``[0, 1]`` and is *not* an official score; it is a
    normalized distance to a documented reference range or to a saved
    baseline.  ``delta_pct`` is the relative change against the previous run.
    """

    name: str
    value: float
    unit: str = ""
    direction: str = "higher"  # "higher" is better, or "lower" is better
    reference: float | None = None
    reference_kind: str = ""  # "doc-range" | "baseline" | "official"
    range_low: float | None = None
    range_high: float | None = None
    quality: float | None = None
    delta_pct: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "direction": self.direction,
        }
        for key in (
            "reference",
            "reference_kind",
            "range_low",
            "range_high",
            "quality",
            "delta_pct",
            "note",
        ):
            value = getattr(self, key)
            if value is not None and value != "":
                data[key] = value
        return data


# ---------------------------------------------------------------------------
# Groups and suites
# ---------------------------------------------------------------------------


@dataclass
class GroupResult:
    """A ``#### OS COMP TEST GROUP START ... ####`` section."""

    group: str
    suite: str
    variant: str
    complete: bool
    body: str
    points: list[TestPoint] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = "local"  # "official-raw" / "official-intended" / "local"
    start_line: int = 0
    end_line: int = 0
    duration_sec: float | None = None

    # Optional alternative score.  Only ``basic`` currently differs between the
    # raw judge (which keeps the trailing newline) and the intended score.
    alternate_score: float | None = None
    alternate_max: float | None = None
    alternate_label: str = ""

    @property
    def score(self) -> float:
        return sum(p.score for p in self.points)

    @property
    def max_score(self) -> float:
        return sum(p.max_score for p in self.points)

    @property
    def counted_score(self) -> float:
        """Score that would be submitted to the online judge.

        An incomplete group has no ``END`` marker, so the competition harness
        cannot capture it.  The partial score is still kept in ``score`` for
        local diagnostics.
        """

        return self.score if self.complete else 0.0

    @property
    def counted_max(self) -> float:
        return self.max_score if self.complete else 0.0

    @property
    def points_total(self) -> int:
        return len([p for p in self.points if p.status != STATUS_SKIP])

    @property
    def points_full(self) -> int:
        return len([p for p in self.points if p.status == STATUS_FULL])

    @property
    def points_partial(self) -> int:
        return len([p for p in self.points if p.status == STATUS_PARTIAL])

    @property
    def points_failed(self) -> int:
        return len([p for p in self.points if p.status == STATUS_FAIL])

    @property
    def points_skipped(self) -> int:
        return len([p for p in self.points if p.status == STATUS_SKIP])

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "group": self.group,
            "suite": self.suite,
            "variant": self.variant,
            "complete": self.complete,
            "score": round(self.score, 6),
            "max_score": round(self.max_score, 6),
            "points": {
                "total": self.points_total,
                "full": self.points_full,
                "partial": self.points_partial,
                "fail": self.points_failed,
                "skip": self.points_skipped,
            },
            "source": self.source,
            "warnings": self.warnings,
            "test_points": [p.to_dict() for p in self.points],
            "metrics": [m.to_dict() for m in self.metrics],
        }
        if self.duration_sec is not None:
            data["duration_sec"] = round(self.duration_sec, 3)
        if self.alternate_score is not None:
            data["alternate"] = {
                "label": self.alternate_label,
                "score": round(self.alternate_score, 6),
                "max_score": round(self.alternate_max or 0.0, 6),
            }
        return data


@dataclass
class SuiteResult:
    """All groups of one suite for one libc variant (e.g. ``basic/glibc``)."""

    suite: str
    variant: str
    groups: list[GroupResult] = field(default_factory=list)
    ran: bool = False

    @property
    def score(self) -> float:
        return sum(g.score for g in self.groups)

    @property
    def max_score(self) -> float:
        return sum(g.max_score for g in self.groups)

    @property
    def metrics(self) -> list[Metric]:
        return [m for g in self.groups for m in g.metrics]

    @property
    def _scored_groups(self) -> list[GroupResult]:
        """Groups that actually produce test points.

        Synthetic "not run" groups only carry the expected maximum for the
        denominator; they must not inflate the full/partial/fail counts.
        """

        return [g for g in self.groups if g.source != "not-run"]

    @property
    def points_total(self) -> int:
        return sum(g.points_total for g in self._scored_groups)

    @property
    def points_full(self) -> int:
        return sum(g.points_full for g in self._scored_groups)

    @property
    def points_partial(self) -> int:
        return sum(g.points_partial for g in self._scored_groups)

    @property
    def points_failed(self) -> int:
        return sum(g.points_failed for g in self._scored_groups)

    @property
    def points_skipped(self) -> int:
        return sum(g.points_skipped for g in self._scored_groups)

    @property
    def warnings(self) -> list[str]:
        return [w for g in self.groups for w in g.warnings]

    @property
    def complete(self) -> bool:
        return bool(self.groups) and all(g.complete for g in self.groups)

    @property
    def duration_sec(self) -> float | None:
        values = [g.duration_sec for g in self.groups if g.duration_sec is not None]
        return sum(values) if values else None

    def first_point(self, name: str) -> TestPoint | None:
        for group in self.groups:
            for point in group.points:
                if point.name == name:
                    return point
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "variant": self.variant,
            "ran": self.ran,
            "complete": self.complete,
            "score": round(self.score, 6),
            "max_score": round(self.max_score, 6),
            "points": {
                "total": self.points_total,
                "full": self.points_full,
                "partial": self.points_partial,
                "fail": self.points_failed,
                "skip": self.points_skipped,
            },
            "duration_sec": (
                round(self.duration_sec, 3) if self.duration_sec is not None else None
            ),
            "groups": [g.to_dict() for g in self.groups],
        }


@dataclass
class ArchResult:
    """Everything measured for one architecture in one run."""

    arch: str
    label: str
    kernel: str = ""
    image: str = ""
    command: list[str] = field(default_factory=list)
    started_at: str = ""
    wall_time_sec: float = 0.0
    timed_out: bool = False
    exit_code: int | None = None
    groups: list[GroupResult] = field(default_factory=list)
    suites: list[SuiteResult] = field(default_factory=list)
    raw_log: str = ""
    score_mode: str = "both"
    version: str = "1"
    warnings: list[str] = field(default_factory=list)
    scripts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete_groups(self) -> list[GroupResult]:
        return [g for g in self.groups if g.complete]

    @property
    def incomplete_groups(self) -> list[GroupResult]:
        return [g for g in self.groups if not g.complete]

    def suite(self, name: str, variant: str) -> SuiteResult | None:
        for item in self.suites:
            if item.suite == name and item.variant == variant:
                return item
        return None

    def ran_suites(self) -> set[str]:
        return {s.suite for s in self.suites if s.ran}

    @property
    def variants(self) -> list[str]:
        seen: list[str] = []
        for suite in self.suites:
            if suite.variant not in seen:
                seen.append(suite.variant)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "arch": self.arch,
            "label": self.label,
            "kernel": self.kernel,
            "image": self.image,
            "started_at": self.started_at,
            "wall_time_sec": round(self.wall_time_sec, 3),
            "timed_out": self.timed_out,
            "exit_code": self.exit_code,
            "score_mode": self.score_mode,
            "warnings": self.warnings,
            "scripts": self.scripts,
            "suites": [s.to_dict() for s in self.suites],
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def range_quality(value: float, low: float, high: float, direction: str) -> float:
    """Normalize ``value`` against a documented expected range.

    ``low``/``high`` are the "best" and "worst" ends of the range as printed in
    the competition document.  Values better than the best end get 1.0,
    values worse than the worst end get 0.0.
    """

    if high == low:
        return 1.0 if value == low else 0.0
    if direction == "lower":
        return clamp((high - value) / (high - low))
    return clamp((value - low) / (high - low))


def ratio_quality(value: float, reference: float, direction: str) -> float:
    """Quality of ``value`` relative to a numeric baseline (1.0 == baseline)."""

    if value <= 0 or reference <= 0:
        return 0.0
    return value / reference if direction == "higher" else reference / value


def point_status(score: float, max_score: float, full_at: float = 1.0) -> str:
    if max_score <= 0:
        return STATUS_SKIP
    if score <= 1e-9:
        return STATUS_FAIL
    if score + 1e-9 >= max_score * full_at:
        return STATUS_FULL
    return STATUS_PARTIAL


def merge_by(items: Iterable[Any], key) -> dict[Any, list[Any]]:
    result: dict[Any, list[Any]] = {}
    for item in items:
        result.setdefault(key(item), []).append(item)
    return result
