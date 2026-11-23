"""Run the competition's own ``judge_*.py`` scripts.

The provided judge scripts are the best available definition of the official
score, so they are executed verbatim in ``raw`` mode.  One of them
(``judge_basic.py``) compares output lines with a trailing newline still
attached, which caps the achievable score; that is a judge quirk, not an OS
behaviour.  For ``basic`` we therefore also compute an ``intended`` score by
re-running the same judge classes on newline-stripped lines.  The report shows
both, so it is always clear whether a point was lost by the OS or by the judge.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import (
    KIND_FUNCTIONAL,
    KIND_PERFORMANCE,
    STATUS_FAIL,
    STATUS_FULL,
    STATUS_PARTIAL,
    Metric,
    TestPoint,
)
from ..references import OFFICIAL_SUITES


class JudgeError(RuntimeError):
    pass


@dataclass
class OfficialScore:
    points: list[TestPoint] = field(default_factory=list)
    alternate_points: list[TestPoint] | None = None
    metrics: list[Metric] = field(default_factory=list)
    source: str = "official"
    warnings: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(p.score for p in self.points)

    @property
    def max_score(self) -> float:
        return sum(p.max_score for p in self.points)

    @property
    def alternate_score(self) -> float | None:
        if self.alternate_points is None:
            return None
        return sum(p.score for p in self.alternate_points)

    @property
    def alternate_max(self) -> float | None:
        if self.alternate_points is None:
            return None
        return sum(p.max_score for p in self.alternate_points)


# ---------------------------------------------------------------------------
# Locating and running a judge
# ---------------------------------------------------------------------------


def judge_path(judge_dir: Path, suite: str) -> Path | None:
    path = judge_dir / f"judge_{suite}.py"
    return path if path.is_file() else None


def official_available(judge_dir: Path, suite: str) -> bool:
    return suite in OFFICIAL_SUITES and judge_path(judge_dir, suite) is not None


def _parse_json_output(stdout: str) -> list[dict[str, Any]]:
    """Extract the JSON array printed by a judge script."""

    text = stdout.strip()
    if not text:
        raise JudgeError("judge produced no output")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Tolerate a judge that prints a diagnostic line before the JSON.
    for start in range(len(text)):
        if text[start] not in "[{":
            continue
        try:
            data = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    raise JudgeError("judge output is not a JSON array")


def _run_raw_judge(path: Path, body: str) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=body,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"{path.name} timed out") from exc
    if proc.returncode != 0:
        raise JudgeError(
            f"{path.name} exited with {proc.returncode}: "
            f"{proc.stderr.strip()[:300]}"
        )
    return _parse_json_output(proc.stdout)


# ---------------------------------------------------------------------------
# JSON -> TestPoint
# ---------------------------------------------------------------------------


def _status_from_counts(passed: int | None, total: int | None) -> str:
    if total is None or total <= 0:
        return STATUS_FULL if (passed or 0) > 0 else STATUS_FAIL
    if passed is None or passed <= 0:
        return STATUS_FAIL
    if passed >= total:
        return STATUS_FULL
    return STATUS_PARTIAL


def _points_from_items(suite: str, items: list[dict[str, Any]]) -> list[TestPoint]:
    points: list[TestPoint] = []
    for item in items:
        name = str(item.get("name", "?"))
        score = float(item.get("score", 0.0))

        if suite == "iozone":
            # judge_iozone.py: one point per throughput metric, score in
            # [0, 2] where 1.0 means "matches the reference baseline".
            max_score = 2.0
            status = (
                STATUS_FULL
                if score >= 1.0
                else STATUS_PARTIAL
                if score > 0
                else STATUS_FAIL
            )
            point = TestPoint(
                name=name,
                score=score,
                max_score=max_score,
                status=status,
                kind=KIND_PERFORMANCE,
                note=f"result={item.get('result')} baseline={item.get('baseline')}",
                extra={
                    "result": item.get("result"),
                    "baseline": item.get("baseline"),
                },
            )
            points.append(point)
            continue

        total = item.get("all", item.get("total"))
        passed = item.get("pass")
        if total is None and suite in ("busybox", "lua"):
            total = 1
        if total is None and suite == "libctest":
            total = 1
        total_int = int(total) if total is not None else None
        passed_int = int(passed) if passed is not None else None

        if total_int is not None:
            max_score = float(total_int)
            status = _status_from_counts(passed_int, total_int)
        else:
            # Should not happen for the current judges; fall back to score.
            max_score = max(1.0, score)
            status = STATUS_FULL if score >= max_score else STATUS_FAIL

        note = ""
        if passed_int is not None and total_int is not None:
            note = f"{passed_int}/{total_int}"
        points.append(
            TestPoint(
                name=name,
                score=score,
                max_score=max_score,
                passed=passed_int,
                total=total_int,
                status=status,
                kind=KIND_FUNCTIONAL,
                note=note,
            )
        )
    return points


# ---------------------------------------------------------------------------
# Intended score for judge_basic.py
# ---------------------------------------------------------------------------

_module_cache: dict[str, Any] = {}


def _load_basic_module(path: Path):
    key = str(path)
    if key not in _module_cache:
        spec = importlib.util.spec_from_file_location("starry_eval_judge_basic", path)
        if spec is None or spec.loader is None:
            raise JudgeError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        # The judge modules print a diagnostic dict_keys(...) line on import.
        # Keep the scorer's stdout clean.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            spec.loader.exec_module(module)
        _module_cache[key] = module
    return _module_cache[key]


def _score_basic_intended(path: Path, body: str) -> list[TestPoint]:
    """Drive the judge classes directly with stripped output lines.

    The official script keys its runner dictionary by *class* name, so
    ``test_dup2`` never matches ``dup2_test``; keying by ``instance.name`` fixes
    that as well.
    """

    module = _load_basic_module(path)
    instances = [cls() for cls in module.TestBase.__subclasses__()]
    by_name = {instance.name: instance for instance in instances}

    start_re = re.compile(r"^========== START (.+) ==========$")
    state = 0
    name = ""
    data: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\n")
        if state == 0:
            match = start_re.match(line)
            if match:
                name = match.group(1)
                data = []
                state = 1
            continue

        if line.startswith("========== END "):
            instance = by_name.get(name)
            if instance is not None:
                instance.start(data)
            state = 0
            data = []
        elif start_re.match(line):
            # An aborted test point: discard its partial output, exactly like
            # the official script.
            data = []
            name = start_re.match(line).group(1)  # type: ignore[union-attr]
        else:
            data.append(line)

    results = [instance.get_result() for instance in instances]
    return _points_from_items("basic", results)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def score_official(
    suite: str,
    body: str,
    judge_dir: Path,
    *,
    mode: str = "both",
) -> OfficialScore:
    """Score one group with the provided judge.

    ``mode``:
      * ``raw``      - primary score is the verbatim judge output
      * ``intended`` - primary score is newline-normalized (basic only)
      * ``both``     - same as intended, but the raw score is reported as the
                       alternate value
    """

    path = judge_path(judge_dir, suite)
    if path is None:
        raise JudgeError(f"no official judge for suite {suite!r} in {judge_dir}")

    raw_items = _run_raw_judge(path, body)
    raw_points = _points_from_items(suite, raw_items)

    intended_points: list[TestPoint] | None = None
    warnings: list[str] = []
    if suite == "basic":
        try:
            intended_points = _score_basic_intended(path, body)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"intended basic scoring failed: {exc}")

    if mode == "raw" or intended_points is None:
        points = raw_points
        alternate = intended_points
        source = "official-raw"
        if intended_points is not None:
            warnings.append(
                "judge_basic.py keeps the trailing newline in exact-match "
                "assertions; the intended score is reported as alternate"
            )
    else:
        points = intended_points
        alternate = raw_points
        source = "official-intended"
        warnings.append(
            "primary score is newline-normalized; verbatim judge score is the "
            "alternate value"
        )

    metrics: list[Metric] = []
    if suite == "iozone":
        for point in points:
            result_value = point.extra.get("result")
            baseline_value = point.extra.get("baseline")
            if result_value is None:
                continue
            metrics.append(
                Metric(
                    name=point.name,
                    value=float(result_value),
                    unit="kB/sec",
                    direction="higher",
                    reference=(
                        float(baseline_value)
                        if baseline_value is not None
                        else None
                    ),
                    reference_kind="official",
                    quality=min(1.0, max(0.0, point.score / 2.0)),
                )
            )

    return OfficialScore(
        points=points,
        alternate_points=alternate,
        metrics=metrics,
        source=source,
        warnings=warnings,
    )
