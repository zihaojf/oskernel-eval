"""LTP parser.

The shipped ``ltp_testcode.sh`` prints ``RUN LTP CASE <name>`` before every
binary and ``FAIL LTP CASE <name> : <ret>`` after it.  The second line is
printed unconditionally, so its presence is not a failure signal.  The actual
status is taken from the binary's own ``PASS`` / ``FAIL`` / ``TCONF`` output and
falls back to the exit code.
"""

from __future__ import annotations

import re

from ..model import GroupResult, TestPoint, STATUS_FAIL, STATUS_FULL, STATUS_SKIP

RUN_RE = re.compile(r"^RUN LTP CASE (.+?)\s*$")
DONE_RE = re.compile(r"^FAIL LTP CASE (.+?)\s*:\s*(-?\d+)\s*$")
STATUS_RE = re.compile(r"\b(PASS|FAIL|TCONF|BROK)\b")


def _classify(text: str, ret: int) -> str:
    """Return ``pass``, ``fail`` or ``skip`` for one LTP case."""

    statuses = STATUS_RE.findall(text)
    if "PASS" in statuses:
        return "pass"
    if "FAIL" in statuses or "BROK" in statuses:
        return "fail"
    if "TCONF" in statuses:
        return "skip"
    return "pass" if ret == 0 else "fail"


def score_ltp(group: GroupResult, baseline: dict[str, float]) -> None:
    points: list[TestPoint] = []
    current_name: str | None = None
    buffer: list[str] = []
    matches = 0

    def finish(name: str, text: str, ret: int) -> None:
        nonlocal matches
        matches += 1
        outcome = _classify(text, ret)
        if outcome == "skip":
            point = TestPoint(
                name=name,
                score=0.0,
                max_score=0.0,
                status=STATUS_SKIP,
                kind="functional",
                note="TCONF（不计分）",
            )
        else:
            ok = outcome == "pass"
            point = TestPoint(
                name=name,
                score=1.0 if ok else 0.0,
                max_score=1.0,
                status=STATUS_FULL if ok else STATUS_FAIL,
                kind="functional",
                note=f"退出码={ret}",
            )
        points.append(point)

    for line in group.body.splitlines():
        run = RUN_RE.match(line.strip())
        if run:
            if current_name is not None:
                # Two RUN lines without a DONE line: treat the previous case as
                # interrupted rather than silently dropping it.
                finish(current_name, "\n".join(buffer), -1)
            current_name = run.group(1).strip()
            buffer = []
            continue

        done = DONE_RE.match(line.strip())
        if done:
            if current_name == done.group(1).strip():
                finish(current_name, "\n".join(buffer), int(done.group(2)))
                current_name = None
                buffer = []
            continue

        if current_name is not None:
            buffer.append(line)

    if current_name is not None:
        finish(current_name, "\n".join(buffer), -1)

    if matches == 0:
        group.warnings.append("ltp：没有找到 'RUN LTP CASE' 行")

    group.points.extend(points)
